"""OpenTelemetry instruments for the incident-correlator (ADR-007 conventions).

Low-cardinality labels only — never ``incident_id`` / ``event_id`` / ``trace_id``
on a metric (those live on spans and logs).
"""

from __future__ import annotations

from functools import lru_cache

from opentelemetry.metrics import Counter, Histogram, UpDownCounter

from incident_correlator.processor import ProcessResult
from sentinelops_common.obs import get_meter


class CorrelatorMetrics:
    def __init__(self) -> None:
        meter = get_meter()
        self.anomalies_processed: Counter = meter.create_counter(
            "incident.anomalies.processed", unit="1", description="Anomaly events consumed."
        )
        self.anomalies_rejected: Counter = meter.create_counter(
            "incident.anomalies.rejected", unit="1", description="Anomaly events sent to the DLQ."
        )
        self.duplicate_anomalies: Counter = meter.create_counter(
            "incident.anomalies.duplicate", unit="1", description="Redelivered events skipped."
        )
        self.incidents_created: Counter = meter.create_counter(
            "incident.created", unit="1", description="Incidents opened."
        )
        self.incidents_updated: Counter = meter.create_counter(
            "incident.updated", unit="1", description="Incidents that gained evidence."
        )
        self.incidents_resolved: Counter = meter.create_counter(
            "incident.resolved", unit="1", description="Incidents resolved, by resolution."
        )
        self.correlation_failures: Counter = meter.create_counter(
            "incident.correlation.failures",
            unit="1",
            description="Correlation attempts that errored.",
        )
        self.processing_latency: Histogram = meter.create_histogram(
            "incident.processing.duration",
            unit="s",
            description="Time to process one anomaly event end to end.",
        )
        # Approximate live count (best-effort; resets on restart).
        self.active_incidents: UpDownCounter = meter.create_up_down_counter(
            "incident.active", unit="1", description="Incidents currently not RESOLVED."
        )

    def record_outcome(self, result: ProcessResult) -> None:
        self.anomalies_processed.add(1, {"outcome": result.value})
        if result is ProcessResult.CREATED:
            self.incidents_created.add(1)
            self.active_incidents.add(1)
        elif result is ProcessResult.APPENDED:
            self.incidents_updated.add(1)
        elif result is ProcessResult.SUPERSEDED:
            self.incidents_created.add(1)
            self.incidents_resolved.add(1, {"resolution": "auto:stale"})
            # net zero: one opened, one auto-resolved
        elif result is ProcessResult.DUPLICATE:
            self.duplicate_anomalies.add(1)


@lru_cache
def get_metrics() -> CorrelatorMetrics:
    return CorrelatorMetrics()
