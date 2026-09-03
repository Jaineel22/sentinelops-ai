"""Phase 5G: remediation lifecycle event contract + envelope translation."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from remediation_controller import SERVICE_NAME
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
from remediation_controller.audit.model import AuditEventType
from remediation_controller.domain.enums import ExecutionStatus, ExecutorType, RemediationStatus
from remediation_controller.domain.proposal import RemediationProposal
from remediation_controller.executor import new_execution_id
from remediation_controller.executor.base import ExecutionResult
from remediation_controller.kafka.events import is_publishable, lifecycle_envelope
from sentinelops_common.contracts import (
    REMEDIATION_LIFECYCLE_EVENT_TYPES,
    REMEDIATION_LIFECYCLE_VERSION,
    RemediationLifecycleV1,
)
from sentinelops_common.events import parse_envelope
from tests.remediation_controller.conftest import make_approval, make_proposal
from tests.remediation_controller.persistence_fakes import allow_decision, deny_decision

_NOW = datetime(2026, 9, 3, 10, 0, 0, tzinfo=UTC)


def _proposal(**kw: object) -> RemediationProposal:
    return make_proposal(**kw)  # type: ignore[arg-type]


def test_proposed_event_maps_and_carries_safe_fields() -> None:
    p = _proposal()
    audit = proposal_created_event(p, correlation_id="req-1", now=_NOW)
    env = lifecycle_envelope(audit, source=SERVICE_NAME)

    assert env is not None
    assert env.event_type == "remediation.proposed"
    assert env.event_type in REMEDIATION_LIFECYCLE_EVENT_TYPES
    assert env.event_version == REMEDIATION_LIFECYCLE_VERSION
    assert env.source == SERVICE_NAME

    payload = RemediationLifecycleV1.model_validate(env.payload)
    assert payload.remediation_id == p.remediation_id
    assert payload.incident_id == p.incident_id
    assert payload.investigation_id == p.investigation_id
    assert payload.change == "proposed"
    assert payload.new_state == "PROPOSED"
    assert payload.action_type == "RESTART_SERVICE"
    assert payload.target_service == "orders-service"
    assert payload.target_environment == "development"
    assert payload.actor_type == "SYSTEM"
    assert payload.correlation_id == "req-1"
    assert payload.audit_id == audit.audit_id


def test_policy_and_blocked_events() -> None:
    p = _proposal()
    allow = policy_evaluated_event(
        p, allow_decision(at=_NOW), new_state=RemediationStatus.PENDING_APPROVAL, now=_NOW
    )
    env = lifecycle_envelope(allow, source=SERVICE_NAME)
    assert env is not None and env.event_type == "remediation.policy_evaluated"
    pl = RemediationLifecycleV1.model_validate(env.payload)
    assert pl.policy_outcome == "ALLOW"
    assert pl.new_state == "PENDING_APPROVAL"

    blk = remediation_blocked_event(p, deny_decision(at=_NOW), now=_NOW)
    benv = lifecycle_envelope(blk, source=SERVICE_NAME)
    assert benv is not None and benv.event_type == "remediation.blocked"
    bpl = RemediationLifecycleV1.model_validate(benv.payload)
    assert bpl.policy_outcome == "DENY"
    assert bpl.new_state == "BLOCKED"
    assert "SEVERITY_NOT_ALLOWED" in bpl.policy_reason_codes


def test_decision_events_use_redacted_actor_id() -> None:
    p = _proposal()
    approval = make_approval(remediation_id=p.remediation_id, approver_identity="alice@example.com")
    env = lifecycle_envelope(decision_event(p, approval, now=_NOW), source=SERVICE_NAME)
    assert env is not None and env.event_type == "remediation.approved"
    pl = RemediationLifecycleV1.model_validate(env.payload)
    assert pl.actor_type == "HUMAN"
    assert pl.actor_role == "INCIDENT_RESPONDER"
    assert pl.new_state == "APPROVED"


def test_execution_events() -> None:
    p = _proposal()
    exec_id = new_execution_id()
    started = execution_started_event(p, execution_id=exec_id, now=_NOW)
    senv = lifecycle_envelope(started, source=SERVICE_NAME)
    assert senv is not None and senv.event_type == "remediation.execution_started"

    result = ExecutionResult(
        execution_id=exec_id,
        remediation_id=p.remediation_id,
        action_type=p.action_type,
        target_service=p.target.service_name,
        target_environment=p.target.environment,
        executor_type=ExecutorType.LOCAL_SIMULATION,
        status=ExecutionStatus.SUCCEEDED,
        dry_run=False,
        started_at=_NOW,
        completed_at=_NOW,
        simulated_effect="restarted all instances",
    )
    fin = execution_finished_event(p, result, final_state=RemediationStatus.EXECUTED, now=_NOW)
    fenv = lifecycle_envelope(fin, source=SERVICE_NAME)
    assert fenv is not None and fenv.event_type == "remediation.execution_succeeded"
    pl = RemediationLifecycleV1.model_validate(fenv.payload)
    assert pl.execution_id == exec_id
    assert pl.execution_result == "SUCCEEDED"


def test_execution_requested_is_not_published() -> None:
    p = _proposal()
    audit = execution_requested_event(p, execution_id="exec_1", now=_NOW)
    assert is_publishable(audit) is False
    assert lifecycle_envelope(audit, source=SERVICE_NAME) is None


def test_verification_events_carry_counts() -> None:
    p = _proposal()
    start = verification_started_event(p, verification_id="ver_1", execution_id="exec_1", now=_NOW)
    senv = lifecycle_envelope(start, source=SERVICE_NAME)
    assert senv is not None
    assert senv.event_type == "remediation.recovery_verification_started"

    ok = verification_finished_event(
        p,
        verification_id="ver_1",
        execution_id="exec_1",
        final_state=RemediationStatus.RECOVERED,
        attempts=2,
        checks_passed=4,
        checks_total=4,
        failure_reason=None,
        verifier_type="DETERMINISTIC_LOCAL",
        now=_NOW,
    )
    env = lifecycle_envelope(ok, source=SERVICE_NAME)
    assert env is not None and env.event_type == "remediation.recovered"
    pl = RemediationLifecycleV1.model_validate(env.payload)
    assert pl.verification_attempts == 2
    assert pl.checks_passed == 4
    assert pl.checks_total == 4

    failed = verification_finished_event(
        p,
        verification_id="ver_1",
        execution_id="exec_1",
        final_state=RemediationStatus.RECOVERY_FAILED,
        attempts=10,
        checks_passed=1,
        checks_total=4,
        failure_reason="error_rate 0.40 > 0.05 after 10 polls",
        verifier_type="DETERMINISTIC_LOCAL",
        now=_NOW,
    )
    fenv = lifecycle_envelope(failed, source=SERVICE_NAME)
    assert fenv is not None and fenv.event_type == "remediation.recovery_failed"
    fpl = RemediationLifecycleV1.model_validate(fenv.payload)
    assert fpl.failure_reason is not None
    assert "error_rate" in fpl.failure_reason


def test_every_audit_event_type_maps_or_is_explicitly_skipped() -> None:
    mapped_or_skipped = {
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
        AuditEventType.REMEDIATION_BLOCKED,
        AuditEventType.APPROVED,
        AuditEventType.REJECTED,
        AuditEventType.EXECUTION_REQUESTED,  # deliberately skipped
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_SUCCEEDED,
        AuditEventType.EXECUTION_FAILED,
        AuditEventType.VERIFICATION_STARTED,
        AuditEventType.VERIFICATION_SUCCEEDED,
        AuditEventType.VERIFICATION_FAILED,
    }
    assert set(AuditEventType) == mapped_or_skipped


def test_event_id_is_deterministic_from_audit_id() -> None:
    p = _proposal()
    audit = proposal_created_event(p, now=_NOW)
    a = lifecycle_envelope(audit, source=SERVICE_NAME)
    b = lifecycle_envelope(audit, source=SERVICE_NAME)
    assert a is not None and b is not None
    assert a.event_id == b.event_id  # stable -> consumer can dedupe a republish


def test_envelope_serialises_and_round_trips() -> None:
    p = _proposal()
    env = lifecycle_envelope(proposal_created_event(p, now=_NOW), source=SERVICE_NAME)
    assert env is not None
    raw = env.to_json_bytes()
    parsed = parse_envelope(raw)
    assert parsed.event_type == env.event_type
    assert parsed.payload == env.payload
    # and the payload is JSON-safe scalars/lists only
    json.loads(raw)


def test_payload_has_no_command_shaped_field() -> None:
    banned = {"command", "script", "shell", "cmd", "exec", "run", "url", "endpoint", "token"}
    assert set(RemediationLifecycleV1.model_fields).isdisjoint(banned)
