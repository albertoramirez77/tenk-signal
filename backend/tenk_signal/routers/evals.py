"""Eval endpoints.

POST /evals/run (admin)  — recompute metrics, persist eval_results row.
GET  /evals (viewer)     — latest + history sparkline data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.auth import require_admin, require_viewer
from tenk_signal.config import Settings, get_settings
from tenk_signal.db import get_session
from tenk_signal.models import EvalResult
from tenk_signal.schemas import EvalSnapshot, EvalsResponse
from tenk_signal.services.evals import run_evals

router = APIRouter(prefix="/evals", tags=["evals"])


@router.post("/run", dependencies=[Depends(require_admin)])
async def run(
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    summary = await run_evals(
        session,
        model=settings.anthropic_model,
        prompt_version=settings.prompt_version,
    )
    return {
        "n": summary.n,
        "n_missing": summary.n_missing,
        "guidance_precision": summary.guidance_precision,
        "guidance_recall": summary.guidance_recall,
        "guidance_f1": summary.guidance_f1,
        "sentiment_mae": summary.sentiment_mae,
        "per_class_f1": summary.per_class_f1,
    }


@router.get("", response_model=EvalsResponse, dependencies=[Depends(require_viewer)])
async def get_evals(
    session: AsyncSession = Depends(get_session),
) -> EvalsResponse:
    rows = (
        (await session.execute(select(EvalResult).order_by(desc(EvalResult.run_at)).limit(50)))
        .scalars()
        .all()
    )
    snaps = [
        EvalSnapshot(
            run_at=r.run_at,
            n=r.n,
            guidance_precision=r.guidance_precision,
            guidance_recall=r.guidance_recall,
            guidance_f1=r.guidance_f1,
            sentiment_mae=r.sentiment_mae,
            prompt_version=r.prompt_version,
            model=r.model,
        )
        for r in rows
    ]
    return EvalsResponse(
        latest=snaps[0] if snaps else None,
        history=snaps,
    )
