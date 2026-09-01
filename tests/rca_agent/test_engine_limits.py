"""Agent-loop safety: the engine is bounded by deterministic limits (ADR-021)."""

from __future__ import annotations

from rca_agent.domain import InvestigationStatus, InvestigationTrigger
from tests.rca_agent.engine_harness import build_service, settings_with
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)

_ABN = ["error_rate", "latency_p95_ms"]
_HANDLER = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=20, abnormal=_ABN, score=0.93),
    related=[],
)


async def test_max_tool_calls_is_never_exceeded() -> None:
    service = build_service(_HANDLER, settings=settings_with(max_tool_calls=2))
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert out.investigation.tool_call_count <= 2
    assert out.investigation.status.is_terminal


async def test_max_evidence_items_is_never_exceeded() -> None:
    service = build_service(_HANDLER, settings=settings_with(max_evidence_items=3))
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert out.investigation.evidence_count <= 3


async def test_max_steps_bounds_the_investigation() -> None:
    service = build_service(_HANDLER, settings=settings_with(max_investigation_steps=5))
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    # the degrade chain adds a few LIMIT notes + the mandatory validate step;
    # the guarantee is "bounded, no runaway", not "exactly max_steps".
    assert out.investigation.status.is_terminal
    assert out.investigation.step_count <= 12


async def test_timeout_terminates_as_timed_out() -> None:
    service = build_service(_HANDLER, settings=settings_with(investigation_timeout_seconds=0.001))
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert out.investigation.status is InvestigationStatus.TIMED_OUT


async def test_max_hypotheses_is_capped() -> None:
    service = build_service(_HANDLER, settings=settings_with(max_hypotheses=1))
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert out.report is not None
    assert len(out.report.hypotheses) <= 1


async def test_engine_always_reaches_a_terminal_state() -> None:
    service = build_service(_HANDLER)
    out = await service.investigate(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert out.investigation.status.is_terminal
    assert out.investigation.completed_at is not None
