"""Eval system: compare current extractions against hand-labeled ground truth.

Metrics (per PLAN.md §7):
- ``guidance``: macro-averaged precision, recall, F1 over the 3 classes
  (raised / maintained / lowered).
- ``sentiment``: mean absolute error (MAE) over numeric labels.

Ground truth lives in ``data/ground_truth.jsonl`` — one JSON per line with
``text_sha256``, ``true_guidance``, ``true_sentiment``. Records that don't
match any extraction in the DB are reported as "missing".

NOTE: the ground-truth file shipped in this repo contains *placeholder*
labels derived programmatically from the fixture content. Replace with
real human labels before relying on these metrics for any decision —
see ``data/ground_truth.jsonl`` header comment.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tenk_signal.logging import get_logger
from tenk_signal.models import EvalResult, Extraction

log = get_logger(__name__)

DEFAULT_GT_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "ground_truth.jsonl"
_LABELS = ("raised", "maintained", "lowered")


@dataclass(frozen=True, slots=True)
class GroundTruth:
    text_sha256: str
    true_guidance: str
    true_sentiment: float


@dataclass(frozen=True, slots=True)
class EvalSummary:
    n: int
    n_missing: int
    guidance_precision: float
    guidance_recall: float
    guidance_f1: float
    sentiment_mae: float
    per_class_f1: dict[str, float]


def load_ground_truth(path: Path = DEFAULT_GT_PATH) -> list[GroundTruth]:
    """Parse the JSONL file. Returns an empty list if the file is absent."""
    if not path.exists():
        log.warning("evals.no_ground_truth", path=str(path))
        return []
    rows: list[GroundTruth] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        obj = json.loads(line)
        rows.append(
            GroundTruth(
                text_sha256=obj["text_sha256"],
                true_guidance=obj["true_guidance"],
                true_sentiment=float(obj["true_sentiment"]),
            )
        )
    return rows


# ---------------------------------------------------------------------------
# Metric math (pure, deterministic, unit-testable)
# ---------------------------------------------------------------------------


def _per_class_precision_recall_f1(
    y_true: list[str], y_pred: list[str], label: str
) -> tuple[float, float, float]:
    tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p == label)
    fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != label and p == label)
    fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == label and p != label)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def compute_metrics(
    y_true_guidance: list[str],
    y_pred_guidance: list[str],
    y_true_sentiment: list[float],
    y_pred_sentiment: list[float],
) -> tuple[float, float, float, float, dict[str, float]]:
    """Return (macro_p, macro_r, macro_f1, sentiment_mae, per_class_f1)."""
    assert len(y_true_guidance) == len(y_pred_guidance)
    assert len(y_true_sentiment) == len(y_pred_sentiment)
    per_class: dict[str, tuple[float, float, float]] = {}
    for label in _LABELS:
        per_class[label] = _per_class_precision_recall_f1(y_true_guidance, y_pred_guidance, label)
    macro_p = sum(v[0] for v in per_class.values()) / len(_LABELS)
    macro_r = sum(v[1] for v in per_class.values()) / len(_LABELS)
    macro_f1 = sum(v[2] for v in per_class.values()) / len(_LABELS)
    mae = sum(abs(a - b) for a, b in zip(y_true_sentiment, y_pred_sentiment, strict=True)) / max(
        len(y_true_sentiment), 1
    )
    return macro_p, macro_r, macro_f1, mae, {k: v[2] for k, v in per_class.items()}


# ---------------------------------------------------------------------------
# DB-driven eval
# ---------------------------------------------------------------------------


async def run_evals(
    session: AsyncSession,
    *,
    model: str,
    prompt_version: str,
    ground_truth: list[GroundTruth] | None = None,
) -> EvalSummary:
    """Pull extractions matching (model, prompt_version), align with ground
    truth by text_sha256, compute metrics, persist an ``eval_results`` row.
    """
    gt = ground_truth if ground_truth is not None else load_ground_truth()
    if not gt:
        log.warning("evals.empty_ground_truth")
        return EvalSummary(0, 0, 0.0, 0.0, 0.0, 0.0, {k: 0.0 for k in _LABELS})

    shas = [g.text_sha256 for g in gt]
    rows = (
        (
            await session.execute(
                select(Extraction).where(
                    Extraction.text_sha256.in_(shas),
                    Extraction.model == model,
                    Extraction.prompt_version == prompt_version,
                )
            )
        )
        .scalars()
        .all()
    )
    by_sha = {r.text_sha256: r for r in rows}

    yt_g: list[str] = []
    yp_g: list[str] = []
    yt_s: list[float] = []
    yp_s: list[float] = []
    missing = 0
    for g in gt:
        ex = by_sha.get(g.text_sha256)
        if ex is None:
            missing += 1
            continue
        yt_g.append(g.true_guidance)
        yp_g.append(ex.guidance.value)
        yt_s.append(g.true_sentiment)
        yp_s.append(ex.sentiment)

    if not yt_g:
        log.warning("evals.no_overlap_between_gt_and_extractions")
        return EvalSummary(0, missing, 0.0, 0.0, 0.0, 0.0, {k: 0.0 for k in _LABELS})

    macro_p, macro_r, macro_f1, mae, per_class = compute_metrics(yt_g, yp_g, yt_s, yp_s)

    session.add(
        EvalResult(
            n=len(yt_g),
            guidance_precision=macro_p,
            guidance_recall=macro_r,
            guidance_f1=macro_f1,
            sentiment_mae=mae,
            prompt_version=prompt_version,
            model=model,
        )
    )
    await session.flush()

    return EvalSummary(
        n=len(yt_g),
        n_missing=missing,
        guidance_precision=macro_p,
        guidance_recall=macro_r,
        guidance_f1=macro_f1,
        sentiment_mae=mae,
        per_class_f1=per_class,
    )
