"""Daily price fetcher.

yfinance is unofficial and can break, so it lives behind a protocol. The
live implementation lazy-imports yfinance so tests never touch it. The
fixture implementation reads a CSV from tests/fixtures/prices/.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import pandas as pd

from tenk_signal.logging import get_logger
from tenk_signal.services.universe import is_allowed

log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class PriceRow:
    ticker: str
    date: dt.date
    open: float
    high: float
    low: float
    close: float
    adj_close: float
    volume: int


class PriceClient(Protocol):
    def fetch(self, ticker: str, start: dt.date, end: dt.date) -> list[PriceRow]: ...


class YFinancePriceClient:
    """Live yfinance wrapper. Imported lazily so tests don't pull it in."""

    def fetch(self, ticker: str, start: dt.date, end: dt.date) -> list[PriceRow]:
        if not is_allowed(ticker):
            raise ValueError(f"ticker not in allowlist: {ticker}")

        import yfinance as yf

        df = yf.download(
            ticker,
            start=start.isoformat(),
            end=end.isoformat(),
            progress=False,
            auto_adjust=False,
            actions=False,
        )
        if df.empty:
            log.warning("prices.empty", ticker=ticker, start=start, end=end)
            return []

        # yfinance returns a MultiIndex on columns when multiple tickers are
        # requested. We always pass one ticker, but defensive flatten anyway.
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        rows: list[PriceRow] = []
        for ts, row in df.iterrows():
            d = ts.date() if hasattr(ts, "date") else ts
            rows.append(
                PriceRow(
                    ticker=ticker,
                    date=d,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    adj_close=float(row.get("Adj Close", row["Close"])),
                    volume=int(row["Volume"]),
                )
            )
        return rows


class FixturePriceClient:
    """Reads recorded OHLCV from a CSV. Used in tests and the seed script."""

    def __init__(self, fixtures_dir: Path) -> None:
        self.dir = fixtures_dir

    def fetch(self, ticker: str, start: dt.date, end: dt.date) -> list[PriceRow]:
        path = self.dir / f"{ticker}.csv"
        if not path.exists():
            raise FileNotFoundError(f"no fixture for {ticker} at {path}")
        df = pd.read_csv(path, parse_dates=["date"])
        df = df[(df["date"].dt.date >= start) & (df["date"].dt.date < end)]
        return [
            PriceRow(
                ticker=ticker,
                date=row["date"].date(),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                adj_close=float(row["adj_close"]),
                volume=int(row["volume"]),
            )
            for _, row in df.iterrows()
        ]
