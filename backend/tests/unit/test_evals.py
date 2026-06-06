"""Eval metric math + ground-truth loading.

DB-driven evaluation runs in the e2e suite; this file covers the pure
metric logic so any regression in P/R/F1 or MAE is caught fast.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tenk_signal.services.evals import (
    DEFAULT_GT_PATH,
    _per_class_precision_recall_f1,
    compute_metrics,
    load_ground_truth,
)

# ---------------------------------------------------------------------------
# precision / recall / F1
# ---------------------------------------------------------------------------


def test_perfect_predictions_yield_f1_1() -> None:
    y_t = ["raised", "lowered", "maintained", "raised"]
    y_p = ["raised", "lowered", "maintained", "raised"]
    macro_p, macro_r, macro_f1, mae, _ = compute_metrics(
        y_t, y_p, [0.0, 0.0, 0.0, 0.0], [0.0, 0.0, 0.0, 0.0]
    )
    assert macro_p == 1.0
    assert macro_r == 1.0
    assert macro_f1 == 1.0
    assert mae == 0.0


def test_all_wrong_yields_zero_f1() -> None:
    y_t = ["raised", "lowered", "maintained"]
    y_p = ["lowered", "raised", "lowered"]
    _, _, macro_f1, _, _ = compute_metrics(y_t, y_p, [0.0, 0.0, 0.0], [0.0, 0.0, 0.0])
    assert macro_f1 == 0.0


def test_one_class_perfect_others_zero_macro_average() -> None:
    y_t = ["raised", "raised", "lowered", "lowered"]
    y_p = ["raised", "raised", "raised", "raised"]
    # raised: precision 2/4=0.5, recall 2/2=1.0, f1=2/3
    # lowered: precision 0/0=0, recall 0/2=0, f1=0
    # maintained: precision 0/0=0, recall 0/0=0, f1=0
    # macro_f1 = (2/3 + 0 + 0) / 3 = 2/9
    _, _, macro_f1, _, _ = compute_metrics(y_t, y_p, [0.0] * 4, [0.0] * 4)
    assert pytest.approx(macro_f1, abs=1e-6) == 2 / 9


def test_per_class_precision_recall_specific() -> None:
    y_t = ["raised", "lowered", "maintained", "raised", "lowered"]
    y_p = ["raised", "lowered", "raised", "raised", "maintained"]
    # For 'raised': tp=2 (positions 0, 3), fp=1 (position 2), fn=0
    # precision = 2/3, recall = 2/2 = 1, f1 = 2*(2/3)*1/(2/3+1) = 4/5
    p, r, f1 = _per_class_precision_recall_f1(y_t, y_p, "raised")
    assert pytest.approx(p, abs=1e-6) == 2 / 3
    assert pytest.approx(r, abs=1e-6) == 1.0
    assert pytest.approx(f1, abs=1e-6) == 4 / 5


def test_sentiment_mae() -> None:
    y_t = [0.5, -0.3, 0.0, 0.8]
    y_p = [0.4, -0.1, 0.05, 0.7]
    _, _, _, mae, _ = compute_metrics(
        ["raised", "raised", "raised", "raised"], ["raised"] * 4, y_t, y_p
    )
    # |0.5-0.4| + |-0.3+0.1| + |0-0.05| + |0.8-0.7| = 0.1+0.2+0.05+0.1 = 0.45
    # mean = 0.45/4 = 0.1125
    assert pytest.approx(mae, abs=1e-6) == 0.1125


def test_empty_inputs_dont_crash() -> None:
    p, r, f1, mae, per_class = compute_metrics([], [], [], [])
    assert p == r == f1 == 0.0
    assert mae == 0.0
    assert set(per_class.keys()) == {"raised", "maintained", "lowered"}


# ---------------------------------------------------------------------------
# ground-truth loader
# ---------------------------------------------------------------------------


def test_load_ground_truth_from_file() -> None:
    """Verify the shipped placeholder file loads cleanly and has the expected
    structure. The class balance is set to be non-degenerate."""
    gt = load_ground_truth(DEFAULT_GT_PATH)
    # AAPL + 5 real fixtures = 6 records.
    assert len(gt) == 6
    classes = {g.true_guidance for g in gt}
    assert classes <= {"raised", "maintained", "lowered"}
    # All three classes represented in the placeholder set.
    assert classes == {"raised", "maintained", "lowered"}
    # Sentiment values in valid range.
    assert all(-1.0 <= g.true_sentiment <= 1.0 for g in gt)


def test_load_returns_empty_when_file_absent() -> None:
    gt = load_ground_truth(Path("/nonexistent/path.jsonl"))
    assert gt == []


def test_load_skips_comment_lines() -> None:
    """The shipped file starts with '#' comments. The loader must skip them."""
    gt = load_ground_truth(DEFAULT_GT_PATH)
    # If comments were parsed, we'd hit a JSONDecodeError.
    assert len(gt) > 0
