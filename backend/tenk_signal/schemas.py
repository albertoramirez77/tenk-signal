"""Pydantic request/response models.

The Extraction model is the load-bearing one: its JSON schema is what we
pass to Anthropic's output_config.format. The wire schema and the Python
validator can't drift because the schema is derived from the model.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------------------
# The extraction contract (Anthropic structured output schema source of truth)
# ---------------------------------------------------------------------------


class Extraction(BaseModel):
    """What the LLM emits per filing. Validated twice: once by Anthropic's
    constrained decoder via output_config.format, once by Pydantic here."""

    model_config = ConfigDict(extra="forbid")

    sentiment: Annotated[float, Field(ge=-1, le=1)]
    guidance: Literal["raised", "maintained", "lowered"]
    risk_flag_count: Annotated[int, Field(ge=0, le=200)]
    confidence: Annotated[float, Field(ge=0, le=1)]
    rationale: Annotated[str, Field(min_length=1, max_length=2000)]


# ---------------------------------------------------------------------------
# Ingest / extract requests
# ---------------------------------------------------------------------------

TickerStr = Annotated[str, Field(pattern=r"^[A-Z][A-Z0-9.\-]{0,9}$", max_length=10)]
FormType = Literal["10-K", "10-Q"]


def _default_forms() -> list[FormType]:
    return ["10-K", "10-Q"]


class IngestRequest(BaseModel):
    tickers: list[TickerStr] = Field(min_length=1, max_length=40)
    forms: list[FormType] = Field(default_factory=_default_forms)
    limit_per_ticker: Annotated[int, Field(ge=1, le=10)] = 2


class IngestResult(BaseModel):
    filings_ingested: int
    filings_skipped_existing: int
    prices_rows_upserted: int


class ExtractRequest(BaseModel):
    # Either a specific filing or "every un-extracted filing".
    filing_id: int | None = None
    all_pending: bool = False


class ExtractResult(BaseModel):
    extracted: int
    cached: int  # hit on UNIQUE(text_sha256, prompt_version, model)
    failed: int


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------


class BacktestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    horizon_days: Annotated[int, Field(ge=1, le=60)] = 5
    execution_lag_days: Annotated[int, Field(ge=0, le=10)] = 1
    transaction_cost_bps: Annotated[float, Field(ge=0, le=100)] = 5.0
    benchmark: TickerStr = "SPY"
    walk_forward: bool = False
    walk_forward_windows: Annotated[int, Field(ge=2, le=20)] = 5


class WalkForwardWindow(BaseModel):
    """Per-window metrics from walk-forward mode."""

    index: int
    start: dt.date
    end: dt.date
    n_positions: int
    hit_rate: float | None
    mean_ret: float | None
    sharpe: float | None


class BacktestSummary(BaseModel):
    id: int
    created_at: dt.datetime
    config: BacktestConfig
    hit_rate: float | None
    mean_ret: float | None
    vol: float | None
    sharpe: float | None


class EquityPoint(BaseModel):
    date: dt.date
    equity: float


class BacktestDetail(BacktestSummary):
    walk_forward_windows: list[WalkForwardWindow] = []
    equity_curve: list[EquityPoint]


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


class SignalRow(BaseModel):
    id: int
    ticker: str
    filed_at: dt.datetime
    active_from: dt.datetime
    signal_value: float
    guidance: Literal["raised", "maintained", "lowered"]
    sentiment: float
    confidence: float
    quarantined: bool


class SignalsResponse(BaseModel):
    rows: list[SignalRow]


class EvalSnapshot(BaseModel):
    run_at: dt.datetime
    n: int
    guidance_precision: float
    guidance_recall: float
    guidance_f1: float
    sentiment_mae: float
    prompt_version: str
    model: str


class EvalsResponse(BaseModel):
    latest: EvalSnapshot | None
    history: list[EvalSnapshot]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extraction_json_schema() -> dict[str, Any]:
    """The JSON schema we hand to Anthropic's output_config.format.

    Anthropic's structured-output validator wants ``additionalProperties:
    false`` and ``type: object``; Pydantic v2 emits both when ``extra:
    forbid`` is set on the model.
    """
    schema = Extraction.model_json_schema()
    # Anthropic expects "additionalProperties": false at the top level.
    schema.setdefault("additionalProperties", False)
    return schema


# Stand-alone validator usable by both the live and fake extractors.
class ExtractionEnvelope(BaseModel):
    """Wraps an Extraction with model + prompt_version for the cache key.

    The extractor service hydrates this from Anthropic's parsed response.
    """

    extraction: Extraction
    model: str
    prompt_version: str

    @field_validator("model", "prompt_version")
    @classmethod
    def _nonempty(cls, v: str) -> str:
        if not v:
            raise ValueError("must be non-empty")
        return v
