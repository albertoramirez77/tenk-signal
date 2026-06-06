"""POST /extract — run the extractor on filings, derive signals.

Admin-only. The extractor service handles caching; signal derivation is a
simple deterministic map: ``signal_value = sentiment * confidence`` with a
guidance overlay (raised: +0.1, lowered: -0.1, maintained: 0).
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.auth import require_admin
from tenk_signal.db import get_session
from tenk_signal.deps import get_extractor
from tenk_signal.logging import get_logger
from tenk_signal.models import Extraction as ExtractionRow
from tenk_signal.models import Filing, Guidance, Signal
from tenk_signal.schemas import ExtractRequest, ExtractResult
from tenk_signal.services.extractor import Extractor

router = APIRouter(prefix="/extract", tags=["extract"])
log = get_logger(__name__)


_GUIDANCE_OVERLAY = {Guidance.RAISED: 0.1, Guidance.MAINTAINED: 0.0, Guidance.LOWERED: -0.1}


def _derive_signal_value(sentiment: float, confidence: float, guidance: Guidance) -> float:
    base = sentiment * confidence
    return float(max(-1.0, min(1.0, base + _GUIDANCE_OVERLAY[guidance])))


@router.post("", response_model=ExtractResult, dependencies=[Depends(require_admin)])
async def extract(
    body: ExtractRequest,
    session: AsyncSession = Depends(get_session),
    extractor: Extractor = Depends(get_extractor),
) -> ExtractResult:
    if body.filing_id is None and not body.all_pending:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide filing_id or set all_pending=true",
        )

    if body.filing_id is not None:
        filings = (
            (await session.execute(select(Filing).where(Filing.id == body.filing_id)))
            .scalars()
            .all()
        )
        if not filings:
            raise HTTPException(status_code=404, detail="filing not found")
    else:
        # All filings with no extraction yet.
        q = (
            select(Filing)
            .outerjoin(ExtractionRow, ExtractionRow.filing_id == Filing.id)
            .where(ExtractionRow.id.is_(None))
        )
        filings = list((await session.execute(q)).scalars().all())

    extracted = cached = failed = 0
    for filing in filings:
        try:
            result = await extractor.extract(session, filing)
        except Exception as exc:
            failed += 1
            log.error("extract.error", filing_id=filing.id, error=str(exc), exc_info=True)
            continue
        if result.cached:
            cached += 1
        else:
            extracted += 1

        # Make sure a Signal row exists for downstream backtests. Idempotent.
        existing_sig = (
            await session.execute(
                select(Signal).where(
                    Signal.extraction_id.in_(
                        select(ExtractionRow.id).where(ExtractionRow.filing_id == filing.id)
                    )
                )
            )
        ).scalar_one_or_none()
        if existing_sig is not None:
            continue

        ext_row = (
            await session.execute(
                select(ExtractionRow).where(
                    ExtractionRow.filing_id == filing.id,
                    ExtractionRow.text_sha256 == filing.text_sha256,
                )
            )
        ).scalar_one()

        # active_from = filed_at + 1 business day. The backtester re-snaps
        # to the actual price calendar; this column is informational + the
        # DB CHECK constraint (active_from >= filed_at) safety net.
        active_from = filing.filed_at + dt.timedelta(days=1)
        sig_val = _derive_signal_value(
            ext_row.sentiment, ext_row.confidence, Guidance(ext_row.guidance)
        )
        session.add(
            Signal(
                extraction_id=ext_row.id,
                ticker=filing.ticker,
                filed_at=filing.filed_at,
                signal_value=sig_val,
                active_from=active_from,
            )
        )

    await session.flush()
    return ExtractResult(extracted=extracted, cached=cached, failed=failed)
