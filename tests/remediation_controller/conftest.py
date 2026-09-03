"""Fixtures + factories for remediation-controller unit tests.

Phase 5A/5B are pure domain + policy (no infra). Phase 5C adds a SQLite-backed
repository fixture; PostgreSQL is only exercised under ``-m integration``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

from remediation_controller.domain import (
    ApprovalDecision,
    ApproverRole,
    RemediationActionType,
    RemediationApproval,
    RemediationProposal,
    RemediationTrigger,
    RiskLevel,
    ServiceTarget,
    new_approval_id,
    new_remediation_id,
)
from remediation_controller.domain.proposal import RcaRecommendedActionInput

_BASE = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_INCIDENT_ID = "inc_00112233aabbccdd"
_INVESTIGATION_ID = "rca_00112233aabbccdd"


def make_target(
    *, service_name: str = "orders-service", environment: str = "development"
) -> ServiceTarget:
    return ServiceTarget(service_name=service_name, environment=environment)


def make_rca_input(
    *,
    action_type: str = "RESTART_SERVICE",
    target_service: str | None = "orders-service",
    description: str = "orders-service p95 latency and error rate rose together for 4 windows.",
    rationale: str = "Connection pool saturation; a restart clears it.",
    evidence_ids: tuple[str, ...] = ("ev_001", "ev_002"),
) -> RcaRecommendedActionInput:
    return RcaRecommendedActionInput(
        action_type=action_type,
        target_service=target_service,
        description=description,
        rationale=rationale,
        evidence_ids=evidence_ids,
    )


def make_proposal(
    *,
    remediation_id: str | None = None,
    incident_id: str = _INCIDENT_ID,
    investigation_id: str | None = _INVESTIGATION_ID,
    action_type: RemediationActionType = RemediationActionType.RESTART_SERVICE,
    target: ServiceTarget | None = None,
    parameters: dict[str, str | int | bool] | None = None,
    risk_level: RiskLevel = RiskLevel.MEDIUM,
    trigger: RemediationTrigger = RemediationTrigger.RCA_RECOMMENDATION,
    proposed_by: str = "rca-agent",
) -> RemediationProposal:
    return RemediationProposal(
        remediation_id=remediation_id or new_remediation_id(),
        incident_id=incident_id,
        investigation_id=investigation_id,
        trigger=trigger,
        proposed_by=proposed_by,
        action_type=action_type,
        target=target or make_target(),
        parameters=parameters or {},
        risk_level=risk_level,
        reason="derived from RCA",
        expected_effect="service restarted",
        evidence_references=("ev_001",),
        created_at=_BASE,
        expires_at=_BASE + timedelta(hours=1),
    )


def make_approval(
    *,
    remediation_id: str,
    decision: ApprovalDecision = ApprovalDecision.APPROVE,
    approver_identity: str = "alice@example.com",
    approver_role: ApproverRole = ApproverRole.INCIDENT_RESPONDER,
    reason: str = "reviewed the RCA; restart is safe",
) -> RemediationApproval:
    return RemediationApproval(
        approval_id=new_approval_id(),
        remediation_id=remediation_id,
        decision=decision,
        approver_identity=approver_identity,
        approver_role=approver_role,
        reason=reason,
        decided_at=_BASE,
    )


@pytest.fixture
def base_time() -> datetime:
    return _BASE


@pytest.fixture
def incident_id() -> str:
    return _INCIDENT_ID


@pytest.fixture
def investigation_id() -> str:
    return _INVESTIGATION_ID


@pytest.fixture
def target_factory() -> Callable[..., ServiceTarget]:
    return make_target


@pytest.fixture
def rca_input_factory() -> Callable[..., RcaRecommendedActionInput]:
    return make_rca_input


@pytest.fixture
def proposal_factory() -> Callable[..., RemediationProposal]:
    return make_proposal


@pytest.fixture
def approval_factory() -> Callable[..., RemediationApproval]:
    return make_approval


# --- Phase 5C: persistence fixtures -----------------------------------
@pytest_asyncio.fixture
async def sqlite_remediation_db(tmp_path: Path) -> AsyncIterator[object]:
    from remediation_controller.db import Database

    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'remediation.db'}")
    await db.create_all()
    try:
        yield db
    finally:
        await db.dispose()
