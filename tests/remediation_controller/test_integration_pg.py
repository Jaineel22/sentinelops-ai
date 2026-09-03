"""PostgreSQL integration (Phase 5C). Deselected by default (``-m 'not integration'``).

    docker compose up -d postgres
    (cd services/remediation-controller && alembic upgrade head)
    DB_TEST_URL=postgresql+asyncpg://sentinelops:sentinelops@localhost:5432/sentinelops \
    pytest -m integration tests/remediation_controller/test_integration_pg.py
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from remediation_controller.audit.model import AuditEventType
from remediation_controller.db import Database, SqlRemediationRepository
from remediation_controller.domain import ApprovalDecision, ApproverRole, RemediationStatus
from remediation_controller.domain.enums import ExecutionStatus
from remediation_controller.domain.proposal import RcaRecommendedActionInput
from remediation_controller.executor.simulation import LocalSimulationExecutor, SimulationState
from remediation_controller.recovery.config import RecoveryVerificationConfig
from remediation_controller.recovery.model import VerificationStatus
from remediation_controller.repository import (
    RecoveryVerificationConflictError,
    RemediationAlreadyDecidedError,
    RemediationExecutionConflictError,
)
from remediation_controller.service import RemediationService
from tests.remediation_controller.persistence_fakes import BASE_TIME

_FAST_VERIFY = RecoveryVerificationConfig(timeout_seconds=6, poll_interval_seconds=1.0)

pytestmark = pytest.mark.integration

_PG_URL = os.environ.get("DB_TEST_URL")
pg = pytest.mark.skipif(_PG_URL is None, reason="set DB_TEST_URL to a Postgres DB")

_INCIDENT = "inc_00112233aabbccdd"


@pytest_asyncio.fixture
async def pg_repo() -> AsyncIterator[SqlRemediationRepository]:
    assert _PG_URL is not None
    db = Database(_PG_URL)
    async with db.engine.begin() as conn:
        from remediation_controller.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield SqlRemediationRepository(db)
    finally:
        await db.dispose()


@pg
async def test_concurrent_approvals_exactly_one_wins(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo)
    record = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    assert record.status is RemediationStatus.PENDING_APPROVAL

    async def _approve(identity: str) -> str:
        try:
            out = await svc.decide(
                record.remediation_id,
                decision=ApprovalDecision.APPROVE,
                approver_identity=identity,
                approver_role=ApproverRole.ADMINISTRATOR,
                now=BASE_TIME,
            )
            return f"ok:{out.status}"
        except RemediationAlreadyDecidedError:
            return "already_decided"
        except Exception as exc:  # InvalidRemediationState from the losing racer
            return f"rejected:{type(exc).__name__}"

    results = await asyncio.gather(*[_approve(f"user-{i}") for i in range(5)])
    winners = [r for r in results if r.startswith("ok:")]
    assert len(winners) == 1, results
    assert all(r != "ok:" for r in results)

    final = await pg_repo.get(record.remediation_id)
    assert final is not None
    assert final.status is RemediationStatus.APPROVED
    assert final.approval is not None  # exactly one immutable decision


@pg
async def test_history_snapshot_across_a_fresh_connection(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    snap = await pg_repo.history_snapshot(
        incident_id=_INCIDENT,
        action_type=rec.proposal.action_type,
        target=rec.proposal.target,
    )
    assert snap.active_remediation_exists(
        incident_id=_INCIDENT,
        action_type=rec.proposal.action_type,
        target=rec.proposal.target,
    )


@pg
async def test_concurrent_executions_exactly_one_wins(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME,
    )

    async def _exec() -> str:
        try:
            out = await svc.execute(rec.remediation_id, now=BASE_TIME)
            return f"ok:{out.record.status}"
        except RemediationExecutionConflictError:
            return "conflict"
        except Exception as exc:
            return f"rejected:{type(exc).__name__}"

    results = await asyncio.gather(*[_exec() for _ in range(5)])
    assert sum(1 for r in results if r.startswith("ok:")) == 1, results

    final = await pg_repo.get(rec.remediation_id)
    assert final is not None
    assert final.status is RemediationStatus.EXECUTED
    assert final.execution is not None and final.execution.status is ExecutionStatus.SUCCEEDED


# --- Phase 5E: append-only audit trail --------------------------------
@pg
async def test_audit_trail_persists_ordered_and_transactionally(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME,
    )
    await svc.execute(rec.remediation_id, now=BASE_TIME)

    # read back through a fresh connection
    events = await pg_repo.list_audit_events(rec.remediation_id)
    assert [e.event_type for e in events] == [
        AuditEventType.PROPOSAL_CREATED,
        AuditEventType.POLICY_EVALUATED,
        AuditEventType.APPROVED,
        AuditEventType.EXECUTION_REQUESTED,
        AuditEventType.EXECUTION_STARTED,
        AuditEventType.EXECUTION_SUCCEEDED,
    ]
    # the remediation reached EXECUTED and its audit records are all present:
    # the transition and its audit rows were committed together.
    final = await pg_repo.get(rec.remediation_id)
    assert final is not None and final.status is RemediationStatus.EXECUTED

    page = await pg_repo.list_audit_events(rec.remediation_id, limit=2, offset=2)
    assert [e.event_type for e in page] == [
        AuditEventType.APPROVED,
        AuditEventType.EXECUTION_REQUESTED,
    ]


@pg
async def test_concurrent_approvals_yield_exactly_one_decision_audit_event(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo)
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )

    async def _approve(identity: str) -> None:
        with contextlib.suppress(Exception):  # losing racers raise; only the winner matters here
            await svc.decide(
                rec.remediation_id,
                decision=ApprovalDecision.APPROVE,
                approver_identity=identity,
                approver_role=ApproverRole.ADMINISTRATOR,
                now=BASE_TIME,
            )

    await asyncio.gather(*[_approve(f"user-{i}") for i in range(5)])

    events = await pg_repo.list_audit_events(rec.remediation_id)
    decision_events = [
        e for e in events if e.event_type in (AuditEventType.APPROVED, AuditEventType.REJECTED)
    ]
    assert len(decision_events) == 1  # the losers' audit rows rolled back with their transactions


@pg
async def test_migrated_schema_rejects_audit_update_and_delete() -> None:
    """Applies the real Alembic lineage (0001→0003) and asserts the PostgreSQL
    append-only trigger from migration 0003 rejects UPDATE and DELETE."""

    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError

    assert _PG_URL is not None
    os.environ["DB_URL"] = _PG_URL
    db = Database(_PG_URL)
    async with db.engine.begin() as conn:
        from remediation_controller.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version_remediation")

    cfg = Config()
    cfg.set_main_option("script_location", "services/remediation-controller/migrations")
    cfg.set_main_option("sqlalchemy.url", _PG_URL)
    # alembic env.py runs its own asyncio loop; run it off the test's loop.
    await asyncio.to_thread(command.upgrade, cfg, "head")

    try:
        svc = RemediationService(repository=SqlRemediationRepository(db))
        rec = await svc.propose(
            incident_id=_INCIDENT,
            recommendation=RcaRecommendedActionInput(
                action_type="RESTART_SERVICE", target_service="orders-service"
            ),
            incident_severity="HIGH",
            now=BASE_TIME,
        )
        events = await svc.list_audit_events(rec.remediation_id)
        assert events and len(events) == 2  # rows inserted through the migrated schema

        with pytest.raises(DBAPIError):
            async with db.engine.begin() as conn:
                await conn.execute(
                    sa.text("UPDATE remediation_audit_events SET reason = 'tampered'")
                )
        with pytest.raises(DBAPIError):
            async with db.engine.begin() as conn:
                await conn.execute(sa.text("DELETE FROM remediation_audit_events"))

        # the rows are still intact and unmodified
        after = await svc.list_audit_events(rec.remediation_id)
        assert after is not None
        assert [e.reason for e in after] == [e.reason for e in events]
    finally:
        await db.dispose()


# --- Phase 5F: recovery verification ---------------------------------
async def _to_executed(svc: RemediationService) -> str:
    rec = await svc.propose(
        incident_id=_INCIDENT,
        recommendation=RcaRecommendedActionInput(
            action_type="RESTART_SERVICE", target_service="orders-service"
        ),
        incident_severity="HIGH",
        now=BASE_TIME,
    )
    await svc.decide(
        rec.remediation_id,
        decision=ApprovalDecision.APPROVE,
        approver_identity="alice",
        approver_role=ApproverRole.ADMINISTRATOR,
        now=BASE_TIME,
    )
    await svc.execute(rec.remediation_id, now=BASE_TIME)
    return rec.remediation_id


@pg
async def test_recovery_verification_persists_and_transitions(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo, verify_config=_FAST_VERIFY)
    rid = await _to_executed(svc)

    out = await svc.verify_recovery(rid, now=BASE_TIME)
    assert out.record.status is RemediationStatus.RECOVERED

    # read back through a fresh connection
    final = await pg_repo.get(rid)
    assert final is not None
    assert final.status is RemediationStatus.RECOVERED
    assert final.verification is not None
    assert final.verification.status is VerificationStatus.RECOVERED
    assert final.verification.checks  # structured evidence persisted
    assert final.verification.verifier_type == "DETERMINISTIC_LOCAL"

    events = await pg_repo.list_audit_events(rid)
    assert [e.event_type for e in events][-2:] == [
        AuditEventType.VERIFICATION_STARTED,
        AuditEventType.VERIFICATION_SUCCEEDED,
    ]
    assert events[-1].verification_id == final.verification.verification_id


@pg
async def test_recovery_failed_persists(pg_repo: SqlRemediationRepository) -> None:
    state = SimulationState()
    state.inject_fault("orders-service", chronic=True)
    svc = RemediationService(
        repository=pg_repo,
        executor=LocalSimulationExecutor(state),
        verify_config=_FAST_VERIFY,
    )
    rid = await _to_executed(svc)

    out = await svc.verify_recovery(rid, now=BASE_TIME)
    assert out.record.status is RemediationStatus.RECOVERY_FAILED

    final = await pg_repo.get(rid)
    assert final is not None and final.status is RemediationStatus.RECOVERY_FAILED
    assert final.verification is not None
    assert final.verification.status is VerificationStatus.RECOVERY_FAILED
    assert final.verification.failure_reason


@pg
async def test_concurrent_verifications_exactly_one_wins(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo, verify_config=_FAST_VERIFY)
    rid = await _to_executed(svc)

    async def _verify() -> str:
        try:
            out = await svc.verify_recovery(rid, now=BASE_TIME)
            return f"ok:{out.record.status}:{out.replayed}"
        except RecoveryVerificationConflictError:
            return "conflict"
        except Exception as exc:
            return f"rejected:{type(exc).__name__}"

    results = await asyncio.gather(*[_verify() for _ in range(5)])
    fresh = [r for r in results if r.startswith("ok:") and r.endswith(":False")]
    assert len(fresh) == 1, results

    final = await pg_repo.get(rid)
    assert final is not None and final.status is RemediationStatus.RECOVERED
    events = await pg_repo.list_audit_events(rid)
    started = [e for e in events if e.event_type is AuditEventType.VERIFICATION_STARTED]
    assert len(started) == 1  # losers rolled back with their transactions


@pg
async def test_repeated_verification_replays_over_a_fresh_connection(
    pg_repo: SqlRemediationRepository,
) -> None:
    svc = RemediationService(repository=pg_repo, verify_config=_FAST_VERIFY)
    rid = await _to_executed(svc)
    first = await svc.verify_recovery(rid, now=BASE_TIME)
    second = await svc.verify_recovery(rid, now=BASE_TIME)
    assert second.replayed is True
    assert second.result.verification_id == first.result.verification_id

    events = await pg_repo.list_audit_events(rid)
    assert sum(1 for e in events if e.event_type is AuditEventType.VERIFICATION_STARTED) == 1


@pg
async def test_migration_0004_creates_verification_table_and_widens_audit_check() -> None:
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    assert _PG_URL is not None
    os.environ["DB_URL"] = _PG_URL
    db = Database(_PG_URL)
    async with db.engine.begin() as conn:
        from remediation_controller.db.models import Base

        await conn.run_sync(Base.metadata.drop_all)
        await conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version_remediation")

    cfg = Config()
    cfg.set_main_option("script_location", "services/remediation-controller/migrations")
    cfg.set_main_option("sqlalchemy.url", _PG_URL)
    await asyncio.to_thread(command.upgrade, cfg, "head")

    try:
        svc = RemediationService(
            repository=SqlRemediationRepository(db), verify_config=_FAST_VERIFY
        )
        rid = await _to_executed(svc)
        out = await svc.verify_recovery(rid, now=BASE_TIME)
        assert out.record.status is RemediationStatus.RECOVERED

        async with db.engine.begin() as conn:
            n = await conn.scalar(sa.text("SELECT count(*) FROM remediation_verifications"))
            assert n == 1
            # the widened CHECK accepts the new recovery audit events
            kinds = (
                (
                    await conn.execute(
                        sa.text(
                            "SELECT event_type FROM remediation_audit_events "
                            "WHERE event_type LIKE 'VERIFICATION%'"
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert set(kinds) == {"VERIFICATION_STARTED", "VERIFICATION_SUCCEEDED"}
    finally:
        await db.dispose()
