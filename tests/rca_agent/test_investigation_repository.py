"""InvestigationRepository — in-memory and SQLite, proven equivalent."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

import pytest
import pytest_asyncio

from rca_agent.db import Database, SqlInvestigationRepository
from rca_agent.domain import Confidence, InvestigationStatus, InvestigationTrigger
from rca_agent.domain import InvestigationStatus as _IS
from rca_agent.domain import StepKind as _SK
from rca_agent.repository import (
    DuplicateActiveInvestigationError,
    InMemoryInvestigationRepository,
    InvestigationRepository,
)
from rca_agent.schemas import InvestigationStep, RCAReport


@pytest_asyncio.fixture
async def sql_repo(sqlite_rca_db: Database) -> AsyncIterator[SqlInvestigationRepository]:
    yield SqlInvestigationRepository(sqlite_rca_db)


@pytest.fixture(params=["memory", "sql"])
def repo(
    request: pytest.FixtureRequest, sql_repo: SqlInvestigationRepository
) -> InvestigationRepository:
    return InMemoryInvestigationRepository() if request.param == "memory" else sql_repo


async def test_begin_then_complete_round_trip(
    repo: InvestigationRepository,
    rca_report_factory: Callable[..., RCAReport],
    evidence_factory: Callable[..., object],
) -> None:
    inv = await repo.begin_investigation(
        "inc_abc123", trigger=InvestigationTrigger.EVENT, mode="mock"
    )
    assert inv.status is InvestigationStatus.PENDING

    report = rca_report_factory(incident_id="inc_abc123", investigation_id=inv.id)
    steps = [
        InvestigationStep(
            seq=1,
            kind=_SK.PLAN,
            phase=_IS.PLANNING,
            description="planned",
            at=report.evidence[0].collected_at,
        ),
        InvestigationStep(
            seq=2,
            kind=_SK.TOOL_CALL,
            phase=_IS.COLLECTING_EVIDENCE,
            description="called get_incident",
            tool_name="get_incident",
            evidence_ids=[report.evidence[0].id],
            at=report.evidence[0].collected_at,
        ),
    ]
    completed = await repo.complete_investigation(
        inv.id,
        status=InvestigationStatus.COMPLETED,
        termination_reason="analysis_complete",
        overall_confidence=str(Confidence.MEDIUM),
        model=None,
        steps=steps,
        evidence=list(report.evidence),
        report=report,
    )
    assert completed.status is InvestigationStatus.COMPLETED
    assert completed.tool_call_count == 1
    assert completed.step_count == 2
    assert completed.evidence_count == len(report.evidence)

    assert (await repo.get_investigation(inv.id)).status is InvestigationStatus.COMPLETED  # type: ignore[union-attr]
    got_steps = await repo.get_steps(inv.id)
    assert got_steps is not None and [s.seq for s in got_steps] == [1, 2]
    got_ev = await repo.get_evidence(inv.id)
    assert got_ev is not None and {e.id for e in got_ev} == {e.id for e in report.evidence}
    got_report = await repo.get_report(inv.id)
    assert got_report is not None and got_report.root_cause is not None
    assert got_report.investigation_id == inv.id


async def test_one_active_investigation_per_incident(repo: InvestigationRepository) -> None:
    await repo.begin_investigation("inc_dup0001", trigger=InvestigationTrigger.EVENT, mode="mock")
    with pytest.raises(DuplicateActiveInvestigationError):
        await repo.begin_investigation(
            "inc_dup0001", trigger=InvestigationTrigger.MANUAL, mode="mock"
        )


async def test_new_investigation_allowed_after_terminal(
    repo: InvestigationRepository, rca_report_factory: Callable[..., RCAReport]
) -> None:
    first = await repo.begin_investigation(
        "inc_x0000001", trigger=InvestigationTrigger.EVENT, mode="mock"
    )
    await repo.complete_investigation(
        first.id,
        status=InvestigationStatus.FAILED,
        termination_reason="boom",
        overall_confidence=str(Confidence.UNKNOWN),
        model=None,
        steps=[],
        evidence=[],
        report=None,
    )
    second = await repo.begin_investigation(
        "inc_x0000001", trigger=InvestigationTrigger.MANUAL, mode="mock"
    )
    assert second.id != first.id
    latest = await repo.get_latest_investigation("inc_x0000001")
    assert latest is not None and latest.id == second.id


async def test_get_active_investigation(repo: InvestigationRepository) -> None:
    assert await repo.get_active_investigation("inc_none00001") is None
    inv = await repo.begin_investigation(
        "inc_act00001", trigger=InvestigationTrigger.EVENT, mode="mock"
    )
    active = await repo.get_active_investigation("inc_act00001")
    assert active is not None and active.id == inv.id


async def test_unknown_investigation_reads_are_none(repo: InvestigationRepository) -> None:
    assert await repo.get_investigation("rca_missing") is None
    assert await repo.get_steps("rca_missing") is None
    assert await repo.get_evidence("rca_missing") is None
    assert await repo.get_report("rca_missing") is None
