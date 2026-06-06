"""Parametrized tests against the recorded real-world EDGAR fixtures.

Goal: catch regressions when the section-parsing heuristic drifts or when
SEC changes their HTML layout. The fixtures were captured by
``scripts/record_edgar_fixtures.py`` and live under
``tests/fixtures/edgar/*.meta.json`` (+ ``.parsed.txt``).

These tests assert structural properties of the parsed text rather than
exact strings, since SEC filings change every year.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tenk_signal.models import SectionMode
from tenk_signal.services.edgar import (
    FixtureEdgarClient,
    extract_sections,
    sha256_hex,
)

FIX_DIR = Path(__file__).parent.parent / "fixtures" / "edgar"
META_PATHS: list[Path] = sorted(FIX_DIR.glob("*.meta.json"))


def _ids(p: Path) -> str:
    return p.stem.split(".")[0]


pytestmark = pytest.mark.skipif(
    not META_PATHS,
    reason="no recorded fixtures present (run scripts/record_edgar_fixtures.py)",
)


@pytest.mark.parametrize("meta_path", META_PATHS, ids=[_ids(p) for p in META_PATHS])
def test_recorded_fixture_has_consistent_metadata(meta_path: Path) -> None:
    """Every recorded fixture must have its companion parsed text and the
    sha256 must round-trip. This catches any silent corruption."""
    meta = json.loads(meta_path.read_text())
    txt_path = meta_path.with_suffix("").with_suffix(".parsed.txt")
    assert txt_path.exists(), f"missing parsed text for {meta_path.name}"
    text = txt_path.read_text(encoding="utf-8")

    assert sha256_hex(text) == meta["text_sha256"], "text_sha256 mismatch"
    assert meta["text_len"] == len(text)
    assert meta["section_extraction_mode"] in {m.value for m in SectionMode}
    # Bounded prompt budget: must not exceed the parser's truncation cap.
    assert len(text) <= 60_000
    assert len(text) >= 1_000, "real 10-K excerpt should be non-trivial"


@pytest.mark.parametrize("meta_path", META_PATHS, ids=[_ids(p) for p in META_PATHS])
def test_recorded_text_looks_like_real_filing_content(meta_path: Path) -> None:
    """Spot-check that the parsed text contains forward-looking / risk
    language. If the parser starts returning boilerplate footers or the
    inline-XBRL prolog, this fires."""
    txt_path = meta_path.with_suffix("").with_suffix(".parsed.txt")
    text = txt_path.read_text(encoding="utf-8").lower()
    # Risk Factors language. At least one of these must appear in a real
    # 10-K's Item 1A.
    risk_markers = ("risk", "adverse", "could", "may", "uncertain")
    assert any(m in text for m in risk_markers)
    # Inline-XBRL prolog must not leak through.
    assert "<?xml" not in text
    assert "xbrl" not in text or text.count("xbrl") < 5  # incidental refs OK


@pytest.mark.asyncio
async def test_fixture_client_loads_recorded_fixtures() -> None:
    """FixtureEdgarClient must hydrate FetchedFiling from the recorded
    format and return it for fetch_recent."""
    client = FixtureEdgarClient(FIX_DIR)
    seen = 0
    for meta_path in META_PATHS:
        meta = json.loads(meta_path.read_text())
        out = await client.fetch_recent(meta["ticker"], ["10-K"], limit=5)
        assert out, f"no filings returned for {meta['ticker']}"
        # The recorded filing must be in the result.
        matching = [f for f in out if f.accession_no == meta["accession_no"]]
        assert matching, f"recorded accession not found for {meta['ticker']}"
        f = matching[0]
        assert f.text_sha256 == meta["text_sha256"]
        assert f.section_extraction_mode.value == meta["section_extraction_mode"]
        seen += 1
    assert seen == len(META_PATHS)


def test_parser_picks_largest_plausible_section_not_toc() -> None:
    """Adversarial fixture: Item 7 / Item 7A pattern appears in three
    places — a TOC entry (tiny gap), a cross-reference (tiny gap), and the
    real section header (large gap). The parser must pick the third."""
    text_blocks = [
        "Cover page boilerplate. " * 20,
        "Item 1. Business",
        # TOC entry: Item 7 followed quickly by Item 7A
        "Item 7. MD&A " + "." * 30 + " Item 7A. Quant Risk",
        "Item 1A. Risk Factors\n"
        + ("risk language " * 600)
        + "\nItem 1B. Unresolved Staff Comments",
        "Cross-reference: see Item 7 above. Then Item 7A two pages later.",
        # Real MD&A section: large gap between Item 7 and Item 7A
        "Item 7. Management's Discussion and Analysis\n"
        + ("MDNA text " * 2000)
        + "\nItem 7A. Quantitative",
    ]
    html = "<html><body>" + "".join(f"<p>{b}</p>" for b in text_blocks) + "</body></html>"
    out, mode = extract_sections(html)
    assert mode is SectionMode.MDNA_RISKFACTORS
    # The real MDNA section content must be present.
    assert "MDNA text" in out
    # The TOC noise should not dominate.
    assert "Cover page boilerplate" not in out
