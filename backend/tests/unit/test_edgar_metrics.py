"""Confirm the parser increments the right Prom counter."""

from __future__ import annotations

from prometheus_client import REGISTRY

from tenk_signal.models import SectionMode
from tenk_signal.services.edgar import extract_sections


def _value(mode: str) -> float:
    v = REGISTRY.get_sample_value("edgar_section_parse_total", {"mode": mode})
    return float(v or 0.0)


def test_counter_increments_on_section_split() -> None:
    before = _value(SectionMode.MDNA_RISKFACTORS.value)
    html = (
        "<html><body>"
        "<p>cover</p>"
        "<p>Item 1A. Risk Factors</p>"
        "<p>" + ("risk text " * 600) + "</p>"
        "<p>Item 1B. Unresolved Staff Comments</p>"
        "<p>Item 7. MD&amp;A</p>"
        "<p>" + ("mdna text " * 600) + "</p>"
        "<p>Item 7A. Quant</p>"
        "</body></html>"
    )
    _, mode = extract_sections(html)
    assert mode is SectionMode.MDNA_RISKFACTORS
    after = _value(SectionMode.MDNA_RISKFACTORS.value)
    assert after == before + 1


def test_counter_increments_on_fallback() -> None:
    before = _value(SectionMode.FULL_DOC_TRUNCATED.value)
    html = "<html><body><p>" + ("filler. " * 200) + "</p></body></html>"
    _, mode = extract_sections(html)
    assert mode is SectionMode.FULL_DOC_TRUNCATED
    after = _value(SectionMode.FULL_DOC_TRUNCATED.value)
    assert after == before + 1
