"""Phase 5E — the append-only remediation audit trail.

Every meaningful, *committed* remediation lifecycle fact — proposal created,
policy evaluated, blocked, approved, rejected, execution requested / started /
succeeded / failed — is recorded as an immutable
:class:`~remediation_controller.audit.model.RemediationAuditEvent`.

Guarantees:

* **append-only** — the application only ever ``INSERT``s and ``SELECT``s audit
  rows. There is no update / delete / client-create path (a PostgreSQL trigger
  is the backstop, migration ``0003``);
* **transactional** — an audit event is written in the *same* transaction as the
  state transition it records (``RemediationRepository`` methods), so a
  committed transition can never be missing its audit record;
* **secret-safe** — every value passes
  :mod:`remediation_controller.audit.redaction` before it is stored.

Read-only access: ``GET /remediations/{id}/audit`` (chronological, paginated).
"""

from __future__ import annotations

from remediation_controller.audit.builders import (
    decision_event,
    execution_finished_event,
    execution_requested_event,
    execution_started_event,
    policy_evaluated_event,
    proposal_created_event,
    remediation_blocked_event,
    verification_finished_event,
    verification_started_event,
)
from remediation_controller.audit.model import (
    AUDIT_ID_RE,
    SYSTEM_ACTOR_ID,
    ActorType,
    AuditEventType,
    AuditMetadataValue,
    ExecutionMode,
    RemediationAuditEvent,
    new_audit_id,
)
from remediation_controller.audit.redaction import (
    REDACTED,
    redact_identity,
    redact_metadata,
    redact_text,
)

__all__ = [
    "AUDIT_ID_RE",
    "REDACTED",
    "SYSTEM_ACTOR_ID",
    "ActorType",
    "AuditEventType",
    "AuditMetadataValue",
    "ExecutionMode",
    "RemediationAuditEvent",
    "decision_event",
    "execution_finished_event",
    "execution_requested_event",
    "execution_started_event",
    "new_audit_id",
    "policy_evaluated_event",
    "proposal_created_event",
    "redact_identity",
    "redact_metadata",
    "redact_text",
    "remediation_blocked_event",
    "verification_finished_event",
    "verification_started_event",
]
