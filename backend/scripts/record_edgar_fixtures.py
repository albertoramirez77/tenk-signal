"""Record real EDGAR fixtures for a small basket of tickers.

This is the only place the live EDGAR client is exercised outside of
production ingest. Output goes to ``tests/fixtures/edgar/`` and is
committed so tests stay reproducible and offline.

Run with the same env vars that production needs (EDGAR_USER_AGENT etc).
Conservative rate limit — well under SEC's 10 rps cap. ~15 requests total.

Usage::

    uv run python -m scripts.record_edgar_fixtures

The script is idempotent: existing fixture files are not re-downloaded
unless ``--force`` is passed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from tenk_signal.config import get_settings
from tenk_signal.services.edgar import LiveEdgarClient

# Variety = exercises the parser against different sectors' filing styles.
BASKET = ("MSFT", "JPM", "JNJ", "XOM", "KO")

FIX_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "edgar"


async def _record(force: bool) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    log = logging.getLogger("record")
    FIX_DIR.mkdir(parents=True, exist_ok=True)

    settings = get_settings()
    client = LiveEdgarClient(settings)
    try:
        manifest: list[dict[str, object]] = []
        for ticker in BASKET:
            log.info("fetching ticker=%s", ticker)
            try:
                filings = await client.fetch_recent(ticker, ["10-K"], limit=1)
            except Exception as exc:
                log.error("  failed: %s", exc)
                continue
            if not filings:
                log.warning("  no 10-K returned for %s", ticker)
                continue
            f = filings[0]
            period = f.period_end.isoformat() if f.period_end is not None else "unknown"
            filed = f.filed_at.date().isoformat()
            stem = f"{ticker}_10-K_{f.accession_no}_{filed}_{period}"
            txt_path = FIX_DIR / f"{stem}.parsed.txt"
            meta_path = FIX_DIR / f"{stem}.meta.json"
            # We persist:
            #   <stem>.parsed.txt   — the post-parser text the LLM would see
            #   <stem>.meta.json    — accession, dates, mode, sha, source URL
            # The raw primary HTML is intentionally NOT committed (often 5-10
            # MB per file x N tickers would bloat the repo). Tests load the
            # parsed text directly via FixtureEdgarClient-compatible meta.
            if not force and txt_path.exists():
                log.info("  exists, skipping (use --force to overwrite)")
                manifest.append({"ticker": ticker, "stem": stem, "skipped": True})
                continue
            txt_path.write_text(f.text, encoding="utf-8")
            meta = {
                "ticker": f.ticker,
                "cik": f.cik,
                "form_type": f.form_type,
                "accession_no": f.accession_no,
                "filed_at": f.filed_at.isoformat(),
                "period_end": period if period != "unknown" else None,
                "source_url": f.source_url,
                "text_sha256": f.text_sha256,
                "section_extraction_mode": f.section_extraction_mode.value,
                "text_len": len(f.text),
            }
            meta_path.write_text(json.dumps(meta, indent=2) + "\n")
            log.info(
                "  saved %s (mode=%s len=%d)",
                stem,
                f.section_extraction_mode.value,
                len(f.text),
            )
            manifest.append({"ticker": ticker, "stem": stem, **meta})
    finally:
        await client.aclose()

    # Print a summary so the operator can eyeball parser success rate.
    log.info("--- summary ---")
    counts: dict[str, int] = {"mdna_riskfactors": 0, "full_doc_truncated": 0}
    for m in manifest:
        mode = m.get("section_extraction_mode")
        if isinstance(mode, str):
            counts[mode] = counts.get(mode, 0) + 1
    total = counts["mdna_riskfactors"] + counts["full_doc_truncated"]
    if total:
        rate = counts["mdna_riskfactors"] / total
        log.info(
            "section split: %d/%d (%.0f%%); fallback: %d",
            counts["mdna_riskfactors"],
            total,
            rate * 100,
            counts["full_doc_truncated"],
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    asyncio.run(_record(force=args.force))


if __name__ == "__main__":
    main()
