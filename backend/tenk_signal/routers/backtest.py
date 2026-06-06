"""Backtest endpoints.

POST /backtest (admin)         — run the backtest, persist a BacktestRun.
GET  /backtest/{id} (viewer)   — retrieve the summary + equity curve.
GET  /backtest (viewer)        — list recent runs.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.auth import require_admin, require_viewer
from tenk_signal.db import get_session
from tenk_signal.models import BacktestRun, Price, Signal
from tenk_signal.schemas import (
    BacktestConfig,
    BacktestDetail,
    BacktestSummary,
    EquityPoint,
    WalkForwardWindow,
)
from tenk_signal.services import backtest as bt

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("", response_model=BacktestDetail, dependencies=[Depends(require_admin)])
async def run(
    config: BacktestConfig,
    session: AsyncSession = Depends(get_session),
) -> BacktestDetail:
    sigs_q = await session.execute(select(Signal))
    sigs = [
        bt.SignalInput(ticker=row.ticker, filed_at=row.filed_at, signal_value=row.signal_value)
        for row in sigs_q.scalars().all()
    ]
    px_q = await session.execute(select(Price))
    pxs = [
        bt.PriceInput(ticker=row.ticker, date=row.date, adj_close=float(row.adj_close))
        for row in px_q.scalars().all()
    ]
    out = bt.run_backtest(sigs, pxs, config)

    run_row = BacktestRun(
        config_json=config.model_dump(mode="json"),
        hit_rate=out.hit_rate,
        mean_ret=out.mean_ret,
        vol=out.vol,
        sharpe=out.sharpe,
        equity_curve_json={
            "points": [p.model_dump(mode="json") for p in out.equity_curve],
            "walk_forward": [w.model_dump(mode="json") for w in out.walk_forward],
        },
    )
    session.add(run_row)
    await session.flush()

    return BacktestDetail(
        id=run_row.id,
        created_at=run_row.created_at,
        config=config,
        hit_rate=out.hit_rate,
        mean_ret=out.mean_ret,
        vol=out.vol,
        sharpe=out.sharpe,
        equity_curve=out.equity_curve,
        walk_forward_windows=out.walk_forward,
    )


@router.get("/{run_id}", response_model=BacktestDetail, dependencies=[Depends(require_viewer)])
async def get_run(run_id: int, session: AsyncSession = Depends(get_session)) -> BacktestDetail:
    row = (
        await session.execute(select(BacktestRun).where(BacktestRun.id == run_id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    points_raw = (row.equity_curve_json or {}).get("points", [])
    wf_raw = (row.equity_curve_json or {}).get("walk_forward", [])
    return BacktestDetail(
        id=row.id,
        created_at=row.created_at,
        config=BacktestConfig.model_validate(row.config_json),
        hit_rate=row.hit_rate,
        mean_ret=row.mean_ret,
        vol=row.vol,
        sharpe=row.sharpe,
        equity_curve=[EquityPoint.model_validate(p) for p in points_raw],
        walk_forward_windows=[WalkForwardWindow.model_validate(w) for w in wf_raw],
    )


@router.get("", response_model=list[BacktestSummary], dependencies=[Depends(require_viewer)])
async def list_runs(
    session: AsyncSession = Depends(get_session),
) -> list[BacktestSummary]:
    rows = (
        (
            await session.execute(
                select(BacktestRun).order_by(desc(BacktestRun.created_at)).limit(20)
            )
        )
        .scalars()
        .all()
    )
    return [
        BacktestSummary(
            id=r.id,
            created_at=r.created_at,
            config=BacktestConfig.model_validate(r.config_json),
            hit_rate=r.hit_rate,
            mean_ret=r.mean_ret,
            vol=r.vol,
            sharpe=r.sharpe,
        )
        for r in rows
    ]
