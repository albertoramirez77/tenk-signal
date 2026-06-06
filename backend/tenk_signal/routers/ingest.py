"""POST /ingest — fetch filings + prices for a list of tickers.

Admin-only. Idempotent: existing filings (by accession_no) are skipped,
existing prices (by ticker+date) are upserted.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.auth import require_admin
from tenk_signal.db import get_session
from tenk_signal.deps import get_edgar, get_prices
from tenk_signal.logging import get_logger
from tenk_signal.models import Filing, Price
from tenk_signal.schemas import IngestRequest, IngestResult
from tenk_signal.services.edgar import EdgarClient
from tenk_signal.services.prices import PriceClient
from tenk_signal.services.universe import BENCHMARK_TICKER, is_allowed

router = APIRouter(prefix="/ingest", tags=["ingest"])
log = get_logger(__name__)


@router.post("", response_model=IngestResult, dependencies=[Depends(require_admin)])
async def ingest(
    body: IngestRequest,
    session: AsyncSession = Depends(get_session),
    edgar: EdgarClient = Depends(get_edgar),
    prices: PriceClient = Depends(get_prices),
) -> IngestResult:
    # Allowlist check at the boundary.
    for t in body.tickers:
        if not is_allowed(t):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"ticker not in allowlist: {t}",
            )

    filings_in = 0
    filings_skipped = 0
    prices_rows = 0

    for ticker in body.tickers:
        fetched = await edgar.fetch_recent(ticker, list(body.forms), body.limit_per_ticker)
        for f in fetched:
            existing = await session.execute(
                select(Filing.id).where(Filing.accession_no == f.accession_no)
            )
            if existing.scalar_one_or_none() is not None:
                filings_skipped += 1
                continue
            session.add(
                Filing(
                    cik=f.cik,
                    ticker=f.ticker,
                    form_type=f.form_type,
                    accession_no=f.accession_no,
                    filed_at=f.filed_at,
                    period_end=f.period_end,
                    source_url=f.source_url,
                    text_sha256=f.text_sha256,
                    text=f.text,
                    section_extraction_mode=f.section_extraction_mode,
                )
            )
            filings_in += 1

        # Price window: 30 days before earliest fetched filing through today.
        start = min(
            (f.filed_at.date() for f in fetched),
            default=dt.date.today() - dt.timedelta(days=180),
        ) - dt.timedelta(days=30)
        end = dt.date.today() + dt.timedelta(days=1)
        rows = prices.fetch(ticker, start, end)
        # Pull benchmark in the same window once per ticker (cheap; idempotent).
        rows.extend(prices.fetch(BENCHMARK_TICKER, start, end))
        for r in rows:
            stmt = (
                pg_insert(Price)
                .values(
                    ticker=r.ticker,
                    date=r.date,
                    open=r.open,
                    high=r.high,
                    low=r.low,
                    close=r.close,
                    adj_close=r.adj_close,
                    volume=r.volume,
                )
                .on_conflict_do_nothing(constraint="uq_prices_ticker_date")
            )
            await session.execute(stmt)
            prices_rows += 1

    await session.flush()
    log.info(
        "ingest.done",
        filings_in=filings_in,
        skipped=filings_skipped,
        prices=prices_rows,
    )
    return IngestResult(
        filings_ingested=filings_in,
        filings_skipped_existing=filings_skipped,
        prices_rows_upserted=prices_rows,
    )
