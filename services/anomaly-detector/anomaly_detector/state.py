"""In-process inference statistics for the ``/ready`` health view (Phase 7C).

OTel instruments (:mod:`anomaly_detector.metrics`) remain the system of record
for dashboards and alerting. This is a small, bounded, thread-safe summary the
detector can serve inline on ``/ready`` for a human eyeballing one instance:
counts, an exponential moving average of inference latency, min/max, and when the
last inference happened. No per-inference history is retained.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Any

# Weight of the newest sample in the latency moving average. 0.2 -> the average
# tracks recent behaviour without a single slow score dominating it.
_EMA_ALPHA = 0.2


class DetectorState:
    """Mutable, lock-guarded rollup of what the scoring loop has done so far."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.startup_time: datetime = datetime.now(tz=UTC)
        self.inference_count: int = 0
        self.anomaly_count: int = 0
        self.last_inference_latency_ms: float = 0.0
        self.avg_inference_latency_ms: float = 0.0
        self.min_inference_latency_ms: float = 0.0
        self.max_inference_latency_ms: float = 0.0
        self.last_inference_time: datetime | None = None

    def record_inference(self, latency_seconds: float, is_anomaly: bool) -> None:
        """Fold one inference into the rollup."""

        latency_ms = latency_seconds * 1000.0
        with self._lock:
            self.inference_count += 1
            if is_anomaly:
                self.anomaly_count += 1
            self.last_inference_latency_ms = latency_ms
            if self.inference_count == 1:
                self.avg_inference_latency_ms = latency_ms
                self.min_inference_latency_ms = latency_ms
                self.max_inference_latency_ms = latency_ms
            else:
                self.avg_inference_latency_ms = (
                    _EMA_ALPHA * latency_ms + (1.0 - _EMA_ALPHA) * self.avg_inference_latency_ms
                )
                self.min_inference_latency_ms = min(self.min_inference_latency_ms, latency_ms)
                self.max_inference_latency_ms = max(self.max_inference_latency_ms, latency_ms)
            self.last_inference_time = datetime.now(tz=UTC)

    def uptime_seconds(self) -> float:
        return (datetime.now(tz=UTC) - self.startup_time).total_seconds()

    def seconds_since_last_inference(self) -> float | None:
        with self._lock:
            if self.last_inference_time is None:
                return None
            return (datetime.now(tz=UTC) - self.last_inference_time).total_seconds()

    def get_summary(self) -> dict[str, Any]:
        """A JSON-serialisable snapshot of the rollup."""

        with self._lock:
            total = self.inference_count
            anomaly_rate = round(100.0 * self.anomaly_count / total, 2) if total else 0.0
            last_time = self.last_inference_time
            return {
                "total_inferences": total,
                "total_anomalies": self.anomaly_count,
                "anomaly_rate": anomaly_rate,
                "avg_latency_ms": round(self.avg_inference_latency_ms, 3),
                "last_latency_ms": round(self.last_inference_latency_ms, 3),
                "min_latency_ms": round(self.min_inference_latency_ms, 3),
                "max_latency_ms": round(self.max_inference_latency_ms, 3),
                "last_inference_time": last_time.isoformat() if last_time is not None else None,
            }

    def reset(self) -> None:
        """Zero every counter (test helper; the startup time is preserved)."""

        with self._lock:
            self.inference_count = 0
            self.anomaly_count = 0
            self.last_inference_latency_ms = 0.0
            self.avg_inference_latency_ms = 0.0
            self.min_inference_latency_ms = 0.0
            self.max_inference_latency_ms = 0.0
            self.last_inference_time = None


def assess_health(
    summary: dict[str, Any],
    *,
    uptime_seconds: float,
    seconds_since_last_inference: float | None,
    max_idle_seconds: float,
    max_anomaly_rate: float,
    max_avg_latency_ms: float,
) -> tuple[bool, list[str]]:
    """Turn a summary + thresholds into ``(healthy, reasons)``.

    ``reasons`` is empty when healthy; otherwise each entry names one tripped
    threshold. This is *degradation* reporting — it does not gate the HTTP status
    of ``/ready`` (that stays tied to the scoring loop being alive).
    """

    reasons: list[str] = []

    idle_for = seconds_since_last_inference
    if summary["total_inferences"] == 0:
        if uptime_seconds > max_idle_seconds:
            reasons.append(f"no inference {uptime_seconds:.0f}s after startup")
    elif idle_for is not None and idle_for > max_idle_seconds:
        reasons.append(f"last inference {idle_for:.0f}s ago")

    if summary["total_inferences"] > 0 and summary["anomaly_rate"] > max_anomaly_rate:
        reasons.append(f"anomaly rate {summary['anomaly_rate']}% > {max_anomaly_rate}%")

    if summary["avg_latency_ms"] > max_avg_latency_ms:
        reasons.append(f"avg latency {summary['avg_latency_ms']}ms > {max_avg_latency_ms}ms")

    return (not reasons, reasons)
