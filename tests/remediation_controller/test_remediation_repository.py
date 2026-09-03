"""RemediationRepository — in-memory and SQLite, proven equivalent (Phase 5C)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio

from remediation_controller.db import Database, SqlRemediationRepository
from remediation_controller.domain import (
    ApprovalDecision,
    ApproverRole,
    RemediationActionType,
    RemediationApproval,
    RemediationStatus,
    RiskLevel,
    new_approval_id,
)
from remediation_controller.repository import (
    InMemoryRemediationRepository,
    RemediationAlreadyDecidedError,
    RemediationFilter,
    RemediationNotFoundError,
    RemediationRepository,
)
from tests.remediation_controller.persistence_fakes import BASE_TIME, make_record


@pytest_asyncio.fixture
async def sql_repo(sqlite_remediation_db: Database) -> AsyncIterator[SqlRemediationRepository]:
    yield SqlRemediationRepository(sqlite_remediation_db)


@pytest.fixture(params=["memory", "sql"])
def repo(
    request: pytest.FixtureRequest, sql_repo: SqlRemediationRepository
) -> RemediationRepository:
    return InMemoryRemediationRepository() if request.param == "memory" else sql_repo


async def test_create_and_read_back(repo: RemediationRepository) -> None:
    record = make_record()
    created = await repo.create(record)
    assert created.remediation_id == record.remediation_id

    got = await repo.get(record.remediation_id)
    assert got is not None
    assert got.status is RemediationStatus.PENDING_APPROVAL
    assert got.proposal.action_type is RemediationActionType.RESTART_SERVICE
    assert got.policy_decision.outcome.value == "ALLOW"
    assert got.approval is None
    assert "command" not in got.model_dump()


async def test_get_unknown_is_none(repo: RemediationRepository) -> None:
    assert await repo.get("rem_deadbeefdeadbeef") is None


async def test_list_filters(repo: RemediationRepository) -> None:
    await repo.create(make_record(remediation_id="rem_00000000000000a1", incident_id="inc_a1a1a1"))
    await repo.create(
        make_record(
            remediation_id="rem_00000000000000a2",
            incident_id="inc_b2b2b2",
            action_type=RemediationActionType.ROLL_BACK_DEPLOYMENT,
            risk_level=RiskLevel.HIGH,
        )
    )
    assert len(await repo.list(RemediationFilter())) == 2
    by_incident = await repo.list(RemediationFilter(incident_id="inc_a1a1a1"))
    assert len(by_incident) == 1 and by_incident[0].incident_id == "inc_a1a1a1"
    by_action = await repo.list(
        RemediationFilter(action_type=RemediationActionType.ROLL_BACK_DEPLOYMENT)
    )
    assert len(by_action) == 1
    by_status = await repo.list(RemediationFilter(status=RemediationStatus.PENDING_APPROVAL))
    assert len(by_status) == 2


async def test_record_decision_attaches_immutable_approval(repo: RemediationRepository) -> None:
    record = make_record()
    await repo.create(record)
    new_proposal = record.proposal.model_copy(update={"status": RemediationStatus.APPROVED})
    approval = RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id=record.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        decided_at=BASE_TIME + timedelta(minutes=5),
    )
    updated = await repo.record_decision(
        record.remediation_id, new_proposal=new_proposal, approval=approval
    )
    assert updated.status is RemediationStatus.APPROVED
    assert updated.approval is not None and updated.approval.approver_identity == "alice"


async def test_second_decision_is_rejected(repo: RemediationRepository) -> None:
    record = make_record()
    await repo.create(record)
    approved = record.proposal.model_copy(update={"status": RemediationStatus.APPROVED})
    rejected = record.proposal.model_copy(update={"status": RemediationStatus.REJECTED})

    def _approval(decision: ApprovalDecision) -> RemediationApproval:
        return RemediationApproval(
            approval_id=new_approval_id(),
            remediation_id=record.remediation_id,
            decision=decision,
            approver_identity="bob",
            approver_role=ApproverRole.ADMINISTRATOR,
            decided_at=BASE_TIME,
        )

    await repo.record_decision(
        record.remediation_id, new_proposal=approved, approval=_approval(ApprovalDecision.APPROVE)
    )
    with pytest.raises(RemediationAlreadyDecidedError):
        await repo.record_decision(
            record.remediation_id,
            new_proposal=rejected,
            approval=_approval(ApprovalDecision.REJECT),
        )


async def test_record_decision_unknown_remediation(repo: RemediationRepository) -> None:
    record = make_record()
    approval = RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id="rem_ffffffffffffffff",
        decision=ApprovalDecision.APPROVE,
        approver_identity="x",
        approver_role=ApproverRole.OPERATOR,
        decided_at=BASE_TIME,
    )
    with pytest.raises(RemediationNotFoundError):
        await repo.record_decision(
            "rem_ffffffffffffffff", new_proposal=record.proposal, approval=approval
        )


async def test_history_snapshot_reports_active_and_last_completed(
    repo: RemediationRepository,
) -> None:
    tgt = make_record().proposal.target
    # a pending remediation for the key => active
    await repo.create(make_record(remediation_id="rem_00000000000000b1", incident_id="inc_c1c1c1"))
    snap = await repo.history_snapshot(
        incident_id="inc_c1c1c1",
        action_type=RemediationActionType.RESTART_SERVICE,
        target=tgt,
    )
    assert snap.active_remediation_exists(
        incident_id="inc_c1c1c1",
        action_type=RemediationActionType.RESTART_SERVICE,
        target=tgt,
    )

    # an approved one 100s ago => last_completed
    approved_rec = make_record(
        remediation_id="rem_00000000000000b2",
        incident_id="inc_c2c2c2",
        status=RemediationStatus.APPROVED,
    )
    await repo.create(approved_rec)
    ap = RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id="rem_00000000000000b2",
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        decided_at=BASE_TIME,
    )
    await repo.record_decision(
        "rem_00000000000000b2",
        new_proposal=approved_rec.proposal.model_copy(
            update={"status": RemediationStatus.APPROVED}
        ),
        approval=ap,
    )
    snap2 = await repo.history_snapshot(
        incident_id="inc_c2c2c2",
        action_type=RemediationActionType.RESTART_SERVICE,
        target=tgt,
    )
    assert (
        snap2.last_completed_at(
            incident_id="inc_c2c2c2",
            action_type=RemediationActionType.RESTART_SERVICE,
            target=tgt,
        )
        == BASE_TIME
    )
