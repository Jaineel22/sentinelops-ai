"""SQLAlchemy ORM models — five tables.

``remediations``            one row per remediation proposal + its policy decision + status
``remediation_approvals``   one immutable row per human decision (``UNIQUE(remediation_id)``) — 5C
``remediation_executions``  one row per real execution (``UNIQUE(remediation_id)``) — 5D
``remediation_audit_events`` append-only, one immutable row per lifecycle fact — 5E
``remediation_verifications`` one row per recovery verification (``UNIQUE(remediation_id)``) — 5F

The models deliberately have **no column that can hold an executable command /
script / shell string** — the Phase 5A domain has no such field, so neither do
the tables.

These tables live in the shared ``sentinelops`` database but are migrated by the
remediation-controller's own Alembic lineage (``alembic_version_remediation``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from remediation_controller.domain.catalogue import ParameterValue

_JSON = JSONB().with_variant(JSON(), "sqlite")

_STATUS_VALUES = (
    "'PROPOSED','POLICY_EVALUATION','PENDING_APPROVAL','APPROVED','EXECUTING','EXECUTED',"
    "'VERIFYING','BLOCKED','REJECTED','EXPIRED','EXECUTION_FAILED','RECOVERED','RECOVERY_FAILED'"
)
_ACTION_VALUES = "'RESTART_SERVICE','SCALE_SERVICE','ROLL_BACK_DEPLOYMENT','DISABLE_FEATURE_FLAG'"
_RISK_VALUES = "'LOW','MEDIUM','HIGH'"
_TRIGGER_VALUES = "'RCA_RECOMMENDATION','MANUAL'"
_ENV_VALUES = "'development','staging','production'"
_DECISION_VALUES = "'APPROVE','REJECT'"
_ROLE_VALUES = "'OPERATOR','INCIDENT_RESPONDER','ADMINISTRATOR'"
_OUTCOME_VALUES = "'ALLOW','DENY'"
_EXECUTOR_VALUES = "'LOCAL_SIMULATION'"
_EXEC_STATUS_VALUES = "'STARTED','SUCCEEDED','FAILED'"
_AUDIT_EVENT_VALUES = (
    "'PROPOSAL_CREATED','POLICY_EVALUATED','REMEDIATION_BLOCKED','APPROVED','REJECTED',"
    "'EXECUTION_REQUESTED','EXECUTION_STARTED','EXECUTION_SUCCEEDED','EXECUTION_FAILED',"
    "'VERIFICATION_STARTED','VERIFICATION_SUCCEEDED','VERIFICATION_FAILED'"
)
_ACTOR_TYPE_VALUES = "'SYSTEM','HUMAN'"
_EXEC_MODE_VALUES = "'REAL'"
_VERIFICATION_STATUS_VALUES = "'STARTED','RECOVERED','RECOVERY_FAILED'"
_VERIFIER_TYPE_VALUES = "'DETERMINISTIC_LOCAL'"


class Base(DeclarativeBase):
    pass


class RemediationRow(Base):
    __tablename__ = "remediations"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(40), nullable=False)
    investigation_id: Mapped[str | None] = mapped_column(String(40))
    trigger: Mapped[str] = mapped_column(String(24), nullable=False)
    proposed_by: Mapped[str] = mapped_column(String(128), nullable=False)

    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_service: Mapped[str] = mapped_column(String(128), nullable=False)
    target_environment: Mapped[str] = mapped_column(String(64), nullable=False)
    parameters: Mapped[dict[str, ParameterValue]] = mapped_column(
        _JSON, nullable=False, default=dict
    )
    risk_level: Mapped[str] = mapped_column(String(8), nullable=False)

    source_recommendation: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    expected_effect: Mapped[str] = mapped_column(Text, nullable=False, default="")
    evidence_references: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)

    status: Mapped[str] = mapped_column(String(24), nullable=False)

    # --- policy decision (traceability; the LLM never touches this) ---
    policy_outcome: Mapped[str] = mapped_column(String(8), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(16), nullable=False)
    policy_reason_codes: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    policy_decision: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    policy_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    approval: Mapped[RemediationApprovalRow | None] = relationship(
        back_populates="remediation", cascade="all, delete-orphan", uselist=False
    )
    execution: Mapped[RemediationExecutionRow | None] = relationship(
        back_populates="remediation", cascade="all, delete-orphan", uselist=False
    )
    verification: Mapped[RemediationVerificationRow | None] = relationship(
        back_populates="remediation", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index("ix_remediations_incident", "incident_id"),
        Index("ix_remediations_status", "status"),
        Index("ix_remediations_created_at", "created_at"),
        Index(
            "ix_remediations_history_key",
            "incident_id",
            "action_type",
            "target_service",
            "target_environment",
        ),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_remediations_status"),
        CheckConstraint(f"action_type IN ({_ACTION_VALUES})", name="ck_remediations_action"),
        CheckConstraint(f"risk_level IN ({_RISK_VALUES})", name="ck_remediations_risk"),
        CheckConstraint(f"trigger IN ({_TRIGGER_VALUES})", name="ck_remediations_trigger"),
        CheckConstraint(
            f"target_environment IN ({_ENV_VALUES})", name="ck_remediations_environment"
        ),
        CheckConstraint(f"policy_outcome IN ({_OUTCOME_VALUES})", name="ck_remediations_outcome"),
    )


class RemediationApprovalRow(Base):
    __tablename__ = "remediation_approvals"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    remediation_id: Mapped[str] = mapped_column(
        ForeignKey("remediations.id", ondelete="CASCADE"), nullable=False
    )
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    approver_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    approver_role: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    remediation: Mapped[RemediationRow] = relationship(back_populates="approval")

    __table_args__ = (
        # One immutable human decision per remediation. The row is only ever
        # INSERTed and SELECTed, never UPDATEd — enforced in code and by having
        # no update path in the repository.
        UniqueConstraint("remediation_id", name="uq_approval_remediation"),
        CheckConstraint(f"decision IN ({_DECISION_VALUES})", name="ck_approval_decision"),
        CheckConstraint(f"approver_role IN ({_ROLE_VALUES})", name="ck_approval_role"),
        CheckConstraint("length(trim(approver_identity)) > 0", name="ck_approval_identity"),
        Index("ix_approvals_remediation", "remediation_id"),
    )


class RemediationExecutionRow(Base):
    """One real execution attempt per remediation (Phase 5D).

    ``UNIQUE(remediation_id)`` guarantees a remediation can be executed at most
    once. The row is INSERTed once (``STARTED``) and UPDATEd once to its terminal
    status (``SUCCEEDED`` / ``FAILED``); the append-only audit trail is Phase 5E.
    Dry-runs are **not** persisted here — a dry-run is a pure read-only preview.

    There is no column that can hold a command / script / shell string.
    """

    __tablename__ = "remediation_executions"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    remediation_id: Mapped[str] = mapped_column(
        ForeignKey("remediations.id", ondelete="CASCADE"), nullable=False
    )
    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_service: Mapped[str] = mapped_column(String(128), nullable=False)
    target_environment: Mapped[str] = mapped_column(String(64), nullable=False)
    executor_type: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    dry_run: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    simulated_effect: Mapped[str] = mapped_column(Text, nullable=False, default="")
    exec_metadata: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False, default=dict)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    remediation: Mapped[RemediationRow] = relationship(back_populates="execution")

    __table_args__ = (
        UniqueConstraint("remediation_id", name="uq_execution_remediation"),
        CheckConstraint(f"executor_type IN ({_EXECUTOR_VALUES})", name="ck_execution_executor"),
        CheckConstraint(f"status IN ({_EXEC_STATUS_VALUES})", name="ck_execution_status"),
        CheckConstraint(f"action_type IN ({_ACTION_VALUES})", name="ck_execution_action"),
        CheckConstraint("dry_run = false", name="ck_execution_not_dry_run"),
        Index("ix_executions_remediation", "remediation_id"),
    )


class RemediationAuditEventRow(Base):
    """Append-only audit trail (Phase 5E, ADR-028).

    One immutable row per committed remediation lifecycle fact. The row is
    **only ever INSERTed and SELECTed** — the repository exposes no update or
    delete path, and on PostgreSQL a ``BEFORE UPDATE OR DELETE`` trigger
    (migration ``0003``) rejects any attempt at the database level.

    The monotonically increasing ``seq`` (``BIGINT`` identity) is the total
    chronological order; ``occurred_at`` is a secondary human-facing timestamp.

    There is no column that can hold a command / script / shell string; every
    value is written through ``remediation_controller.audit.redaction``.
    """

    __tablename__ = "remediation_audit_events"

    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    audit_id: Mapped[str] = mapped_column(String(24), nullable=False)
    remediation_id: Mapped[str] = mapped_column(
        ForeignKey("remediations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[str] = mapped_column(String(40), nullable=False)
    investigation_id: Mapped[str | None] = mapped_column(String(40))

    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_role: Mapped[str | None] = mapped_column(String(24))

    previous_state: Mapped[str | None] = mapped_column(String(24))
    new_state: Mapped[str | None] = mapped_column(String(24))

    action_type: Mapped[str | None] = mapped_column(String(32))
    target_service: Mapped[str | None] = mapped_column(String(128))
    target_environment: Mapped[str | None] = mapped_column(String(64))

    policy_outcome: Mapped[str | None] = mapped_column(String(8))
    policy_version: Mapped[str | None] = mapped_column(String(16))
    policy_reason_codes: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)

    execution_id: Mapped[str | None] = mapped_column(String(24))
    execution_mode: Mapped[str | None] = mapped_column(String(16))
    execution_result: Mapped[str | None] = mapped_column(String(12))
    verification_id: Mapped[str | None] = mapped_column(String(24))

    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    event_metadata: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False, default=dict)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("audit_id", name="uq_audit_audit_id"),
        CheckConstraint(f"event_type IN ({_AUDIT_EVENT_VALUES})", name="ck_audit_event_type"),
        CheckConstraint(f"actor_type IN ({_ACTOR_TYPE_VALUES})", name="ck_audit_actor_type"),
        CheckConstraint(
            f"execution_mode IS NULL OR execution_mode IN ({_EXEC_MODE_VALUES})",
            name="ck_audit_execution_mode",
        ),
        CheckConstraint("length(trim(actor_id)) > 0", name="ck_audit_actor_id"),
        Index("ix_audit_remediation_seq", "remediation_id", "seq"),
        Index("ix_audit_incident_seq", "incident_id", "seq"),
        Index("ix_audit_execution", "execution_id"),
        Index("ix_audit_verification", "verification_id"),
        Index("ix_audit_occurred_at", "occurred_at"),
    )


class RemediationVerificationRow(Base):
    """One recovery verification per remediation (Phase 5F, ADR-029).

    ``UNIQUE(remediation_id)`` guarantees a remediation is verified at most once.
    The row is INSERTed once (``STARTED``) and UPDATEd once to its terminal
    status (``RECOVERED`` / ``RECOVERY_FAILED``); the append-only history lives in
    ``remediation_audit_events``.

    ``checks`` holds the structured evidence (name / passed / observed /
    threshold / detail) — inert data, redacted before storage. There is no
    column that can hold a command / script / shell string.
    """

    __tablename__ = "remediation_verifications"

    id: Mapped[str] = mapped_column(String(24), primary_key=True)
    remediation_id: Mapped[str] = mapped_column(
        ForeignKey("remediations.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    verifier_type: Mapped[str] = mapped_column(String(32), nullable=False)
    verifier_version: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checks: Mapped[list[dict[str, object]]] = mapped_column(_JSON, nullable=False, default=list)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    poll_interval_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    ver_metadata: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    remediation: Mapped[RemediationRow] = relationship(back_populates="verification")

    __table_args__ = (
        UniqueConstraint("remediation_id", name="uq_verification_remediation"),
        CheckConstraint(
            f"status IN ({_VERIFICATION_STATUS_VALUES})", name="ck_verification_status"
        ),
        CheckConstraint(
            f"verifier_type IN ({_VERIFIER_TYPE_VALUES})", name="ck_verification_verifier"
        ),
        CheckConstraint("attempts >= 0", name="ck_verification_attempts"),
        Index("ix_verifications_remediation", "remediation_id"),
        Index("ix_verifications_execution", "execution_id"),
    )
