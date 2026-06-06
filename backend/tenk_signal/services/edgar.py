"""SEC EDGAR client.

Two responsibilities:
1. Fetch recent 10-K / 10-Q filings for a ticker via the submissions JSON API.
2. Extract the MD&A (Item 7) and Risk Factors (Item 1A) sections from the
   primary document HTML — best effort, with a bounded whole-document
   fallback so parsing edge cases never block ingestion.

We respect SEC fair access: descriptive User-Agent (required), token-bucket
rate limit, exponential backoff on 429/5xx. Live HTTP is never invoked in
tests — tests pass FixtureEdgarClient.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import httpx
from bs4 import BeautifulSoup
from prometheus_client import Counter
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from tenk_signal.config import Settings
from tenk_signal.logging import get_logger
from tenk_signal.models import SectionMode
from tenk_signal.services.universe import is_allowed

log = get_logger(__name__)

# Bounded fallback. ~60k chars ≈ 15k tokens, well under the model context.
_FALLBACK_MAX_CHARS = 60_000

# Regex for SEC item headers. Filings are wildly inconsistent so we cast wide.
_ITEM_7 = re.compile(r"item\s+7\.?\s*(?!a)", re.IGNORECASE)
_ITEM_7A = re.compile(r"item\s+7a\.?", re.IGNORECASE)
_ITEM_1A = re.compile(r"item\s+1a\.?", re.IGNORECASE)
_ITEM_1B = re.compile(r"item\s+1b\.?", re.IGNORECASE)
_ITEM_2 = re.compile(r"item\s+2\.?", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class FetchedFiling:
    cik: str
    ticker: str
    form_type: str
    accession_no: str
    filed_at: dt.datetime  # tz-aware UTC
    period_end: dt.date | None
    source_url: str
    text: str  # the slice we actually feed the LLM
    text_sha256: str  # sha256(text) — content-addressed dedupe + cache key
    section_extraction_mode: SectionMode


class EdgarClient(Protocol):
    async def fetch_recent(
        self, ticker: str, forms: list[str], limit: int
    ) -> list[FetchedFiling]: ...


class _TokenBucket:
    """Simple async token bucket. The SEC publishes 10 req/s as the cap."""

    def __init__(self, rate_per_sec: int) -> None:
        self._capacity = float(rate_per_sec)
        self._tokens = float(rate_per_sec)
        self._rate = float(rate_per_sec)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def take(self) -> None:
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = now
            if self._tokens < 1:
                wait = (1 - self._tokens) / self._rate
                await asyncio.sleep(wait)
                self._tokens = 0
            else:
                self._tokens -= 1


# ---------------------------------------------------------------------------
# Section parsing
# ---------------------------------------------------------------------------


# Prom counter for section-parse outcomes — surfaces in /metrics. Ops can
# alert if the fallback rate spikes (typically signals an SEC HTML format
# change). Labels = the SectionMode enum string.
_section_parse_total = Counter(
    "edgar_section_parse_total",
    "Outcomes of EDGAR section-parsing (per filing).",
    labelnames=("mode",),
)


# Sections are typically thousands of chars. Anything narrower than this
# between start and end markers is almost certainly a TOC entry, not a real
# section. Anything wider than the upper bound is almost certainly the
# entire rest of the document because we missed the end marker.
_SECTION_MIN_CHARS = 3_000
_SECTION_MAX_CHARS = 120_000


def _html_to_text(html: str) -> str:
    # Modern 10-Ks are inline-XBRL (.htm with an <?xml ...?> prolog). Strip
    # the prolog so BeautifulSoup's lxml selects the HTML path rather than
    # the XML path. The XML path produces a different DOM walk that loses
    # most of the visible text.
    if html.lstrip().startswith("<?xml"):
        # Trim through the first '>' to remove the prolog.
        idx = html.find("?>")
        if idx != -1:
            html = html[idx + 2 :]
    soup = BeautifulSoup(html, "lxml")
    # Drop script/style.
    for tag in soup(["script", "style", "head", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=False)
    # Inline-XBRL produces many runs of nbsp + line breaks. Collapse to keep
    # the text readable and the prompt budget tight.
    text = text.replace("\xa0", " ")
    return text


def _best_section_slice(
    text: str, start_pat: re.Pattern[str], end_pat: re.Pattern[str]
) -> str | None:
    """Pick the (start, end) pair with the largest gap inside the sane
    range. TOC entries pair up with tiny gaps; the real section is the one
    where the gap is large. If no such pairing exists, return None.
    """
    starts = [m.start() for m in start_pat.finditer(text)]
    ends = [m.start() for m in end_pat.finditer(text)]
    if not starts or not ends:
        return None

    best: tuple[int, int] | None = None  # (start, end)
    best_gap = 0
    for s in starts:
        # First end after s.
        candidate_ends = [e for e in ends if e > s]
        if not candidate_ends:
            continue
        e = candidate_ends[0]
        gap = e - s
        if gap < _SECTION_MIN_CHARS or gap > _SECTION_MAX_CHARS:
            continue
        if gap > best_gap:
            best_gap = gap
            best = (s, e)
    if best is None:
        return None
    return text[best[0] : best[1]].strip()


def extract_sections(html: str) -> tuple[str, SectionMode]:
    """Try to pull Item 7 (MD&A) + Item 1A (Risk Factors). Fall back to a
    bounded slice of the whole document if either fails.

    Returns (text, mode). Mode tells downstream code what it's looking at.

    Real 10-Ks defeat naïve "first/last occurrence" heuristics because Item
    headings appear many times — table of contents, cross-references, footers.
    We instead look at every (start, end) pair and pick the one whose gap is
    plausibly section-sized. See _best_section_slice.
    """
    text = _html_to_text(html)
    if len(text) == 0:
        return "", SectionMode.FULL_DOC_TRUNCATED

    mdna = _best_section_slice(text, _ITEM_7, _ITEM_7A)
    risk = _best_section_slice(text, _ITEM_1A, _ITEM_1B) or _best_section_slice(
        text, _ITEM_1A, _ITEM_2
    )

    parts = [p for p in (risk, mdna) if p]
    if parts and sum(len(p) for p in parts) >= 1000:
        joined = "\n\n--- SECTION BREAK ---\n\n".join(parts)
        # Truncate to bound prompt cost even on successful section split.
        _section_parse_total.labels(mode=SectionMode.MDNA_RISKFACTORS.value).inc()
        return joined[:_FALLBACK_MAX_CHARS], SectionMode.MDNA_RISKFACTORS

    # Fallback: bounded slice of the whole document.
    _section_parse_total.labels(mode=SectionMode.FULL_DOC_TRUNCATED.value).inc()
    return text[:_FALLBACK_MAX_CHARS], SectionMode.FULL_DOC_TRUNCATED


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Live HTTP client
# ---------------------------------------------------------------------------


_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
# CIK lookup by ticker. SEC publishes a static company_tickers.json.
_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


class LiveEdgarClient:
    """Production EDGAR client. Lazy ticker→CIK map; rate-limited; backoff."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bucket = _TokenBucket(settings.edgar_rate_limit_rps)
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": settings.edgar_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=httpx.Timeout(15.0),
        )
        self._cik_map: dict[str, str] | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError,)),
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _get(self, url: str) -> httpx.Response:
        await self._bucket.take()
        resp = await self._client.get(url)
        if resp.status_code in (429, 500, 502, 503, 504):
            resp.raise_for_status()
        return resp

    async def _ensure_cik_map(self) -> dict[str, str]:
        if self._cik_map is None:
            resp = await self._get(_TICKER_MAP_URL)
            resp.raise_for_status()
            data = resp.json()
            self._cik_map = {
                str(entry["ticker"]).upper(): f"{int(entry['cik_str']):010d}"
                for entry in data.values()
            }
        return self._cik_map

    async def fetch_recent(self, ticker: str, forms: list[str], limit: int) -> list[FetchedFiling]:
        if not is_allowed(ticker):
            raise ValueError(f"ticker not in allowlist: {ticker}")

        cik_map = await self._ensure_cik_map()
        cik = cik_map.get(ticker.upper())
        if cik is None:
            log.warning("edgar.unknown_ticker", ticker=ticker)
            return []

        resp = await self._get(_SUBMISSIONS_URL.format(cik=cik))
        resp.raise_for_status()
        sub = resp.json()
        recent = sub.get("filings", {}).get("recent", {})

        out: list[FetchedFiling] = []
        for i, form in enumerate(recent.get("form", [])):
            if form not in forms:
                continue
            accession = recent["accessionNumber"][i].replace("-", "")
            primary = recent["primaryDocument"][i]
            filed = dt.datetime.fromisoformat(recent["filingDate"][i]).replace(tzinfo=dt.UTC)
            # SEC's submissions JSON uses "reportDate" for the period-of-
            # report; "periodOfReport" is the EDGAR-internal name and isn't
            # exposed here. Some entries leave it empty for amendments.
            report_dates = recent.get("reportDate") or recent.get("periodOfReport") or []
            period_raw = report_dates[i] if i < len(report_dates) else None
            period_d = dt.date.fromisoformat(period_raw) if period_raw else None
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession}/{primary}"
            doc = await self._get(url)
            doc.raise_for_status()
            text, mode = extract_sections(doc.text)
            if not text:
                continue
            out.append(
                FetchedFiling(
                    cik=cik,
                    ticker=ticker.upper(),
                    form_type=form,
                    accession_no=recent["accessionNumber"][i],
                    filed_at=filed,
                    period_end=period_d,
                    source_url=url,
                    text=text,
                    text_sha256=sha256_hex(text),
                    section_extraction_mode=mode,
                )
            )
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# Fixture client (tests + seed script)
# ---------------------------------------------------------------------------


