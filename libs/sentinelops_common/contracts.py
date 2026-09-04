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
    # Detection-latency breakdown (Phase 7B), milliseconds, best-effort. Present
    # only when the detector captured a full timeline for this window; for
    # downstream debugging, not correlation logic.
    detection_latency_ms: float | None = None  # window close -> anomaly publish
    scrape_latency_ms: float | None = None  # window close -> scrape
    inference_latency_ms: float | None = None  # model scoring duration


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


# --- remediation lifecycle v1 (Phase 5G) ----------------------------
REMEDIATION_LIFECYCLE_VERSION = 1

# The closed set of remediation lifecycle ``event_type`` values. One value per
# meaningful, *committed* domain fact — a 1:1 mirror of the Phase 5E/5F
# append-only audit trail (minus the internal ``EXECUTION_REQUESTED`` note).
# The remediation-controller database + audit trail remain authoritative; this
# stream is a best-effort notification, published after the transaction commits
# (ADR-030, same pattern as ``incident.events`` / ADR-016).
REMEDIATION_PROPOSED = "remediation.proposed"
REMEDIATION_POLICY_EVALUATED = "remediation.policy_evaluated"
REMEDIATION_BLOCKED = "remediation.blocked"
REMEDIATION_APPROVED = "remediation.approved"
REMEDIATION_REJECTED = "remediation.rejected"
REMEDIATION_EXECUTION_STARTED = "remediation.execution_started"
REMEDIATION_EXECUTION_SUCCEEDED = "remediation.execution_succeeded"
REMEDIATION_EXECUTION_FAILED = "remediation.execution_failed"
REMEDIATION_RECOVERY_VERIFICATION_STARTED = "remediation.recovery_verification_started"
REMEDIATION_RECOVERED = "remediation.recovered"
REMEDIATION_RECOVERY_FAILED = "remediation.recovery_failed"

REMEDIATION_LIFECYCLE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        REMEDIATION_PROPOSED,
        REMEDIATION_POLICY_EVALUATED,
        REMEDIATION_BLOCKED,
        REMEDIATION_APPROVED,
        REMEDIATION_REJECTED,
        REMEDIATION_EXECUTION_STARTED,
        REMEDIATION_EXECUTION_SUCCEEDED,
        REMEDIATION_EXECUTION_FAILED,
        REMEDIATION_RECOVERY_VERIFICATION_STARTED,
        REMEDIATION_RECOVERED,
        REMEDIATION_RECOVERY_FAILED,
    }
)


class RemediationLifecycleV1(BaseModel):
    """A best-effort notification that a remediation's lifecycle advanced.

    The remediation-controller's PostgreSQL state + append-only audit trail are
    authoritative (ADR-026, ADR-028); this payload carries **safe structured
    metadata only** — ids, closed-enum labels, timestamps, and text that has
    already passed the Phase 5E redaction boundary. There is deliberately **no
    field that can carry a command, script, shell string, URL, or credential**;
    a consumer can never turn one of these events into an action.
    """

    remediation_id: str
    incident_id: str
    investigation_id: str | None = None

    # The lifecycle fact, e.g. "proposed" / "approved" / "execution_succeeded".
    # Equals the envelope ``event_type`` with the ``remediation.`` prefix removed.
    change: str

    previous_state: str | None = None
    new_state: str | None = None

    action_type: str | None = None
    target_service: str | None = None
    target_environment: str | None = None
    trigger: str | None = None
    risk_level: str | None = None

    # Actor: "SYSTEM" for the deterministic mapping / policy engine / executor /
    # verifier; "HUMAN" for an approver. ``actor_id`` is redacted + capped.
    actor_type: str | None = None
    actor_id: str | None = None
    actor_role: str | None = None

    policy_outcome: str | None = None
    policy_version: str | None = None
    policy_reason_codes: list[str] = Field(default_factory=list)

    execution_id: str | None = None
    execution_result: str | None = None  # "STARTED" | "SUCCEEDED" | "FAILED"

    verification_id: str | None = None
    verification_attempts: int | None = None
    checks_passed: int | None = None
    checks_total: int | None = None
    failure_reason: str | None = None  # redacted

    # Short, redacted human-facing note from the audit event.
    reason: str = ""
    # The audit-trail row this event mirrors (stable correlation to the DB).
    audit_id: str | None = None
    # Caller ``x-request-id``, echoed through — informational only.
    correlation_id: str | None = None

    occurred_at: str  # RFC 3339 UTC
