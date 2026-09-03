"""Request / response models for the remediation approval API (Phase 5C).

Requests are ``extra="forbid"`` — an unknown field (``command``, ``script``,
``kubectl``, an arbitrary executor parameter, …) is a ``422``, never silently
accepted. Responses are explicit and flat; the SQLAlchemy models are never
exposed.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from remediation_controller.audit.model import (
    ActorType,
    AuditEventType,
    ExecutionMode,
    RemediationAuditEvent,
)
from remediation_controller.domain.enums import (
    ApproverRole,
    ExecutionStatus,
    ExecutorType,
    RemediationActionType,
    RemediationStatus,
    RemediationTrigger,
    RiskLevel,
)
from remediation_controller.domain.models import INCIDENT_ID_RE, INVESTIGATION_ID_RE
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.policy.codes import PolicyOutcome, PolicyReasonCode
from remediation_controller.recovery.model import (
    RecoveryCheck,
    VerificationResult,
    VerificationStatus,
)
from remediation_controller.repository import RemediationRecord


# --- requests -------------------------------------------------------
class RecommendedActionBody(BaseModel):
    """The Phase 4 RCA recommendation slice (mirrors ``RcaRecommendedActionInput``).

    ``action_type`` is a bounded label, not the executable enum — an unknown or
    adversarial value is mapped to a deterministic BLOCK, never executed.
    """

    model_config = ConfigDict(extra="forbid")

    action_type: str = Field(min_length=1, max_length=64)
    target_service: str | None = Field(default=None, max_length=128)
    description: str = Field(default="", max_length=4000)
    rationale: str = Field(default="", max_length=4000)
    evidence_ids: tuple[str, ...] = ()


class CreateRemediationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(pattern=INCIDENT_ID_RE)
    investigation_id: str | None = Field(default=None, pattern=INVESTIGATION_ID_RE)
    # 5C limitation: the caller supplies the verified incident severity and target
    # environment. 5H fetches severity from the Incident API. Absent severity
    # fails closed in the policy engine.
    incident_severity: str | None = Field(default=None, max_length=16)
    target_environment: str = Field(default="development", max_length=64)
    proposed_by: str = Field(default="api", min_length=1, max_length=128)
    recommended_action: RecommendedActionBody


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Demo identity model: supplied by the request, only structurally validated.
    # NOT production authentication (ADR-026).
    approver_identity: str = Field(min_length=1, max_length=128)
    approver_role: ApproverRole
    reason: str = Field(default="", max_length=1000)

    @field_validator("approver_identity")
    @classmethod
    def _identity_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("approver_identity must not be empty or whitespace")
        return value


class ExecuteRequest(BaseModel):
    """Phase 5D execution request. The ONLY field is ``dry_run``. There is no
    command, script, shell, executor selector, or infrastructure config — the
    already-validated stored remediation is all the executor gets."""

    model_config = ConfigDict(extra="forbid")

    dry_run: bool = False


class VerifyRecoveryRequest(BaseModel):
    """Phase 5F recovery-verification request. It has **no fields** — the
    verifier only observes; there is nothing a client can pass that would change
    what it does, and any extra field is a ``422`` (``extra="forbid"``)."""

    model_config = ConfigDict(extra="forbid")


# --- responses -----------------------------------------------------
class TargetView(BaseModel):
    service_name: str
    environment: str


class PolicyView(BaseModel):
    outcome: PolicyOutcome
    policy_version: str
    reason_codes: tuple[PolicyReasonCode, ...]
    evaluated_rules: tuple[str, ...]
    evaluated_at: datetime
    violations: tuple[dict[str, str], ...] = ()


class ApprovalView(BaseModel):
    approval_id: str
    decision: str
    approver_identity: str
    approver_role: ApproverRole
    reason: str
    decided_at: datetime


class ExecutionView(BaseModel):
    execution_id: str
    action_type: RemediationActionType
    target_service: str
    target_environment: str
    executor_type: ExecutorType
    status: ExecutionStatus
    dry_run: bool
    started_at: datetime
    completed_at: datetime | None
    simulated_effect: str
    metadata: dict[str, str | int | bool]
    error: str | None

    @classmethod
    def of(cls, result: ExecutionResult) -> ExecutionView:
        return cls(
            execution_id=result.execution_id,
            action_type=result.action_type,
            target_service=result.target_service,
            target_environment=result.target_environment,
            executor_type=result.executor_type,
            status=result.status,
            dry_run=result.dry_run,
            started_at=result.started_at,
            completed_at=result.completed_at,
            simulated_effect=result.simulated_effect,
            metadata=dict(result.metadata),
            error=result.error,
        )


class RecoveryCheckView(BaseModel):
    name: str
    passed: bool
    observed: str
    threshold: str
    detail: str

    @classmethod
    def of(cls, check: RecoveryCheck) -> RecoveryCheckView:
        return cls(
            name=check.name,
            passed=check.passed,
            observed=check.observed,
            threshold=check.threshold,
            detail=check.detail,
        )


class VerificationView(BaseModel):
    verification_id: str
    execution_id: str
    status: VerificationStatus
    verifier_type: str
    verifier_version: str
    attempts: int
    checks: tuple[RecoveryCheckView, ...]
    failure_reason: str | None
    timeout_seconds: int
    poll_interval_seconds: float
    metadata: dict[str, str | int | bool]
    started_at: datetime
    completed_at: datetime | None

    @classmethod
    def of(cls, result: VerificationResult) -> VerificationView:
        return cls(
            verification_id=result.verification_id,
            execution_id=result.execution_id,
            status=result.status,
            verifier_type=result.verifier_type,
            verifier_version=result.verifier_version,
            attempts=result.attempts,
            checks=tuple(RecoveryCheckView.of(c) for c in result.checks),
            failure_reason=result.failure_reason,
            timeout_seconds=result.timeout_seconds,
            poll_interval_seconds=result.poll_interval_seconds,
            metadata=dict(result.metadata),
            started_at=result.started_at,
            completed_at=result.completed_at,
        )


class RemediationView(BaseModel):
    remediation_id: str
    incident_id: str
    investigation_id: str | None
    trigger: RemediationTrigger
    proposed_by: str
    action_type: RemediationActionType
    target: TargetView
    parameters: dict[str, str | int | bool]
    risk_level: RiskLevel
    requires_approval: bool
    source_recommendation: str
    reason: str
    expected_effect: str
    evidence_references: tuple[str, ...]
    status: RemediationStatus
    created_at: datetime
    expires_at: datetime
    policy: PolicyView
    approval: ApprovalView | None
    execution: ExecutionView | None
    verification: VerificationView | None

    @classmethod
    def of(cls, record: RemediationRecord) -> RemediationView:
        p = record.proposal
        d = record.policy_decision
        return cls(
            remediation_id=p.remediation_id,
            incident_id=p.incident_id,
            investigation_id=p.investigation_id,
            trigger=p.trigger,
            proposed_by=p.proposed_by,
            action_type=p.action_type,
            target=TargetView(service_name=p.target.service_name, environment=p.target.environment),
            parameters=dict(p.parameters),
            risk_level=p.risk_level,
            requires_approval=p.requires_approval,
            source_recommendation=p.source_recommendation,
            reason=p.reason,
            expected_effect=p.expected_effect,
            evidence_references=p.evidence_references,
            status=p.status,
            created_at=p.created_at,
            expires_at=p.expires_at,
            policy=PolicyView(
                outcome=d.outcome,
                policy_version=d.policy_version,
                reason_codes=d.reason_codes,
                evaluated_rules=d.evaluated_rules,
                evaluated_at=d.evaluated_at,
                violations=tuple(
                    {"code": str(v.code), "rule": v.rule, "detail": v.detail} for v in d.violations
                ),
            ),
            approval=(
                None
                if record.approval is None
                else ApprovalView(
                    approval_id=record.approval.approval_id,
                    decision=str(record.approval.decision),
                    approver_identity=record.approval.approver_identity,
                    approver_role=record.approval.approver_role,
                    reason=record.approval.reason,
                    decided_at=record.approval.decided_at,
                )
            ),
            execution=(None if record.execution is None else ExecutionView.of(record.execution)),
            verification=(
                None if record.verification is None else VerificationView.of(record.verification)
            ),
        )


class RemediationListResponse(BaseModel):
    remediations: list[RemediationView]
    count: int


# --- audit trail (Phase 5E) ---------------------------------------
class AuditEventView(BaseModel):
    """One immutable audit record. Read-only projection — the API exposes no
    field a client could write, and the trail is append-only (no POST/PUT/PATCH/
    DELETE route exists)."""

    audit_id: str
    remediation_id: str
    incident_id: str
    investigation_id: str | None
    event_type: AuditEventType
    actor_type: ActorType
    actor_id: str
    actor_role: ApproverRole | None
    previous_state: RemediationStatus | None
    new_state: RemediationStatus | None
    action_type: RemediationActionType | None
    target_service: str | None
    target_environment: str | None
    policy_outcome: PolicyOutcome | None
    policy_version: str | None
    policy_reason_codes: tuple[PolicyReasonCode, ...]
    execution_id: str | None
    execution_mode: ExecutionMode | None
    execution_result: ExecutionStatus | None
    reason: str
    correlation_id: str | None
    metadata: dict[str, str | int | bool]
    occurred_at: datetime

    @classmethod
    def of(cls, event: RemediationAuditEvent) -> AuditEventView:
        return cls(
            audit_id=event.audit_id,
            remediation_id=event.remediation_id,
            incident_id=event.incident_id,
            investigation_id=event.investigation_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            actor_role=event.actor_role,
            previous_state=event.previous_state,
            new_state=event.new_state,
            action_type=event.action_type,
            target_service=event.target_service,
            target_environment=event.target_environment,
            policy_outcome=event.policy_outcome,
            policy_version=event.policy_version,
            policy_reason_codes=event.policy_reason_codes,
            execution_id=event.execution_id,
            execution_mode=event.execution_mode,
            execution_result=event.execution_result,
            reason=event.reason,
            correlation_id=event.correlation_id,
            metadata=dict(event.metadata),
            occurred_at=event.occurred_at,
        )


class AuditEventListResponse(BaseModel):
    remediation_id: str
    events: list[AuditEventView]
    count: int
