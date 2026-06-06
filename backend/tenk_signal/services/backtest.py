"""Backtest engine.

Methodology (PLAN.md §6):

* For each (extraction, signal) we compute ``active_from`` =
  ``next_trading_day_at_or_after(filed_at) + execution_lag_days``.
* Positions are taken only on trading days >= ``active_from`` for the next
  ``horizon_days``. This is what enforces point-in-time correctness; the
  same rule is also a CHECK constraint at the DB layer (see models.py).
* Returns are computed close-to-close on ``adj_close``, sign-multiplied by
  the signal direction, net of ``transaction_cost_bps`` per position turn.
* Metrics: hit rate (% of positions with positive net return), mean return
  per position, volatility, annualized Sharpe (assume 252 trading days,
  zero risk-free for this prototype), equity curve normalized to 1.0.

The function below operates on pure data structures; the router wraps it
with DB I/O.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from tenk_signal.schemas import BacktestConfig, EquityPoint, WalkForwardWindow

if TYPE_CHECKING:
    from collections.abc import Iterable


_BPS = 1e-4
_TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True, slots=True)
class SignalInput:
    """One signal row, denormalized for the engine."""

    ticker: str
    filed_at: dt.datetime  # tz-aware
    signal_value: float


@dataclass(frozen=True, slots=True)
class PriceInput:
    """One day of adj_close for one ticker. Engine needs nothing else."""

    ticker: str
    date: dt.date
    adj_close: float


@dataclass(frozen=True, slots=True)
class BacktestOutput:
    hit_rate: float | None
    mean_ret: float | None
    vol: float | None
    sharpe: float | None
    equity_curve: list[EquityPoint]
    n_positions: int
    walk_forward: list[WalkForwardWindow] = field(default_factory=list)


def next_trading_day(d: dt.date, calendar: pd.DatetimeIndex) -> dt.date | None:
    """First trading day >= d in the calendar, or None if past end."""
    ts = pd.Timestamp(d)
    idx = calendar.searchsorted(ts, side="left")
    if idx >= len(calendar):
        return None
    return calendar[idx].date()  # type: ignore[no-any-return]


def compute_active_from(
    filed_at: dt.datetime,
    calendar: pd.DatetimeIndex,
    execution_lag_days: int,
) -> dt.date | None:
    """Snap filed_at to the next trading day, then add execution_lag *trading*
    days. Returns None if we run off the end of available data."""
    # filed_at may be intraday/after-hours. Snap to next trading day at-or-after.
    first_eligible = next_trading_day(filed_at.date(), calendar)
    if first_eligible is None:
        return None
    idx = calendar.searchsorted(pd.Timestamp(first_eligible), side="left")
    target = idx + execution_lag_days
    if target >= len(calendar):
        return None
    return calendar[target].date()  # type: ignore[no-any-return]


def run_backtest(
    signals: Iterable[SignalInput],
    prices: Iterable[PriceInput],
    config: BacktestConfig,
) -> BacktestOutput:
    """Compute metrics and equity curve from in-memory inputs.

    Determinism: for the same inputs and config, the output is byte-identical.
    """
    sig_df = pd.DataFrame(
        [
            {"ticker": s.ticker, "filed_at": s.filed_at, "signal_value": s.signal_value}
            for s in signals
        ]
    )
    px_df = pd.DataFrame(
        [
            {"ticker": p.ticker, "date": pd.Timestamp(p.date), "adj_close": p.adj_close}
            for p in prices
        ]
    )
    if sig_df.empty or px_df.empty:
        return BacktestOutput(None, None, None, None, [], 0, [])

    px_df = px_df.sort_values(["ticker", "date"]).reset_index(drop=True)
    cost = config.transaction_cost_bps * _BPS

    # Per-ticker trading calendars, since not every name trades every day.
    per_ticker_returns: list[tuple[dt.date, float]] = []
    for ticker, grp in px_df.groupby("ticker", sort=False):
        cal = pd.DatetimeIndex(grp["date"].to_list())
        # date → adj_close lookup.
        px = grp.set_index("date")["adj_close"].astype(float)

        sigs = sig_df[sig_df["ticker"] == ticker]
        for _, row in sigs.iterrows():
            active_from = compute_active_from(row["filed_at"], cal, config.execution_lag_days)
            if active_from is None:
                continue
            # Take a position for horizon_days starting at active_from.
            i0 = cal.searchsorted(pd.Timestamp(active_from), side="left")
            i1 = i0 + config.horizon_days
            if i1 >= len(cal):
                continue
            p0 = float(px.iloc[i0])
            p1 = float(px.iloc[i1])
            if p0 <= 0:
                continue
            raw_ret = (p1 - p0) / p0
            direction = 1.0 if row["signal_value"] >= 0 else -1.0
            net_ret = direction * raw_ret - 2 * cost  # cost on entry + exit
            per_ticker_returns.append((cal[i1].date(), net_ret))

    if not per_ticker_returns:
        return BacktestOutput(None, None, None, None, [], 0, [])

    per_ticker_returns.sort(key=lambda x: x[0])

    hit_rate, mean_ret, vol, sharpe = _agg_metrics(per_ticker_returns, config.horizon_days)
    equity = _equity_curve(per_ticker_returns)

    walk_forward = _walk_forward_windows(per_ticker_returns, config) if config.walk_forward else []

    return BacktestOutput(
        hit_rate=hit_rate,
        mean_ret=mean_ret,
        vol=vol,
        sharpe=sharpe,
        equity_curve=equity,
        n_positions=len(per_ticker_returns),
        walk_forward=walk_forward,
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _agg_metrics(
    rows: list[tuple[dt.date, float]], horizon_days: int
) -> tuple[float | None, float | None, float | None, float | None]:
    if not rows:
        return None, None, None, None
    rets = np.array([r for _, r in rows], dtype=float)
    hit_rate = float((rets > 0).mean())
    mean_ret = float(rets.mean())
    vol = float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
    if vol > 0:
        periods_per_year = _TRADING_DAYS_PER_YEAR / max(horizon_days, 1)
        sharpe: float | None = float((mean_ret / vol) * np.sqrt(periods_per_year))
    else:
        sharpe = None
    return hit_rate, mean_ret, vol, sharpe


def _equity_curve(rows: list[tuple[dt.date, float]]) -> list[EquityPoint]:
    """Compound positions sequentially by exit date, normalized to 1.0."""
    out: list[EquityPoint] = []
    e = 1.0
    for d, r in rows:
        e *= 1.0 + r
        out.append(EquityPoint(date=d, equity=round(e, 6)))
    return out


def _walk_forward_windows(
    rows: list[tuple[dt.date, float]], config: BacktestConfig
) -> list[WalkForwardWindow]:
    """Equal-position-count rolling windows.

    Splits the chronologically-sorted positions into N contiguous groups of
    roughly equal size and reports per-window metrics. For a strategy with
    a stable signal, per-window Sharpe should not collapse from one to the
    next; if it does, the signal is unstable.
    """
    if len(rows) < config.walk_forward_windows * 2:
        # Not enough positions to form meaningful windows.
        return []
    n_windows = config.walk_forward_windows
    chunk = len(rows) // n_windows
    out: list[WalkForwardWindow] = []
    for i in range(n_windows):
        start = i * chunk
        end = (i + 1) * chunk if i < n_windows - 1 else len(rows)
        window_rows = rows[start:end]
        hit, mean, _vol, sharpe = _agg_metrics(window_rows, config.horizon_days)
        out.append(
            WalkForwardWindow(
                index=i,
                start=window_rows[0][0],
                end=window_rows[-1][0],
                n_positions=len(window_rows),
                hit_rate=hit,
                mean_ret=mean,
                sharpe=sharpe,
            )
        )
    return out
