"""End-to-end investigation in mock mode (ADR-021).

incident -> plan -> tools -> evidence -> hypotheses -> verify -> RCA -> validate
-> persistence, deterministic and network-free.
"""

from __future__ import annotations

from itertools import pairwise
from pathlib import Path

from rca_agent.db import Database, SqlInvestigationRepository
from rca_agent.domain import InvestigationStatus, InvestigationTrigger
from rca_agent.repository import InMemoryInvestigationRepository
from rca_agent.state_machine import can_transition
from tests.rca_agent.engine_harness import build_service
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)

# --- Scenario A: a supported latency/error incident -------------------
_ABN = ["error_rate", "latency_p95_ms"]
_DB_LATENCY = scenario_handler(
    incident=make_incident(severity="HIGH", anomaly_count=4, abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


async def test_scenario_a_completes_with_a_root_cause() -> None:
    service = build_service(_DB_LATENCY)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)

    assert out.investigation.status is InvestigationStatus.COMPLETED
    assert out.report is not None
    report = out.report
    assert report.status is InvestigationStatus.COMPLETED
    assert report.root_cause is not None
    assert report.root_cause.evidence_ids  # grounded
    assert report.overall_confidence.rank <= report.root_cause.confidence.rank
    assert report.recommended_action.requires_human_approval is True
    assert report.uncertainty.strip()
    assert report.hypotheses  # explicit hypotheses were formed
    # every cited evidence id was actually collected
    known = {e.id for e in report.evidence}
    for f in report.findings:
        assert set(f.evidence_ids) <= known
    # unavailable sources are reported honestly, not fabricated
    assert any("get_recent_logs" in s for s in report.unavailable_evidence_sources)


async def test_scenario_a_records_a_bounded_operational_trace() -> None:
    service = build_service(_DB_LATENCY)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)

    kinds = [s.kind for s in out.steps]
    assert "PLAN" in kinds and "TOOL_CALL" in kinds and "ANALYSIS" in kinds
    assert "VERIFICATION" in kinds and "RCA" in kinds and "VALIDATION" in kinds
    assert out.investigation.tool_call_count <= 12
    assert out.investigation.step_count <= 25
    # steps carry only concise operational text, never chain-of-thought markers
    for s in out.steps:
        assert "chain of thought" not in s.description.lower()
        assert "reasoning:" not in s.description.lower()


async def test_engine_respects_the_investigation_state_machine() -> None:
    service = build_service(_DB_LATENCY)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    phases = [s.phase for s in out.steps]
    for a, b in pairwise(phases):
        assert a == b or can_transition(a, b), f"illegal phase move {a} -> {b}"


# --- Scenario B: insufficient evidence -------------------------------
_THIN = scenario_handler(
    incident=make_incident(severity="LOW", anomaly_count=0, abnormal=[]),
    anomalies=[],  # the incident API returns no anomaly windows
)


async def test_scenario_b_returns_insufficient_evidence_not_a_guess() -> None:
    service = build_service(_THIN)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)

    assert out.investigation.status is InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert out.report is not None
    assert out.report.status is InvestigationStatus.INSUFFICIENT_EVIDENCE
    assert out.report.root_cause is None
    assert out.report.uncertainty.strip()


# --- persistence ---------------------------------------------------
async def test_investigation_is_persisted_in_memory() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_DB_LATENCY, repository=repo)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.EVENT)

    stored = await repo.get_investigation(out.investigation.id)
    assert stored is not None and stored.status is InvestigationStatus.COMPLETED
    steps = await repo.get_steps(out.investigation.id)
    evidence = await repo.get_evidence(out.investigation.id)
    report = await repo.get_report(out.investigation.id)
    assert steps and evidence and report is not None
    assert report.root_cause is not None


async def test_investigation_is_persisted_in_sqlite(tmp_path: Path) -> None:
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'e2e.db'}")
    await db.create_all()
    try:
        repo = SqlInvestigationRepository(db)
        service = build_service(_DB_LATENCY, repository=repo)
        out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.EVENT)

        assert out.investigation.status is InvestigationStatus.COMPLETED
        report = await repo.get_report(out.investigation.id)
        assert report is not None and report.root_cause is not None
        assert report.investigation_id == out.investigation.id
        steps = await repo.get_steps(out.investigation.id)
        assert steps and [s.seq for s in steps] == sorted(s.seq for s in steps)
        evidence = await repo.get_evidence(out.investigation.id)
        assert evidence and all(e.id.startswith("ev_") for e in evidence)
    finally:
        await db.dispose()


# --- idempotency --------------------------------------------------
async def test_second_investigation_while_one_is_active_is_not_duplicated() -> None:
    repo = InMemoryInvestigationRepository()
    # begin one and leave it active (PENDING)
    active = await repo.begin_investigation(
        INCIDENT_ID, trigger=InvestigationTrigger.EVENT, mode="mock"
    )
    service = build_service(_DB_LATENCY, repository=repo)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.EVENT)
    assert out.investigation.id == active.id
    assert out.report is None
    assert out.already_running


async def test_re_investigation_after_completion_is_allowed() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_DB_LATENCY, repository=repo)
    first = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    second = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert first.investigation.id != second.investigation.id
    assert second.investigation.status is InvestigationStatus.COMPLETED