class FixtureEdgarClient:
    """Reads recorded fixtures. Two formats supported:

    1. ``TICKER_FORM_ACC_FILED_PERIOD.html`` — raw HTML; the parser runs at
       test time. Used for small hand-crafted fixtures.
    2. ``TICKER_FORM_ACC_FILED_PERIOD.meta.json`` (+ ``.parsed.txt``) —
       recorded by ``scripts/record_edgar_fixtures.py`` against the live SEC
       API. The parser ran at record time; we just hydrate the FetchedFiling.

    Format 2 is preferred because it exercises the same parser output the
    extractor would see in production while keeping the repo small.
    """

    def __init__(self, fixtures_dir: Path) -> None:
        self._dir = fixtures_dir

    async def fetch_recent(self, ticker: str, forms: list[str], limit: int) -> list[FetchedFiling]:
        ticker = ticker.upper()
        out: list[FetchedFiling] = []
        # First: pre-parsed (.meta.json + .parsed.txt) fixtures.
        for meta_path in sorted(self._dir.glob(f"{ticker}_*.meta.json")):
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("form_type") not in forms:
                continue
            txt_path = meta_path.with_suffix("").with_suffix(".parsed.txt")
            if not txt_path.exists():
                log.warning("fixture.missing_parsed_text", meta=str(meta_path))
                continue
            text = txt_path.read_text(encoding="utf-8")
            mode = SectionMode(meta["section_extraction_mode"])
            period_end = meta.get("period_end")
            out.append(
                FetchedFiling(
                    cik=meta["cik"],
                    ticker=meta["ticker"],
                    form_type=meta["form_type"],
                    accession_no=meta["accession_no"],
                    filed_at=dt.datetime.fromisoformat(meta["filed_at"]),
                    period_end=(dt.date.fromisoformat(period_end) if period_end else None),
                    source_url=meta.get("source_url", f"fixture://{meta_path.name}"),
                    text=text,
                    text_sha256=meta.get("text_sha256", sha256_hex(text)),
                    section_extraction_mode=mode,
                )
            )
            if len(out) >= limit:
                return out

        # Then: legacy .html fixtures (parser runs at test time).
        for path in sorted(self._dir.glob(f"{ticker}_*.html")):
            stem = path.stem
            try:
                _ticker, form, accession, filed_s, period_s = stem.split("_")
            except ValueError:
                continue
            if form not in forms:
                continue
            html = path.read_text(encoding="utf-8")
            text, mode = extract_sections(html)
            if not text:
                continue
            out.append(
                FetchedFiling(
                    cik="0000320193" if ticker == "AAPL" else "0000000000",
                    ticker=ticker,
                    form_type=form,
                    accession_no=accession,
                    filed_at=dt.datetime.fromisoformat(filed_s).replace(tzinfo=dt.UTC),
                    period_end=dt.date.fromisoformat(period_s),
                    source_url=f"fixture://{path.name}",
                    text=text,
                    text_sha256=sha256_hex(text),
                    section_extraction_mode=mode,
                )
            )
            if len(out) >= limit:
                break
        return out
