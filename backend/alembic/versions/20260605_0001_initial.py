"""initial schema: filings, extractions, prices, signals, backtest_runs, eval_results

Revision ID: 0001_initial
Revises:
Create Date: 2026-06-05

The cache contract — UNIQUE(text_sha256, prompt_version, model) on
extractions — is enforced here at the DB layer, not via app convention.
The no-look-ahead invariant — active_from >= filed_at on signals — is a
CHECK constraint. Both downgrade reversibly.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- enums ---------------------------------------------------------------
    op.execute("CREATE TYPE guidance AS ENUM ('raised', 'maintained', 'lowered')")
    op.execute(
        "CREATE TYPE section_mode AS ENUM "
        "('mdna_riskfactors', 'full_doc_truncated')"
    )

    # --- filings -------------------------------------------------------------
    op.create_table(
        "filings",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("cik", sa.String(20), nullable=False),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("form_type", sa.String(10), nullable=False),
        sa.Column("accession_no", sa.String(32), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("source_url", sa.String(512), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "section_extraction_mode",
            postgresql.ENUM(
                "mdna_riskfactors",
                "full_doc_truncated",
                name="section_mode",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "quarantined",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("accession_no", name="uq_filings_accession_no"),
        sa.UniqueConstraint("text_sha256", name="uq_filings_text_sha256"),
        sa.CheckConstraint("char_length(text) > 0", name="filings_text_nonempty"),
    )
    op.create_index("ix_filings_cik", "filings", ["cik"])
    op.create_index("ix_filings_ticker", "filings", ["ticker"])
    op.create_index("ix_filings_filed_at", "filings", ["filed_at"])

    # --- extractions ---------------------------------------------------------
    op.create_table(
        "extractions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "filing_id",
            sa.BigInteger(),
            sa.ForeignKey("filings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("sentiment", sa.Float(), nullable=False),
        sa.Column(
            "guidance",
            postgresql.ENUM(
                "raised", "maintained", "lowered", name="guidance", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("risk_flag_count", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("rationale", sa.String(2000), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # The cache contract. INSERT ... ON CONFLICT DO NOTHING relies on it.
        sa.UniqueConstraint(
            "text_sha256", "prompt_version", "model", name="uq_extraction_cache"
        ),
        sa.CheckConstraint(
            "sentiment >= -1 AND sentiment <= 1", name="ck_sentiment_range"
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1", name="ck_confidence_range"
        ),
        sa.CheckConstraint(
            "risk_flag_count >= 0 AND risk_flag_count <= 200",
            name="ck_risk_flag_count_range",
        ),
    )
    op.create_index("ix_extractions_filing_id", "extractions", ["filing_id"])

    # --- prices --------------------------------------------------------------
    op.create_table(
        "prices",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(18, 6), nullable=False),
        sa.Column("high", sa.Numeric(18, 6), nullable=False),
        sa.Column("low", sa.Numeric(18, 6), nullable=False),
        sa.Column("close", sa.Numeric(18, 6), nullable=False),
        sa.Column("adj_close", sa.Numeric(18, 6), nullable=False),
        sa.Column("volume", sa.BigInteger(), nullable=False),
        sa.UniqueConstraint("ticker", "date", name="uq_prices_ticker_date"),
    )
    op.create_index("ix_prices_ticker_date", "prices", ["ticker", "date"])

    # --- signals -------------------------------------------------------------
    op.create_table(
        "signals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "extraction_id",
            sa.BigInteger(),
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("ticker", sa.String(10), nullable=False),
        sa.Column("filed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signal_value", sa.Float(), nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        # Hard structural guarantee: a signal cannot be active before its filing.
        sa.CheckConstraint("active_from >= filed_at", name="ck_signal_no_lookahead"),
    )
    op.create_index("ix_signals_ticker", "signals", ["ticker"])
    op.create_index("ix_signals_active_from", "signals", ["active_from"])

    # --- backtest_runs -------------------------------------------------------
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("config_json", postgresql.JSONB(), nullable=False),
        sa.Column("hit_rate", sa.Float(), nullable=True),
        sa.Column("mean_ret", sa.Float(), nullable=True),
        sa.Column("vol", sa.Float(), nullable=True),
        sa.Column("sharpe", sa.Float(), nullable=True),
        sa.Column("equity_curve_json", postgresql.JSONB(), nullable=True),
    )

    # --- eval_results --------------------------------------------------------
    op.create_table(
        "eval_results",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "run_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("n", sa.Integer(), nullable=False),
        sa.Column("guidance_precision", sa.Float(), nullable=False),
        sa.Column("guidance_recall", sa.Float(), nullable=False),
        sa.Column("guidance_f1", sa.Float(), nullable=False),
        sa.Column("sentiment_mae", sa.Float(), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("model", sa.String(64), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("eval_results")
    op.drop_table("backtest_runs")
    op.drop_index("ix_signals_active_from", table_name="signals")
    op.drop_index("ix_signals_ticker", table_name="signals")
    op.drop_table("signals")
    op.drop_index("ix_prices_ticker_date", table_name="prices")
    op.drop_table("prices")
    op.drop_index("ix_extractions_filing_id", table_name="extractions")
    op.drop_table("extractions")
    op.drop_index("ix_filings_filed_at", table_name="filings")
    op.drop_index("ix_filings_ticker", table_name="filings")
    op.drop_index("ix_filings_cik", table_name="filings")
    op.drop_table("filings")
    op.execute("DROP TYPE section_mode")
    op.execute("DROP TYPE guidance")
