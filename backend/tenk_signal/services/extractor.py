"""Extraction service: filing text → validated structured record.

Architecture
------------
Two implementations behind a Protocol:

- ``LiveAnthropicExtractor`` calls the Anthropic API using ``output_config.
  format`` (GA on Sonnet 4.6, no beta header required as of June 2026). The
  JSON schema fed to the API is derived from the Pydantic model so the wire
  contract and the validator cannot drift. Retry with exponential backoff.
- ``FixtureExtractor`` reads canned JSON from ``tests/fixtures/anthropic/``
  keyed by ``text_sha256``. Used by tests and by the seed script so the
  whole pipeline can run without spending Anthropic credits.

Cache contract
--------------
Persistence uses ``INSERT … ON CONFLICT DO NOTHING RETURNING id`` against
the ``UNIQUE(text_sha256, prompt_version, model)`` constraint. Repeated
calls with the same (text, prompt_version, model) return the cached row
without a second API call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tenk_signal.config import Settings
from tenk_signal.logging import get_logger
from tenk_signal.models import Extraction as ExtractionRow
from tenk_signal.models import Filing
from tenk_signal.schemas import (
    Extraction,
    extraction_json_schema,
)
from tenk_signal.services.prompt import (
    SYSTEM_PROMPT,
    build_user_message,
    contains_instruction_patterns,
)

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ExtractorResult:
    extraction: Extraction
    cached: bool
    quarantined: bool


class Extractor(Protocol):
    async def extract(
        self,
        session: AsyncSession,
        filing: Filing,
    ) -> ExtractorResult: ...


# ---------------------------------------------------------------------------
# Shared cache lookup / insert (used by both impls)
# ---------------------------------------------------------------------------


async def _cache_lookup(
    session: AsyncSession,
    *,
    text_sha256: str,
    prompt_version: str,
    model: str,
) -> ExtractionRow | None:
    q = select(ExtractionRow).where(
        ExtractionRow.text_sha256 == text_sha256,
        ExtractionRow.prompt_version == prompt_version,
        ExtractionRow.model == model,
    )
    return (await session.execute(q)).scalar_one_or_none()


async def _cache_insert(
    session: AsyncSession,
    *,
    filing_id: int,
    text_sha256: str,
    extraction: Extraction,
    model: str,
    prompt_version: str,
) -> ExtractionRow:
    """INSERT ... ON CONFLICT DO NOTHING. Returns the row either way."""
    stmt = (
        pg_insert(ExtractionRow)
        .values(
            filing_id=filing_id,
            text_sha256=text_sha256,
            sentiment=extraction.sentiment,
            guidance=extraction.guidance,
            risk_flag_count=extraction.risk_flag_count,
            confidence=extraction.confidence,
            rationale=extraction.rationale,
            model=model,
            prompt_version=prompt_version,
        )
        .on_conflict_do_nothing(constraint="uq_extraction_cache")
    )
    await session.execute(stmt)
    await session.flush()
    row = await _cache_lookup(
        session,
        text_sha256=text_sha256,
        prompt_version=prompt_version,
        model=model,
    )
    assert row is not None, "row vanished after INSERT ON CONFLICT — DB bug"
    return row


def _maybe_quarantine(filing: Filing, text: str) -> bool:
    hits = contains_instruction_patterns(text)
    if hits:
        log.warning(
            "extractor.injection_patterns",
            filing_id=filing.id,
            ticker=filing.ticker,
            patterns=hits,
        )
        filing.quarantined = True
        return True
    return False


# ---------------------------------------------------------------------------
# Live (Anthropic, structured outputs)
# ---------------------------------------------------------------------------


class LiveAnthropicExtractor:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # Lazy import + log the SDK version. If a future SDK reintroduces a
        # beta header requirement, this log is where we'll notice.
        import anthropic

        try:
            ver = version("anthropic")
        except PackageNotFoundError:
            ver = "unknown"
        log.info("extractor.sdk_version", anthropic=ver)
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    )
    async def _call(self, system: str, user: str) -> dict[str, Any]:
        resp = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=self._settings.extraction_max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            # GA structured outputs. No anthropic-beta header required as of
            # June 2026 (docs URL in PLAN.md §5). The SDK supports
            # ``output_config`` as a native parameter from 0.105+.
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": extraction_json_schema(),
                }
            },
        )
        # The first content block is the JSON text under constrained decode.
        block = resp.content[0]
        text = getattr(block, "text", None) or ""
        if not text:
            raise RuntimeError("empty response from Anthropic")
        return json.loads(text)  # type: ignore[no-any-return]

    async def extract(
        self,
        session: AsyncSession,
        filing: Filing,
    ) -> ExtractorResult:
        cached = await _cache_lookup(
            session,
            text_sha256=filing.text_sha256,
            prompt_version=self._settings.prompt_version,
            model=self._settings.anthropic_model,
        )
        if cached is not None:
            log.info("extractor.cache_hit", filing_id=filing.id)
            return ExtractorResult(
                extraction=Extraction(
                    sentiment=cached.sentiment,
                    guidance=cached.guidance.value,
                    risk_flag_count=cached.risk_flag_count,
                    confidence=cached.confidence,
                    rationale=cached.rationale,
                ),
                cached=True,
                quarantined=filing.quarantined,
            )

        quarantined = _maybe_quarantine(filing, filing.text)
        raw = await self._call(SYSTEM_PROMPT, build_user_message(filing.text))

        # Belt-and-suspenders Pydantic validation. With structured outputs
        # this should be unreachable; if it ever fires, we want loud failure.
        extraction = Extraction.model_validate(raw)

        await _cache_insert(
            session,
            filing_id=filing.id,
            text_sha256=filing.text_sha256,
            extraction=extraction,
            model=self._settings.anthropic_model,
            prompt_version=self._settings.prompt_version,
        )
        return ExtractorResult(extraction=extraction, cached=False, quarantined=quarantined)


# ---------------------------------------------------------------------------
# Fixture extractor — for tests + seed script
# ---------------------------------------------------------------------------


class FixtureExtractor:
    """Looks up tests/fixtures/anthropic/<text_sha256>.json. Falls back to a
    deterministic neutral record so tests can use any filing fixture."""

    def __init__(self, settings: Settings, fixtures_dir: Path) -> None:
        self._settings = settings
        self._dir = fixtures_dir

    async def extract(
        self,
        session: AsyncSession,
        filing: Filing,
    ) -> ExtractorResult:
        cached = await _cache_lookup(
            session,
            text_sha256=filing.text_sha256,
            prompt_version=self._settings.prompt_version,
            model=self._settings.anthropic_model,
        )
        if cached is not None:
            return ExtractorResult(
                extraction=Extraction(
                    sentiment=cached.sentiment,
                    guidance=cached.guidance.value,
                    risk_flag_count=cached.risk_flag_count,
                    confidence=cached.confidence,
                    rationale=cached.rationale,
                ),
                cached=True,
                quarantined=filing.quarantined,
            )

        quarantined = _maybe_quarantine(filing, filing.text)
        path = self._dir / f"{filing.text_sha256}.json"
        if path.exists():
            raw = json.loads(path.read_text())
        else:
            log.warning("extractor.fixture_missing", sha=filing.text_sha256)
            raw = {
                "sentiment": 0.0,
                "guidance": "maintained",
                "risk_flag_count": 0,
                "confidence": 0.2,
                "rationale": "No fixture present; returning neutral default.",
            }

        extraction = Extraction.model_validate(raw)
        await _cache_insert(
            session,
            filing_id=filing.id,
            text_sha256=filing.text_sha256,
            extraction=extraction,
            model=self._settings.anthropic_model,
            prompt_version=self._settings.prompt_version,
        )
        return ExtractorResult(extraction=extraction, cached=False, quarantined=quarantined)
