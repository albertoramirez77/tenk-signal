"""Ticker allowlist.

Inputs that go anywhere near a paid API (Anthropic) or a third-party (EDGAR,
yfinance) have to be validated against this list. The default is 30
large-cap US tickers; override via TENK_UNIVERSE env (comma-separated) if
you want a different basket.

Phase 3.5 only exercises AAPL + SPY; the rest of the list is here so the
production ingest can run against the full universe without code changes.
"""

from __future__ import annotations

import os

DEFAULT_UNIVERSE: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "GOOGL",
    "AMZN",
    "META",
    "NVDA",
    "TSLA",
    "BRK.B",
    "JPM",
    "V",
    "JNJ",
    "WMT",
    "MA",
    "PG",
    "XOM",
    "HD",
    "CVX",
    "MRK",
    "LLY",
    "ABBV",
    "AVGO",
    "PEP",
    "KO",
    "COST",
    "MCD",
    "ADBE",
    "CRM",
    "NFLX",
    "ORCL",
    "AMD",
)

# SPY is always allowed as the benchmark even if not in the user universe.
BENCHMARK_TICKER = "SPY"


def get_universe() -> frozenset[str]:
    env = os.environ.get("TENK_UNIVERSE", "").strip()
    if env:
        tickers = tuple(t.strip().upper() for t in env.split(",") if t.strip())
    else:
        tickers = DEFAULT_UNIVERSE
    return frozenset(tickers) | {BENCHMARK_TICKER}


def is_allowed(ticker: str) -> bool:
    return ticker.upper() in get_universe()
