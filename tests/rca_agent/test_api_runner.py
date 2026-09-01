"""``BackgroundInvestigationRunner`` — the async execution path (Sub-phase 4E).

Verifies POST does not block on the graph: ``submit`` returns a PENDING
investigation immediately and the run completes on a background task that
``drain`` waits for. No DB transaction spans the run.
"""

from __future__ import annotations

from rca_agent.api.runner import BackgroundInvestigationRunner
from rca_agent.domain import InvestigationStatus, InvestigationTrigger
from rca_agent.metrics import RcaMetrics
from rca_agent.repository import InMemoryInvestigationRepository
from tests.rca_agent.engine_harness import build_service
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)

_ABN = ["error_rate", "latency_p95_ms"]
_HANDLER = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


def _runner(repo: InMemoryInvestigationRepository) -> BackgroundInvestigationRunner:
    return BackgroundInvestigationRunner(
        build_service(_HANDLER, repository=repo), metrics=RcaMetrics(), run_in_background=True
    )


async def test_submit_returns_immediately_then_completes_in_background() -> None:
    repo = InMemoryInvestigationRepository()
    runner = _runner(repo)

    inv, created = await runner.submit(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert created is True
    assert inv.status is InvestigationStatus.PENDING  # not blocked on the graph
    assert runner.in_flight == 1

    await runner.drain(timeout=30.0)
    assert runner.in_flight == 0

    done = await repo.get_investigation(inv.id)
    assert done is not None and done.status.is_terminal
    assert (await repo.get_report(inv.id)) is not None


async def test_submit_is_idempotent_while_running_and_after() -> None:
    repo = InMemoryInvestigationRepository()
    runner = _runner(repo)

    inv, created = await runner.submit(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert created

    inv2, created2 = await runner.submit(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert created2 is False and inv2.id == inv.id

    await runner.drain(timeout=30.0)

    inv3, created3 = await runner.submit(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert created3 is False and inv3.id == inv.id  # finished one is returned, not re-run


async def test_drain_with_nothing_in_flight_is_a_noop() -> None:
    runner = _runner(InMemoryInvestigationRepository())
    await runner.drain(timeout=1.0)
    assert runner.in_flight == 0


async def test_synchronous_mode_completes_before_submit_returns() -> None:
    repo = InMemoryInvestigationRepository()
    runner = BackgroundInvestigationRunner(
        build_service(_HANDLER, repository=repo), metrics=RcaMetrics(), run_in_background=False
    )
    inv, created = await runner.submit(INCIDENT_ID, trigger=InvestigationTrigger.MANUAL)
    assert created and inv.status.is_terminal
