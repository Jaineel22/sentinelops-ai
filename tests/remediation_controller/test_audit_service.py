"""Phase 5E — the append-only audit trail is written for every lifecycle event."""

from __future__ import annotations

from datetime import timedelta

import pytest
from pydantic import ValidationError

from remediation_controller.audit.model import ActorType, AuditEventType, RemediationAuditEvent
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.enums import ExecutorType
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.executor import ExecutorError
from remediation_controller.repository import (
    InMemoryRemediationRepository,
    ProposalNotMappableError,
)
from remediation_controller.service import RemediationService
from tests.remediation_controller.policy_fakes import BASE_TIME


class _BoomExecutor:
    executor_type = ExecutorType.LOCAL_SIMULATION

    def execute(self, proposal, *, execution_id, dry_run, now):  # type: ignore[no-untyped-def]
        raise ExecutorError("simulated failure")


_INCIDENT = "inc_00112233aabbccdd"


def _svc() -> tuple[RemediationService, InMemoryRemediationRepository]:
    repo = InMemoryRemediationRepository()
    return RemediationService(repository=repo), repo


def _rec(
    action: str = "RESTART_SERVICE", target: str | None = "orders-service"
) -> RcaRecommendedActionInput:
    return RcaRecommendedActionInput(action_type=action, target_service=target, rationale="pool")


async def _events(svc: RemediationService, rid: str) -> list[RemediationAuditEvent]:
    got = await svc.list_audit_events(rid)
    assert got is not None
    return list(got)


async def test_proposal_and_policy_events_on_create() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    events = await _events(svc, rec.remediation_id)
    assert [e.event_type for e in events] == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
    ]
    created, policy = events
    assert created.actor_type is ActorType.SYSTEM
    assert created.previous_state is None
    assert created.new_state is RemediationStatus.PROPOSED
    assert policy.new_state is RemediationStatus.PENDING_APPROVAL
    assert policy.policy_outcome is not None and policy.policy_outcome.value == "ALLOW"
    assert policy.policy_version == "1"
    assert created.incident_id == _INCIDENT
    assert created.action_type is not None


async def test_policy_block_emits_blocked_event() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=_rec(),
        incident_severity="HIGH",
        target_environment="staging",  # policy DENY -> BLOCKED
        now=BASE_TIME,
    )
    assert rec.status is RemediationStatus.BLOCKED
    types = [e.event_type for e in await _events(svc, rec.remediation_id)]
    assert types == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
        AuditEventType.REMEDIATION_BLOCKED,
    ]


async def test_unmappable_proposal_writes_no_audit_row() -> None:
    svc, repo = _svc()
    with pytest.raises(ProposalNotMappableError):
        await svc.propose(
            incident_id=_INCIDENT,
            recommendation=_rec(action="kubectl delete deploy orders-service"),
            incident_severity="HIGH",
        )
    assert repo._store.audit == []  # nothing persisted, nothing audited


async def test_approval_event() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice@example.com",
        approver_role=ApproverRole.INCIDENT_RESPONDER,
        reason="restart is safe",
        now=BASE_TIME + timedelta(minutes=1),
    )
    events = await _events(svc, rec.remediation_id)
    approval = events[-1]
    assert approval.event_type is AuditEventType.APPROVED
    assert approval.actor_type is ActorType.HUMAN
    assert approval.actor_id == "alice@example.com"
    assert approval.actor_role is ApproverRole.INCIDENT_RESPONDER
    assert approval.previous_state is RemediationStatus.PENDING_APPROVAL
    assert approval.new_state is RemediationStatus.APPROVED
    assert approval.reason == "restart is safe"


async def test_rejection_event() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.REJECT,
        approver_identity="bob",
        approver_role=ApproverRole.OPERATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    assert (await _events(svc, rec.remediation_id))[-1].event_type is AuditEventType.REJECTED


async def test_full_execution_lifecycle_produces_six_ordered_events() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    await svc.execute(rec.remediation_id, now=BASE_TIME + timedelta(minutes=2))

    events = await _events(svc, rec.remediation_id)
    assert [e.event_type for e in events] == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
        AuditEventType.APPROVED,
        AuditEventType.EXECUTION_REQUESTED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_SUCCEEDED,
    ]
    started, succeeded = events[4], events[5]
    assert started.previous_state is RemediationStatus.APPROVED
    assert started.new_state is RemediationStatus.EXECUTING
    assert succeeded.new_state is RemediationStatus.EXECUTED
    assert succeeded.execution_id is not None
    assert succeeded.execution_id == started.execution_id
    assert succeeded.execution_result is not None
    assert succeeded.execution_result.value == "SUCCEEDED"


async def test_execution_failure_is_audited_as_failed_not_succeeded() -> None:
    repo = InMemoryRemediationRepository()
    svc = RemediationService(repository=repo, executor=_BoomExecutor())

    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    out = await svc.execute(rec.remediation_id, now=BASE_TIME + timedelta(minutes=2))
    assert out.record.status is RemediationStatus.EXECUTION_FAILED

    events = await _events(svc, rec.remediation_id)
    assert events[-1].event_type is AuditEventType.EXECUTION_FAILED
    assert AuditEventType.EXECUTION_SUCCEEDED not in [e.event_type for e in events]
    assert events[-1].new_state is RemediationStatus.EXECUTION_FAILED


async def test_dry_run_writes_no_audit_event() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    before = len(await _events(svc, rec.remediation_id))
    await svc.execute(rec.remediation_id, dry_run=True, now=BASE_TIME + timedelta(minutes=2))
    assert len(await _events(svc, rec.remediation_id)) == before


async def test_audit_trail_is_append_only_earlier_entries_never_change() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    first_snapshot = [e.model_dump() for e in await _events(svc, rec.remediation_id)]
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    await svc.execute(rec.remediation_id, now=BASE_TIME + timedelta(minutes=2))
    after = [e.model_dump() for e in await _events(svc, rec.remediation_id)]
    assert after[: len(first_snapshot)] == first_snapshot  # prefix unchanged
    assert len(after) == 6


async def test_repository_exposes_no_audit_mutation_method() -> None:
    repo = InMemoryRemediationRepository()
    for banned in ("update_audit_event", "delete_audit_event", "edit_audit_event"):
        assert not hasattr(repo, banned)
    # the only public audit method is the read
    assert hasattr(repo, "list_audit_events")


async def test_audit_events_are_frozen() -> None:
    svc, _ = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    event = (await _events(svc, rec.remediation_id))[0]
    with pytest.raises(ValidationError):
        event.reason = "tampered"


async def test_audit_events_paginate_chronologically() -> None:
    svc, repo = _svc()
    rec = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME + timedelta(minutes=1),
    )
    page1 = await repo.list_audit_events(rec.remediation_id, limit=2, offset=0)
    page2 = await repo.list_audit_events(rec.remediation_id, limit=2, offset=2)
    assert [e.event_type for e in page1] == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
    ]
    assert [e.event_type for e in page2] == [AuditEventType.APPROVED]


async def test_audit_trail_is_scoped_per_remediation() -> None:
    svc, _ = _svc()
    a = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    b = await svc.propose(
        incident_id=_INCIDENT, recommendation=_rec(), incident_severity="HIGH", now=BASE_TIME
    )
    for rid in (a.remediation_id, b.remediation_id):
        events = await _events(svc, rid)
        assert {e.remediation_id for e in events} == {rid}
