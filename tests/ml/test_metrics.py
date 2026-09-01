"""Evaluation metrics: point-wise and event-wise."""

from __future__ import annotations

import numpy as np
from ml.evaluation.metrics import evaluate, event_metrics, pointwise_metrics


def test_pointwise_on_known_confusion() -> None:
    y_true = np.array([0, 0, 1, 1, 1, 0, 1, 0])
    y_pred = np.array([0, 1, 1, 1, 0, 0, 0, 0])
    m = pointwise_metrics(y_true, y_pred)
    assert m["confusion_matrix"] == {"tp": 2, "fp": 1, "fn": 2, "tn": 3}
    assert m["precision"] == round(2 / 3, 4)
    assert m["recall"] == round(2 / 4, 4)
    assert m["false_positive_rate"] == round(1 / 4, 4)
    assert m["false_negative_rate"] == round(2 / 4, 4)


def test_pr_auc_present_with_scores() -> None:
    y_true = np.array([0, 0, 0, 1, 1, 1])
    scores = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    m = pointwise_metrics(y_true, (scores > 0.5).astype(int), scores)
    assert m["pr_auc"] == 1.0
    assert m["roc_auc"] == 1.0


def test_perfect_and_empty_predictions() -> None:
    y = np.array([0, 1, 0, 1])
    assert pointwise_metrics(y, y)["f1"] == 1.0
    zeros = pointwise_metrics(y, np.zeros_like(y))
    assert zeros["recall"] == 0.0
    assert zeros["precision"] == 0.0


def test_event_grouping_and_detection_delay() -> None:
    # two events: windows 2-4 and 7-8
    y_true = np.array([0, 0, 1, 1, 1, 0, 0, 1, 1, 0])
    # event 1 detected one window late; event 2 missed entirely
    y_pred = np.array([0, 0, 0, 1, 0, 0, 0, 0, 0, 0])
    ev = event_metrics(y_true, y_pred, window_seconds=10.0)
    assert ev["n_events"] == 2
    assert ev["events_detected"] == 1
    assert ev["event_recall"] == 0.5
    assert ev["mean_detection_delay_windows"] == 1.0
    assert ev["mean_detection_delay_seconds"] == 10.0


def test_false_alarms_per_hour() -> None:
    y_true = np.zeros(360, dtype=int)  # 1 hour of 10s normal windows
    y_pred = np.zeros(360, dtype=int)
    y_pred[[10, 20, 30]] = 1
    ev = event_metrics(y_true, y_pred, window_seconds=10.0)
    assert ev["false_alarm_windows"] == 3
    assert ev["false_alarms_per_hour_normal"] == 3.0


def test_evaluate_bundles_both() -> None:
    y_true = np.array([0, 0, 1, 1, 0, 1])
    scores = np.array([0.1, 0.2, 0.9, 0.8, 0.3, 0.7])
    res = evaluate(y_true, (scores > 0.5).astype(int), scores, threshold=0.5, window_seconds=10)
    assert "pointwise" in res.to_dict()
    assert "eventwise" in res.to_dict()
