"""Evaluation metrics for window-level anomaly detection.

**Evaluation is window-wise (point-wise on the windowed series).** Each ~10 s
window is one labelled example; a detector emits one score / one 0-1 decision
per window. This matches how the live path will work: score each telemetry
window as it arrives.

Reported metrics and why each matters:

* **precision** — of the windows we flagged, how many were truly anomalous.
  Low precision = alert fatigue.
* **recall** (a.k.a. TPR / anomaly coverage) — of the truly anomalous windows,
  how many we caught.
* **F1** — harmonic mean; the headline single number given class imbalance.
* **false positive rate** (FPR) — flagged / all-normal. Drives the false-alarm
  budget.
* **false negative rate** (FNR = 1 - recall) — missed anomalies.
* **PR-AUC** (average precision) — threshold-free quality on the score, robust
  to imbalance (preferred over ROC-AUC here).
* **confusion matrix** — the raw tp/fp/fn/tn.

Accuracy is deliberately *not* headlined: with ~20-35% anomalies a "always
normal" model already scores 0.65-0.8.

Event-wise metrics group contiguous anomalous windows into *events* and ask:
did we detect the event at all, and how many windows late? Plus false alarms
per hour during normal operation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.metrics import average_precision_score, roc_auc_score


@dataclass
class Confusion:
    tp: int
    fp: int
    fn: int
    tn: int

    def to_dict(self) -> dict[str, int]:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "tn": self.tn}


def _confusion(y_true: np.ndarray, y_pred: np.ndarray) -> Confusion:
    return Confusion(
        tp=int(((y_pred == 1) & (y_true == 1)).sum()),
        fp=int(((y_pred == 1) & (y_true == 0)).sum()),
        fn=int(((y_pred == 0) & (y_true == 1)).sum()),
        tn=int(((y_pred == 0) & (y_true == 0)).sum()),
    )


def pointwise_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, scores: np.ndarray | None = None
) -> dict[str, Any]:
    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    c = _confusion(y_true, y_pred)

    precision = c.tp / (c.tp + c.fp) if c.tp + c.fp else 0.0
    recall = c.tp / (c.tp + c.fn) if c.tp + c.fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = c.fp / (c.fp + c.tn) if c.fp + c.tn else 0.0
    fnr = c.fn / (c.fn + c.tp) if c.fn + c.tp else 0.0
    accuracy = (c.tp + c.tn) / len(y_true) if len(y_true) else 0.0

    out: dict[str, Any] = {
        "n": len(y_true),
        "n_anomaly": int(y_true.sum()),
        "anomaly_fraction": round(float(y_true.mean()), 4) if len(y_true) else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "false_positive_rate": round(fpr, 4),
        "false_negative_rate": round(fnr, 4),
        "accuracy": round(accuracy, 4),
        "confusion_matrix": c.to_dict(),
    }
    if scores is not None and len(np.unique(y_true)) == 2:
        s = np.asarray(scores, dtype=float)
        out["pr_auc"] = round(float(average_precision_score(y_true, s)), 4)
        out["roc_auc"] = round(float(roc_auc_score(y_true, s)), 4)
    return out


@dataclass
class Event:
    start: int
    end: int  # inclusive index
    detected: bool = False
    detection_delay: int | None = None


def _events(y_true: np.ndarray) -> list[Event]:
    events: list[Event] = []
    i = 0
    n = len(y_true)
    while i < n:
        if y_true[i] == 1:
            j = i
            while j + 1 < n and y_true[j + 1] == 1:
                j += 1
            events.append(Event(start=i, end=j))
            i = j + 1
        else:
            i += 1
    return events


def event_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    window_seconds: float = 10.0,
) -> dict[str, Any]:
    """Group true anomalies into contiguous events; report detection coverage,
    mean detection delay (in windows and seconds), and false alarms per hour of
    normal operation."""

    y_true = np.asarray(y_true, dtype=int)
    y_pred = np.asarray(y_pred, dtype=int)
    events = _events(y_true)

    delays: list[int] = []
    for ev in events:
        fired = np.where(y_pred[ev.start : ev.end + 1] == 1)[0]
        if fired.size:
            ev.detected = True
            ev.detection_delay = int(fired[0])
            delays.append(ev.detection_delay)

    detected = sum(e.detected for e in events)
    normal_windows = int((y_true == 0).sum())
    false_alarm_windows = int(((y_pred == 1) & (y_true == 0)).sum())
    normal_hours = normal_windows * window_seconds / 3600.0

    return {
        "n_events": len(events),
        "events_detected": detected,
        "event_recall": round(detected / len(events), 4) if events else 0.0,
        "mean_detection_delay_windows": round(float(np.mean(delays)), 2) if delays else None,
        "mean_detection_delay_seconds": (
            round(float(np.mean(delays)) * window_seconds, 1) if delays else None
        ),
        "max_detection_delay_windows": int(max(delays)) if delays else None,
        "false_alarm_windows": false_alarm_windows,
        "false_alarms_per_hour_normal": (
            round(false_alarm_windows / normal_hours, 2) if normal_hours > 0 else None
        ),
    }


@dataclass
class EvaluationResult:
    pointwise: dict[str, Any]
    eventwise: dict[str, Any]
    threshold: float
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "pointwise": self.pointwise,
            "eventwise": self.eventwise,
            **({"extra": self.extra} if self.extra else {}),
        }


def evaluate(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    scores: np.ndarray,
    *,
    threshold: float,
    window_seconds: float = 10.0,
) -> EvaluationResult:
    return EvaluationResult(
        pointwise=pointwise_metrics(y_true, y_pred, scores),
        eventwise=event_metrics(y_true, y_pred, window_seconds=window_seconds),
        threshold=round(float(threshold), 6),
    )
