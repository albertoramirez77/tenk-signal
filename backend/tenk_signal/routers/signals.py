"""GET /signals — viewer-readable list of signals with filing context."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.auth import require_viewer
from tenk_signal.db import get_session
from tenk_signal.models import Extraction, Filing, Signal
from tenk_signal.schemas import SignalRow, SignalsResponse

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("", response_model=SignalsResponse, dependencies=[Depends(require_viewer)])
async def list_signals(
    session: AsyncSession = Depends(get_session),
    limit: int = 50,
) -> SignalsResponse:
    q = (
        select(Signal, Extraction, Filing)
        .join(Extraction, Extraction.id == Signal.extraction_id)
        .join(Filing, Filing.id == Extraction.filing_id)
        .order_by(desc(Signal.filed_at))
        .limit(min(limit, 200))
    )
    rows = (await session.execute(q)).all()
    return SignalsResponse(
        rows=[
            SignalRow(
                id=s.id,
                ticker=s.ticker,
                filed_at=s.filed_at,
                active_from=s.active_from,
                signal_value=s.signal_value,
                guidance=e.guidance.value,
                sentiment=e.sentiment,
                confidence=e.confidence,
                quarantined=f.quarantined,
            )
            for s, e, f in rows
        ]
    )
