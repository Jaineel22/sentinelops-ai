"""Remediation lifecycle event translation (Phase 5G).

Outbound only: a committed :class:`~remediation_controller.audit.model.
RemediationAuditEvent` -> a versioned :class:`~sentinelops_common.contracts.
RemediationLifecycleV1` envelope on ``remediation.events``.

The audit event is the source: it is already built from validated domain
objects and every free-text / structured value has already passed the Phase 5E
redaction boundary (:mod:`remediation_controller.audit.redaction`). This module
re-applies :func:`redact_text` defensively (it is idempotent) and never adds a
field that could carry a command, URL, or secret.

There is **no inbound translation** — the remediation-controller consumes no
Kafka topic. A Kafka message is never read back into this service (ADR-030).
"""

from __future__ import annotations

import uuid

from remediation_controller.audit.model import AuditEventType, RemediationAuditEvent
from remediation_controller.audit.redaction import redact_text
from sentinelops_common.contracts import (
    REMEDIATION_APPROVED,
    REMEDIATION_BLOCKED,
    REMEDIATION_EXECUTION_FAILED,
    REMEDIATION_EXECUTION_STARTED,
    REMEDIATION_EXECUTION_SUCCEEDED,
    REMEDIATION_LIFECYCLE_VERSION,
    REMEDIATION_POLICY_EVALUATED,
    REMEDIATION_PROPOSED,
    REMEDIATION_RECOVERED,
    REMEDIATION_RECOVERY_FAILED,
    REMEDIATION_RECOVERY_VERIFICATION_STARTED,
    REMEDIATION_REJECTED,
    RemediationLifecycleV1,
)
from sentinelops_common.events import EventEnvelope

DEFAULT_REMEDIATION_TOPIC = "remediation.events"

# A stable namespace so the same committed audit fact always produces the same
# Kafka ``event_id`` — a consumer keying on ``event_id`` (see
# docs/architecture/events.md) then deduplicates a republish after a restart.
_EVENT_ID_NAMESPACE = uuid.UUID("6f2b1c34-5d6e-4a7b-8c9d-0e1f2a3b4c5d")

# Closed 1:1 map from an auditable lifecycle fact to an envelope ``event_type``.
# ``EXECUTION_REQUESTED`` is intentionally absent — it is an internal pre-check
# note (state ``APPROVED`` -> ``APPROVED``), not a domain lifecycle transition.
_AUDIT_EVENT_TO_TYPE: dict[AuditEventType, str] = {
    AuditEventType.PROPOSAL_CREATED: REMEDIATION_PROPOSED,
    AuditEventType.POLICY_EVALUATED: REMEDIATION_POLICY_EVALUATED,
    AuditEventType.REMEDIATION_BLOCKED: REMEDIATION_BLOCKED,
    AuditEventType.APPROVED: REMEDIATION_APPROVED,
    AuditEventType.REJECTED: REMEDIATION_REJECTED,
    AuditEventType.EXECUTION_STARTED: REMEDIATION_EXECUTION_STARTED,
    AuditEventType.EXECUTION_SUCCEEDED: REMEDIATION_EXECUTION_SUCCEEDED,
    AuditEventType.EXECUTION_FAILED: REMEDIATION_EXECUTION_FAILED,
    AuditEventType.VERIFICATION_STARTED: REMEDIATION_RECOVERY_VERIFICATION_STARTED,
    AuditEventType.VERIFICATION_SUCCEEDED: REMEDIATION_RECOVERED,
    AuditEventType.VERIFICATION_FAILED: REMEDIATION_RECOVERY_FAILED,
}


def is_publishable(event: RemediationAuditEvent) -> bool:
    """Whether this audit fact has a corresponding lifecycle event type."""

    return event.event_type in _AUDIT_EVENT_TO_TYPE


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def lifecycle_envelope(event: RemediationAuditEvent, *, source: str) -> EventEnvelope | None:
    """Build the ``remediation.events`` envelope for one committed audit fact.

    Returns ``None`` for an audit event with no lifecycle mapping (currently only
    ``EXECUTION_REQUESTED``) so the caller can simply skip it.
    """

    event_type = _AUDIT_EVENT_TO_TYPE.get(event.event_type)
    if event_type is None:
        return None

    md = event.metadata
    failure_reason: str | None = None
    if event.event_type is AuditEventType.VERIFICATION_FAILED:
        failure_reason = redact_text(event.reason) if event.reason else None

    payload = RemediationLifecycleV1(
        remediation_id=event.remediation_id,
        incident_id=event.incident_id,
        investigation_id=event.investigation_id,
        change=event_type.removeprefix("remediation."),
        previous_state=str(event.previous_state) if event.previous_state is not None else None,
        new_state=str(event.new_state) if event.new_state is not None else None,
        action_type=str(event.action_type) if event.action_type is not None else None,
        target_service=event.target_service,
        target_environment=event.target_environment,
        trigger=(str(md["trigger"]) if isinstance(md.get("trigger"), str) else None),
        risk_level=(str(md["risk_level"]) if isinstance(md.get("risk_level"), str) else None),
        actor_type=str(event.actor_type),
        actor_id=event.actor_id,
        actor_role=str(event.actor_role) if event.actor_role is not None else None,
        policy_outcome=str(event.policy_outcome) if event.policy_outcome is not None else None,
        policy_version=event.policy_version,
        policy_reason_codes=[str(c) for c in event.policy_reason_codes],
        execution_id=event.execution_id,
        execution_result=(
            str(event.execution_result) if event.execution_result is not None else None
        ),
        verification_id=event.verification_id,
        verification_attempts=_int_or_none(md.get("attempts")),
        checks_passed=_int_or_none(md.get("checks_passed")),
        checks_total=_int_or_none(md.get("checks_total")),
        failure_reason=failure_reason,
        reason=redact_text(event.reason) if event.reason else "",
        audit_id=event.audit_id,
        correlation_id=event.correlation_id,
        occurred_at=event.occurred_at.isoformat(),
    )

    return EventEnvelope(
        event_id=str(uuid.uuid5(_EVENT_ID_NAMESPACE, event.audit_id)),
        event_type=event_type,
        event_version=REMEDIATION_LIFECYCLE_VERSION,
        occurred_at=event.occurred_at,
        source=source,
        trace_id=None,
        payload=payload.model_dump(),
    )


__all__ = [
    "DEFAULT_REMEDIATION_TOPIC",
    "is_publishable",
    "lifecycle_envelope",
]
