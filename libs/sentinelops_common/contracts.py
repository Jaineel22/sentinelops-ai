"""Versioned payload contracts for cross-service SentinelOps events.

Each class is the frozen shape of one ``(event_type, event_version)`` pair.
Producers build these; consumers validate against them. Adding an optional field
is backward-compatible; anything else needs a new version (see
docs/architecture/events.md).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

# --- anomaly.detected v1 --------------------------------------------------
ANOMALY_DETECTED = "anomaly.detected"
ANOMALY_DETECTED_VERSION = 1


class AnomalyDetectedV1(BaseModel):
    """Emitted by ``anomaly-detector`` for each telemetry window it scores as
    anomalous."""

    detector: str  # e.g. "isolation_forest"
    detector_version: str  # ml package version the model was trained with
    service: str  # the service the telemetry describes, e.g. "orders-service"
    environment: str  # "development" | "staging" | "production"
    window_start: str  # RFC 3339 UTC
    window_end: str  # RFC 3339 UTC
    anomaly_score: float
    threshold: float
    is_anomaly: bool
    # The per-window operational signals (ml.data.schema.SIGNAL_COLUMNS).
    signals: dict[str, float]
    # Coarse deterministic triage: which signals are outside their normal band.
    abnormal_signals: list[str] = Field(default_factory=list)


# --- incident lifecycle v1 --------------------------------------------
INCIDENT_OPENED = "incident.opened"
INCIDENT_UPDATED = "incident.updated"
INCIDENT_RESOLVED = "incident.resolved"
INCIDENT_LIFECYCLE_VERSION = 1


class IncidentLifecycleV1(BaseModel):
    """A best-effort notification that an incident changed. The Incident API /
    database is authoritative; this stream is a wake-up for Phase 4."""

    incident_id: str
    correlation_key: str
    service: str
    environment: str
    status: str
    severity: str
    anomaly_count: int
    title: str
    started_at: str
    updated_at: str
    change: str  # "opened" | "evidence-added" | "severity-changed" | "resolved" | "<from>-><to>"
