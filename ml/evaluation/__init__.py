"""Evaluation: point-wise (window-wise) classification metrics plus event-wise
detection metrics (delay, false-alarm rate)."""

from ml.evaluation.metrics import evaluate, event_metrics, pointwise_metrics

__all__ = ["evaluate", "event_metrics", "pointwise_metrics"]
