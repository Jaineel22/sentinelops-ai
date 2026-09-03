"""SQLAlchemy model mapping + constraints (Phase 5C, SQLite)."""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from remediation_controller.db import Database, SqlRemediationRepository
from remediation_controller.db.models import RemediationApprovalRow, RemediationRow
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.models import RemediationApproval, new_approval_id
from tests.remediation_controller.persistence_fakes import BASE_TIME, make_record


async def test_no_command_shaped_column_exists() -> None:
    cols = set(RemediationRow.__table__.columns.keys())
    for banned in ("command", "script", "shell", "cmd", "exec", "payload", "kubectl_command"):
        assert banned not in cols


async def test_round_trip_preserves_policy_decision(sqlite_remediation_db: Database) -> None:
    repo = SqlRemediationRepository(sqlite_remediation_db)
    record = make_record()
    await repo.create(record)
    got = await repo.get(record.remediation_id)
    assert got is not None
    assert got.policy_decision.policy_version == "1"
    assert got.policy_decision.outcome is record.policy_decision.outcome
    assert got.proposal.parameters == record.proposal.parameters


async def test_unique_approval_per_remediation(sqlite_remediation_db: Database) -> None:
    repo = SqlRemediationRepository(sqlite_remediation_db)
    record = make_record()
    await repo.create(record)

    def _ap() -> RemediationApproval:
        return RemediationApproval(
            approval_id=new_approval_id(),
            remediation_id=record.remediation_id,
            decision=ApprovalDecision.APPROVE,
            approver_identity="alice",
            approver_role=ApproverRole.ADMINISTRATOR,
            decided_at=BASE_TIME,
        )

    approved = record.proposal.model_copy(update={"status": RemediationStatus.APPROVED})
    await repo.record_decision(record.remediation_id, new_proposal=approved, approval=_ap())

    # a direct second insert must violate UNIQUE(remediation_id)
    async with sqlite_remediation_db.session() as session, session.begin():
        session.add(
            RemediationApprovalRow(
                id=new_approval_id(),
                remediation_id=record.remediation_id,
                decision="REJECT",
                approver_identity="mallory",
                approver_role="ADMINISTRATOR",
                reason="",
                decided_at=BASE_TIME,
                created_at=BASE_TIME,
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()


async def test_status_check_constraint(sqlite_remediation_db: Database) -> None:
    async with sqlite_remediation_db.engine.begin() as conn:
        with pytest.raises(IntegrityError):
            await conn.execute(
                text(
                    "INSERT INTO remediations (id, incident_id, trigger, proposed_by, action_type, "
                    "target_service, target_environment, parameters, risk_level, "
                    "source_recommendation, reason, expected_effect, evidence_references, status, "
                    "policy_outcome, policy_version, policy_reason_codes, policy_decision, "
                    "policy_evaluated_at, created_at, expires_at, updated_at) VALUES "
                    "('rem_0000000000000001','inc_abcdef','MANUAL','x','RESTART_SERVICE',"
                    "'orders-service','development','{}','MEDIUM','','','','[]','NONSENSE',"
                    "'ALLOW','1','[]','{}','2026-09-02','2026-09-02','2026-09-02','2026-09-02')"
                )
            )
