"""Seed the local Postgres with the AAPL vertical-slice demo.

Idempotent. Reads the recorded EDGAR filing fixture + AAPL/SPY price
fixtures, runs the FixtureExtractor (no Anthropic call), derives a signal,
and runs one backtest with the default config.

Run:

    cd backend
    # 1. Have Postgres reachable via DATABASE_URL (see .env.example)
    uv run alembic upgrade head
    uv run python -m scripts.seed_demo
    # 2. Start the API
    uv run uvicorn tenk_signal.main:app --reload
    # 3. Hit it
    curl -H "X-API-Key: $APP_API_KEY_VIEWER" http://localhost:8000/signals

This script also runs in the e2e test so the same code path is exercised
end-to-end against an ephemeral DB in CI.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
from pathlib import Path

from sqlalchemy import select

from tenk_signal.config import get_settings
from tenk_signal.db import get_sessionmaker
from tenk_signal.logging import configure_logging
from tenk_signal.models import BacktestRun, Filing, Price
from tenk_signal.schemas import BacktestConfig
from tenk_signal.services import backtest as bt
from tenk_signal.services.edgar import FixtureEdgarClient
from tenk_signal.services.extractor import FixtureExtractor
from tenk_signal.services.prices import FixturePriceClient
from tenk_signal.services.universe import BENCHMARK_TICKER

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
TICKER = "AAPL"


async def _seed() -> None:
    configure_logging("INFO")
    log = logging.getLogger("seed")
    settings = get_settings()
    sm = get_sessionmaker()

    edgar = FixtureEdgarClient(FIXTURES / "edgar")
    prices = FixturePriceClient(FIXTURES / "prices")
    extractor = FixtureExtractor(settings, FIXTURES / "anthropic")

    async with sm() as session:
        # --- ingest the filing ---------------------------------------------
        fetched = await edgar.fetch_recent(TICKER, ["10-K"], limit=1)
        if not fetched:
            raise RuntimeError(f"no fixture filing found for {TICKER}")
        f = fetched[0]
        existing = (
            await session.execute(select(Filing).where(Filing.accession_no == f.accession_no))
        ).scalar_one_or_none()
        if existing is None:
            filing = Filing(
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
            session.add(filing)
            await session.flush()
        else:
            filing = existing
        log.info("seed.filing_id=%s ticker=%s", filing.id, filing.ticker)

        # --- prices --------------------------------------------------------
        start = dt.date(2023, 11, 1)
        end = dt.date(2024, 5, 1)
        for ticker in (TICKER, BENCHMARK_TICKER):
            for row in prices.fetch(ticker, start, end):
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = (
                    pg_insert(Price)
                    .values(
                        ticker=row.ticker,
                        date=row.date,
                        open=row.open,
                        high=row.high,
                        low=row.low,
                        close=row.close,
                        adj_close=row.adj_close,
                        volume=row.volume,
                    )
                    .on_conflict_do_nothing(constraint="uq_prices_ticker_date")
                )
                await session.execute(stmt)
        await session.flush()

        # --- extract (FixtureExtractor; no Anthropic call) -----------------
        result = await extractor.extract(session, filing)
        log.info(
            "seed.extraction guidance=%s sentiment=%.2f cached=%s",
            result.extraction.guidance,
            result.extraction.sentiment,
            result.cached,
        )

        # --- derive signal -------------------------------------------------
        from tenk_signal.models import Extraction as ExtractionRow
        from tenk_signal.models import Guidance, Signal
        from tenk_signal.routers.extract import _derive_signal_value

        ext_row = (
            await session.execute(select(ExtractionRow).where(ExtractionRow.filing_id == filing.id))
        ).scalar_one()
        existing_sig = (
            await session.execute(select(Signal).where(Signal.extraction_id == ext_row.id))
        ).scalar_one_or_none()
        if existing_sig is None:
            session.add(
                Signal(
                    extraction_id=ext_row.id,
                    ticker=filing.ticker,
                    filed_at=filing.filed_at,
                    signal_value=_derive_signal_value(
                        ext_row.sentiment,
                        ext_row.confidence,
                        Guidance(ext_row.guidance),
                    ),
                    active_from=filing.filed_at + dt.timedelta(days=1),
                )
            )
            await session.flush()

        # --- backtest ------------------------------------------------------
        sigs_q = await session.execute(select(Signal))
        sigs = [
            bt.SignalInput(ticker=s.ticker, filed_at=s.filed_at, signal_value=s.signal_value)
            for s in sigs_q.scalars().all()
        ]
        px_q = await session.execute(select(Price))
        pxs = [
            bt.PriceInput(ticker=p.ticker, date=p.date, adj_close=float(p.adj_close))
            for p in px_q.scalars().all()
        ]
        config = BacktestConfig()
        out = bt.run_backtest(sigs, pxs, config)

        run = BacktestRun(
            config_json=config.model_dump(mode="json"),
            hit_rate=out.hit_rate,
            mean_ret=out.mean_ret,
            vol=out.vol,
            sharpe=out.sharpe,
            equity_curve_json={"points": [p.model_dump(mode="json") for p in out.equity_curve]},
        )
        session.add(run)
        await session.commit()
        log.info(
            "seed.backtest n_positions=%d mean_ret=%s sharpe=%s",
            out.n_positions,
            out.mean_ret,
            out.sharpe,
        )


if __name__ == "__main__":
    asyncio.run(_seed())
