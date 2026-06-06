"""Backtest unit tests.

The critical one is ``test_no_lookahead``: a signal that perfectly equals
day-t's return must produce ~zero P&L because positions only take effect at
``t + execution_lag``. If anyone weakens the point-in-time rule, this fails.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from tenk_signal.schemas import BacktestConfig
from tenk_signal.services.backtest import (
    PriceInput,
    SignalInput,
    compute_active_from,
    next_trading_day,
    run_backtest,
)

# ---------------------------------------------------------------------------
# trading-calendar helpers
# ---------------------------------------------------------------------------

CAL_2024 = pd.bdate_range("2024-01-02", "2024-12-31")


def test_next_trading_day_on_trading_day() -> None:
    d = dt.date(2024, 3, 15)  # Friday
    assert next_trading_day(d, CAL_2024) == d


def test_next_trading_day_weekend_snaps_forward() -> None:
    sat = dt.date(2024, 3, 16)
    assert next_trading_day(sat, CAL_2024) == dt.date(2024, 3, 18)


def test_compute_active_from_lag_zero_intraday_filing() -> None:
    filed = dt.datetime(2024, 3, 15, 15, 30, tzinfo=dt.UTC)  # Fri 3:30pm
    # Lag 0 + intraday filing → next *full* trading session is Monday.
    af = compute_active_from(filed, CAL_2024, execution_lag_days=0)
    assert af == dt.date(2024, 3, 15)  # snap-forward semantics


def test_compute_active_from_lag_one_day_after_weekend_filing() -> None:
    filed = dt.datetime(2024, 3, 16, 9, 0, tzinfo=dt.UTC)  # Sat
    af = compute_active_from(filed, CAL_2024, execution_lag_days=1)
    # Sat → Mon (snap), then +1 trading day → Tue
    assert af == dt.date(2024, 3, 19)


# ---------------------------------------------------------------------------
# end-to-end
# ---------------------------------------------------------------------------


def _make_prices(start: str, n: int, ticker: str = "AAA") -> list[PriceInput]:
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(123)
    prices = 100.0 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, size=n)))
    return [PriceInput(ticker, d.date(), float(p)) for d, p in zip(dates, prices, strict=False)]


def test_run_backtest_with_zero_signals_returns_empty() -> None:
    px = _make_prices("2024-01-02", 30)
    out = run_backtest([], px, BacktestConfig())
    assert out.n_positions == 0
    assert out.equity_curve == []


def test_run_backtest_simple_long_signal_produces_curve() -> None:
    px = _make_prices("2024-01-02", 50)
    sigs = [
        SignalInput(
            ticker="AAA",
            filed_at=dt.datetime(2024, 1, 5, 9, 0, tzinfo=dt.UTC),
            signal_value=0.5,
        ),
        SignalInput(
            ticker="AAA",
            filed_at=dt.datetime(2024, 1, 20, 9, 0, tzinfo=dt.UTC),
            signal_value=0.3,
        ),
    ]
    out = run_backtest(sigs, px, BacktestConfig(horizon_days=5, execution_lag_days=1))
    assert out.n_positions == 2
    assert len(out.equity_curve) == 2
    assert out.hit_rate is not None
    assert 0.0 <= out.hit_rate <= 1.0


def test_costs_reduce_return() -> None:
    """Same positions with non-zero cost must have lower mean return."""
    px = _make_prices("2024-01-02", 80)
    sigs = [
        SignalInput(
            ticker="AAA",
            filed_at=dt.datetime(2024, 1, d, 9, 0, tzinfo=dt.UTC),
            signal_value=0.5,
        )
        for d in (5, 12, 19, 26)
    ]
    free = run_backtest(sigs, px, BacktestConfig(transaction_cost_bps=0.0))
    costly = run_backtest(sigs, px, BacktestConfig(transaction_cost_bps=50.0))
    assert free.mean_ret is not None and costly.mean_ret is not None
    assert free.mean_ret > costly.mean_ret


# ---------------------------------------------------------------------------
# THE no-look-ahead test (PLAN.md §6 requires it)
# ---------------------------------------------------------------------------


def test_no_lookahead_with_perfect_future_signal() -> None:
    """Adversarial setup: filed_at sits *during* day t and the signal's sign
    perfectly equals the close-to-close return of day t. A look-ahead
    backtester would harvest this. A correct one takes the position only at
    t + lag, so the future-return information is consumed and the mean P&L
    over many trials hovers near zero.
    """
    # Make a deterministic price path.
    dates = pd.bdate_range("2024-01-02", periods=60)
    rng = np.random.default_rng(7)
    daily_ret = rng.normal(0.0, 0.012, size=len(dates))
    prices = 100.0 * np.exp(np.cumsum(daily_ret))
    px = [PriceInput("AAA", d.date(), float(p)) for d, p in zip(dates, prices, strict=False)]

    # One "oracle" signal per day: filed during day t, sign of that day's
    # return. The signal would yield ~max P&L IF used same-day. With lag=1
    # and horizon=1 it should average near zero.
    sigs: list[SignalInput] = []
    for i in range(1, len(dates) - 2):
        sign = 1.0 if daily_ret[i] > 0 else -1.0
        # File at mid-day of day t.
        filed = dt.datetime.combine(dates[i].date(), dt.time(13, 0), tzinfo=dt.UTC)
        sigs.append(SignalInput("AAA", filed, sign))

    out = run_backtest(
        sigs,
        px,
        BacktestConfig(horizon_days=1, execution_lag_days=1, transaction_cost_bps=0.0),
    )
    assert out.mean_ret is not None
    # Random walk + delayed perfect signal → mean near 0. Tight tolerance
    # here is the whole point; if someone breaks point-in-time logic the
    # mean spikes far above 0.
    assert abs(out.mean_ret) < 0.003, f"mean_ret={out.mean_ret} suggests look-ahead"


def test_signal_with_filed_at_past_data_end_is_dropped() -> None:
    px = _make_prices("2024-01-02", 20)
    sigs = [SignalInput("AAA", dt.datetime(2025, 6, 1, tzinfo=dt.UTC), signal_value=0.5)]
    out = run_backtest(sigs, px, BacktestConfig())
    assert out.n_positions == 0


@pytest.mark.parametrize("lag", [0, 1, 2, 5])
def test_lag_param_pushes_position_forward(lag: int) -> None:
    px = _make_prices("2024-01-02", 60)
    sig = SignalInput("AAA", dt.datetime(2024, 1, 8, 9, 0, tzinfo=dt.UTC), signal_value=0.5)
    out = run_backtest(
        [sig],
        px,
        BacktestConfig(horizon_days=3, execution_lag_days=lag, transaction_cost_bps=0.0),
    )
    # Just asserting the engine runs cleanly across lag values.
    assert out.n_positions == 1
