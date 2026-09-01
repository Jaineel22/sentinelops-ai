"""OpenTelemetry instruments for the rca-agent (ADR-007 conventions).

Low-cardinality labels only — never ``incident_id`` / ``investigation_id`` /
``event_id`` / ``trace_id`` on a metric (those live on spans and structured
logs). Exposed for Prometheus scrape at ``GET /metrics``.
"""

from __future__ import annotations

from functools import lru_cache

from opentelemetry.metrics import Counter, Histogram

from rca_agent.domain import InvestigationStatus
from sentinelops_common.obs import get_meter

# Terminal statuses grouped for the ``investigations.completed`` outcome label.
_OUTCOME = {
    InvestigationStatus.COMPLETED: "completed",
    InvestigationStatus.INSUFFICIENT_EVIDENCE: "insufficient_evidence",
    InvestigationStatus.FAILED: "failed",
    InvestigationStatus.TIMED_OUT: "timed_out",
}


class RcaMetrics:
    def __init__(self) -> None:
        meter = get_meter()
        self.events_consumed: Counter = meter.create_counter(
            "rca.kafka.events.consumed",
            unit="1",
            description="incident lifecycle events read from the topic, by event_type.",
        )
        self.events_rejected: Counter = meter.create_counter(
            "rca.kafka.events.rejected",
            unit="1",
            description="incident.opened events sent to the DLQ, by reason.",
        )
        self.duplicate_events: Counter = meter.create_counter(
            "rca.kafka.events.duplicate",
            unit="1",
            description="incident.opened redeliveries skipped (an investigation already exists).",
        )
        self.investigations_started: Counter = meter.create_counter(
            "rca.investigations.started",
            unit="1",
            description="investigations begun, by trigger (EVENT | MANUAL).",
        )
        self.investigations_completed: Counter = meter.create_counter(
            "rca.investigations.completed",
            unit="1",
            description="investigations that reached a terminal state, by outcome.",
        )
        self.investigation_duration: Histogram = meter.create_histogram(
            "rca.investigations.duration",
            unit="s",
            description="wall-clock time for one investigation graph run.",
        )
        self.api_requests: Counter = meter.create_counter(
            "rca.api.requests",
            unit="1",
            description="investigation API requests, by route and status class.",
        )

    def record_started(self, trigger: str) -> None:
        self.investigations_started.add(1, {"trigger": trigger})

    def record_completed(self, status: InvestigationStatus, *, duration_seconds: float) -> None:
        self.investigations_completed.add(1, {"outcome": _OUTCOME.get(status, "other")})
        self.investigation_duration.record(duration_seconds)


@lru_cache
def get_metrics() -> RcaMetrics:
    return RcaMetrics()
