"""Section parser tests.

Asserts the happy path (Item 1A + Item 7 visible → MDNA_RISKFACTORS mode)
and the fallback path (sections missing → FULL_DOC_TRUNCATED).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tenk_signal.models import SectionMode
from tenk_signal.services.edgar import (
    FixtureEdgarClient,
    extract_sections,
    sha256_hex,
)

FIX_DIR = Path(__file__).parent.parent / "fixtures" / "edgar"


def test_parser_finds_both_sections() -> None:
    html = (FIX_DIR / "AAPL_10-K_0000320193-23-000106_2023-11-03_2023-09-30.html").read_text()
    text, mode = extract_sections(html)
    assert mode is SectionMode.MDNA_RISKFACTORS
    # Both sections present.
    assert "Risk Factors" in text or "Risk Factors".lower() in text.lower()
    assert "Management" in text  # MD&A header
    # Forward-looking outlook language present (load-bearing for the LLM).
    assert "raising" in text.lower() or "outlook" in text.lower()


def test_parser_falls_back_when_headings_absent() -> None:
    html = "<html><body><p>" + ("filler text. " * 200) + "</p></body></html>"
    text, mode = extract_sections(html)
    assert mode is SectionMode.FULL_DOC_TRUNCATED
    assert len(text) > 100


def test_parser_empty_html() -> None:
    text, mode = extract_sections("")
    assert mode is SectionMode.FULL_DOC_TRUNCATED
    assert text == ""


def test_parser_truncates_long_full_doc() -> None:
    html = "<html><body>" + ("x" * 200_000) + "</body></html>"
    text, _ = extract_sections(html)
    assert len(text) <= 60_000


def test_sha256_stable() -> None:
    a = sha256_hex("hello")
    b = sha256_hex("hello")
    c = sha256_hex("world")
    assert a == b != c
    assert len(a) == 64


@pytest.mark.asyncio
async def test_fixture_client_returns_filing() -> None:
    client = FixtureEdgarClient(FIX_DIR)
    out = await client.fetch_recent("AAPL", ["10-K"], limit=5)
    assert len(out) == 1
    f = out[0]
    assert f.ticker == "AAPL"
    assert f.form_type == "10-K"
    assert f.accession_no == "0000320193-23-000106"
    assert f.filed_at.year == 2023 and f.filed_at.month == 11
    assert f.section_extraction_mode is SectionMode.MDNA_RISKFACTORS
    assert f.text_sha256 == sha256_hex(f.text)


@pytest.mark.asyncio
async def test_fixture_client_filters_by_form() -> None:
    client = FixtureEdgarClient(FIX_DIR)
    out = await client.fetch_recent("AAPL", ["10-Q"], limit=5)
    assert out == []  # only the 10-K fixture exists
