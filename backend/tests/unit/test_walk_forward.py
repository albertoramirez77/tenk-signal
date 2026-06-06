"""Walk-forward backtest tests."""

from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from tenk_signal.schemas import BacktestConfig
from tenk_signal.services.backtest import PriceInput, SignalInput, run_backtest


def _prices(start: str, n: int, drift: float = 0.0008, vol: float = 0.012) -> list[PriceInput]:
    dates = pd.bdate_range(start, periods=n)
    rng = np.random.default_rng(42)
    ret = rng.normal(drift, vol, size=n)
    p = 100.0 * np.exp(np.cumsum(ret))
    return [PriceInput("AAA", d.date(), float(x)) for d, x in zip(dates, p, strict=False)]


def _signals_every_n_days(n_signals: int, every_days: int = 5) -> list[SignalInput]:
    rng = np.random.default_rng(7)
    out: list[SignalInput] = []
    base = dt.datetime(2024, 1, 8, 9, 0, tzinfo=dt.UTC)
    for i in range(n_signals):
        out.append(
            SignalInput(
                ticker="AAA",
                filed_at=base + dt.timedelta(days=i * every_days),
                signal_value=float(rng.uniform(-0.5, 0.5)),
            )
        )
    return out


def test_walk_forward_disabled_returns_empty_list() -> None:
    px = _prices("2024-01-02", 80)
    sigs = _signals_every_n_days(10)
    out = run_backtest(sigs, px, BacktestConfig(walk_forward=False))
    assert out.walk_forward == []


def test_walk_forward_produces_n_windows() -> None:
    px = _prices("2024-01-02", 240)
    sigs = _signals_every_n_days(20, every_days=10)
    out = run_backtest(
        sigs,
        px,
        BacktestConfig(
            walk_forward=True, walk_forward_windows=4, horizon_days=3, execution_lag_days=1
        ),
    )
    assert len(out.walk_forward) == 4
    # Sum of window n_positions equals overall n_positions.
    assert sum(w.n_positions for w in out.walk_forward) == out.n_positions
    # Windows are chronologically ordered.
    for w_prev, w_next in zip(out.walk_forward, out.walk_forward[1:], strict=False):
        assert w_prev.end <= w_next.start


def test_walk_forward_skips_when_too_few_positions() -> None:
    """Two windows requested, only one position taken — should bail rather
    than produce a degenerate window."""
    px = _prices("2024-01-02", 30)
    sigs = _signals_every_n_days(1)
    out = run_backtest(sigs, px, BacktestConfig(walk_forward=True, walk_forward_windows=5))
    assert out.walk_forward == []
    assert out.n_positions in (0, 1)


def test_walk_forward_window_indices_start_at_zero() -> None:
    px = _prices("2024-01-02", 240)
    sigs = _signals_every_n_days(30, every_days=6)
    out = run_backtest(
        sigs,
        px,
        BacktestConfig(walk_forward=True, walk_forward_windows=3, horizon_days=3),
    )
    assert [w.index for w in out.walk_forward] == [0, 1, 2]
