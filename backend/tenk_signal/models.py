"""ORM models.

Six tables: filings, extractions, prices, signals, backtest_runs,
eval_results. Critical invariants live in constraints (not docstrings):

* ``filings.accession_no`` is globally unique (SEC's primary key).
* ``filings.text_sha256`` is unique — content-addressed dedupe.
* ``extractions`` has UNIQUE(text_sha256, prompt_version, model). This is the
  cache contract: the extractor uses INSERT ... ON CONFLICT DO NOTHING, so
  idempotency is a DB-level guarantee, not a convention.
* ``prices`` has UNIQUE(ticker, date).
"""

from __future__ import annotations

import datetime as dt
import enum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tenk_signal.db import Base


class Guidance(enum.StrEnum):
    """The three-class label the LLM emits and we score against."""

    RAISED = "raised"
    MAINTAINED = "maintained"
    LOWERED = "lowered"


class SectionMode(enum.StrEnum):
    """Which slice of the filing text fed the LLM."""

    MDNA_RISKFACTORS = "mdna_riskfactors"
    FULL_DOC_TRUNCATED = "full_doc_truncated"


# ---------------------------------------------------------------------------
# filings
# ---------------------------------------------------------------------------
class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    cik: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    form_type: Mapped[str] = mapped_column(String(10), nullable=False)
    accession_no: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    filed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    period_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(String(512), nullable=False)
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_extraction_mode: Mapped[SectionMode] = mapped_column(
        Enum(SectionMode, name="section_mode"), nullable=False
    )
    quarantined: Mapped[bool] = mapped_column(
        # Flagged by the prompt-injection heuristic in P5. Default false.
        # Surface in the dashboard so a human can review.
        default=False,
        nullable=False,
        server_default="false",
    )
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    extractions: Mapped[list[Extraction]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )

    __table_args__ = (CheckConstraint("char_length(text) > 0", name="filings_text_nonempty"),)


# ---------------------------------------------------------------------------
# extractions
# ---------------------------------------------------------------------------
class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    filing_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("filings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalized: keeps the cache key local without joining filings.
    text_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    sentiment: Mapped[float] = mapped_column(Float, nullable=False)
    guidance: Mapped[Guidance] = mapped_column(Enum(Guidance, name="guidance"), nullable=False)
    risk_flag_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    rationale: Mapped[str] = mapped_column(String(2000), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    filing: Mapped[Filing] = relationship(back_populates="extractions")
    signal: Mapped[Signal | None] = relationship(
        back_populates="extraction", uselist=False, cascade="all, delete-orphan"
    )

    __table_args__ = (
        # THE cache contract. Re-extracting the same text under the same
        # (prompt_version, model) is a no-op. See services/extractor.py.
        UniqueConstraint(
            "text_sha256",
            "prompt_version",
            "model",
            name="uq_extraction_cache",
        ),
        CheckConstraint("sentiment >= -1 AND sentiment <= 1", name="ck_sentiment_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="ck_confidence_range"),
        CheckConstraint(
            "risk_flag_count >= 0 AND risk_flag_count <= 200",
            name="ck_risk_flag_count_range",
        ),
    )


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------
class Price(Base):
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(10), nullable=False)
    # Trading date in the asset's local market calendar.
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    open: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    adj_close: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
    volume: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "date", name="uq_prices_ticker_date"),
        Index("ix_prices_ticker_date", "ticker", "date"),
    )


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    extraction_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("extractions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,  # one signal per extraction
    )
    ticker: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    filed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signal_value: Mapped[float] = mapped_column(Float, nullable=False)
    # active_from = filed_at + execution_lag, snapped to next trading day.
    # The backtester filters returns by this column to avoid look-ahead.
    active_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )

    extraction: Mapped[Extraction] = relationship(back_populates="signal")

    __table_args__ = (
        CheckConstraint(
            "active_from >= filed_at",
            name="ck_signal_no_lookahead",
        ),
    )


# ---------------------------------------------------------------------------
# backtest_runs
# ---------------------------------------------------------------------------
class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    mean_ret: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol: Mapped[float | None] = mapped_column(Float, nullable=True)
    sharpe: Mapped[float | None] = mapped_column(Float, nullable=True)
    equity_curve_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)


# ---------------------------------------------------------------------------
# eval_results
# ---------------------------------------------------------------------------
class EvalResult(Base):
    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    run_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    n: Mapped[int] = mapped_column(Integer, nullable=False)
    guidance_precision: Mapped[float] = mapped_column(Float, nullable=False)
    guidance_recall: Mapped[float] = mapped_column(Float, nullable=False)
    guidance_f1: Mapped[float] = mapped_column(Float, nullable=False)
    sentiment_mae: Mapped[float] = mapped_column(Float, nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
