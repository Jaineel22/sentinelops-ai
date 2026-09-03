"""The append-only remediation audit event model (Phase 5E).

A :class:`RemediationAuditEvent` is one **immutable historical fact** about a
remediation's lifecycle: who did what, when, which state transition it caused,
and (for a policy or execution event) the deterministic decision or result that
went with it.

Design rules (mirrors the rest of the domain):

* frozen + ``extra="forbid"`` — an event cannot grow a command-shaped field;
* every free-text / structured field passes the
  :mod:`~remediation_controller.audit.redaction` boundary before it is built,
  so the audit trail can never become a secret store;
* the vocabulary (:class:`AuditEventType`, :class:`ActorType`) is **closed** —
  there is no ``CUSTOM`` escape hatch and no client can mint an arbitrary type.

The model carries no ``sequence`` field: total chronological order is the
database's monotonic ``BIGINT`` primary key (``occurred_at`` is a secondary,
human-facing timestamp only).
"""

from __future__ import annotations

import secrets
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from remediation_controller.domain.enums import (
    ApproverRole,
    ExecutionStatus,
    RemediationActionType,
    RemediationStatus,
)
from remediation_controller.policy.codes import PolicyOutcome, PolicyReasonCode

AUDIT_ID_RE = r"^aud_[0-9a-f]{16}$"

SYSTEM_ACTOR_ID = "remediation-controller"
"""The actor id used for every deterministic, system-generated audit event
(proposal mapping, policy evaluation, executor invocation)."""


def new_audit_id() -> str:
    return f"aud_{secrets.token_hex(8)}"


class AuditEventType(StrEnum):
    """The closed set of auditable remediation lifecycle events (Phase 5E + 5F).

    Deliberately 1:1 with meaningful, *committed* lifecycle facts — there is no
    synthetic event created just to inflate the trail.
    """

    PROPOSAL_CREATED = "PROPOSAL_CREATED"
    POLICY_EVALUATED = "POLICY_EVALUATED"
    REMEDIATION_BLOCKED = "REMEDIATION_BLOCKED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTION_REQUESTED = "EXECUTION_REQUESTED"
    EXECUTION_STARTED = "EXECUTION_STARTED"
    EXECUTION_SUCCEEDED = "EXECUTION_SUCCEEDED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    # --- Phase 5F: recovery verification ---
    VERIFICATION_STARTED = "VERIFICATION_STARTED"
    VERIFICATION_SUCCEEDED = "VERIFICATION_SUCCEEDED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class ActorType(StrEnum):
    """Who caused an audit event.

    ``SYSTEM`` — the remediation-controller itself (deterministic mapping, policy
    engine, executor). ``HUMAN`` — an operator acting through the approval API.
    """

    SYSTEM = "SYSTEM"
    HUMAN = "HUMAN"


class ExecutionMode(StrEnum):
    """Only ``REAL`` is ever recorded — a dry-run is a read-only preview that
    persists nothing (ADR-027), so it produces no audit event. Kept explicit so
    the schema is self-documenting and 5G can extend it."""

    REAL = "REAL"


AuditMetadataValue = str | int | bool
"""Audit metadata values are scalars only — no nested structure that could
smuggle an instruction or an unbounded blob."""


class RemediationAuditEvent(BaseModel):
    """One immutable audit record. Built only by
    :mod:`remediation_controller.audit.builders`; never constructed from client
    input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    audit_id: str = Field(pattern=AUDIT_ID_RE)
    remediation_id: str
    incident_id: str
    investigation_id: str | None = None

    event_type: AuditEventType

    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=128)
    actor_role: ApproverRole | None = None

    previous_state: RemediationStatus | None = None
    new_state: RemediationStatus | None = None

    action_type: RemediationActionType | None = None
    target_service: str | None = Field(default=None, max_length=128)
    target_environment: str | None = Field(default=None, max_length=64)

    policy_outcome: PolicyOutcome | None = None
    policy_version: str | None = Field(default=None, max_length=16)
    policy_reason_codes: tuple[PolicyReasonCode, ...] = ()

    execution_id: str | None = None
    execution_mode: ExecutionMode | None = None
    execution_result: ExecutionStatus | None = None

    verification_id: str | None = None

    reason: str = Field(default="", max_length=1000)
    correlation_id: str | None = Field(default=None, max_length=64)
    metadata: dict[str, AuditMetadataValue] = Field(default_factory=dict)

    occurred_at: datetime


__all__ = [
    "AUDIT_ID_RE",
    "SYSTEM_ACTOR_ID",
    "ActorType",
    "AuditEventType",
    "AuditMetadataValue",
    "ExecutionMode",
    "RemediationAuditEvent",
    "new_audit_id",
]
