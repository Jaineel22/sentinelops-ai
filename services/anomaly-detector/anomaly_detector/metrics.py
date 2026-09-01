"""OpenTelemetry instruments for the anomaly-detector (low-cardinality labels)."""

from __future__ import annotations

from functools import lru_cache

from opentelemetry.metrics import Counter, Histogram

from sentinelops_common.obs import get_meter


class DetectorMetrics:
    def __init__(self) -> None:
        meter = get_meter()
        self.scrapes: Counter = meter.create_counter(
            "detector.scrapes", unit="1", description="Target /metrics scrapes, by outcome."
        )
        self.windows_scored: Counter = meter.create_counter(
            "detector.windows.scored", unit="1", description="Telemetry windows scored."
        )
        self.anomalies_published: Counter = meter.create_counter(
            "detector.anomalies.published",
            unit="1",
            description="anomaly.detected events published.",
        )
        self.publish_failures: Counter = meter.create_counter(
            "detector.publish.failures", unit="1", description="Failed anomaly.detected publishes."
        )
        self.score_duration: Histogram = meter.create_histogram(
            "detector.score.duration", unit="s", description="Time to score one window."
        )


@lru_cache
def get_metrics() -> DetectorMetrics:
    return DetectorMetrics()
