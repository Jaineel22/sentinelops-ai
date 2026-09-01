"""``IncidentEventConsumer`` handler behaviour (Sub-phase 4E).

No broker: ``consumer.handle(envelope, record)`` is driven directly. The
investigation runs for real (mock LLM + fake Incident API) through the
application service — the consumer stays a thin translator.
"""

from __future__ import annotations

import pytest

from rca_agent.config import Settings
from rca_agent.domain import InvestigationStatus, InvestigationTrigger
from rca_agent.engine import InvestigationService
from rca_agent.kafka.consumer import IncidentEventConsumer
from rca_agent.metrics import RcaMetrics
from rca_agent.repository import InMemoryInvestigationRepository
from sentinelops_common.kafka import KafkaJsonProducer, MessageRejected
from tests.rca_agent.engine_harness import build_service
from tests.rca_agent.incident_api_fakes import (
    INCIDENT_ID,
    make_anomaly_windows,
    make_incident,
    scenario_handler,
)
from tests.rca_agent.kafka_fakes import FakeRecord, incident_lifecycle_envelope

_ABN = ["error_rate", "latency_p95_ms"]
_HANDLER = scenario_handler(
    incident=make_incident(abnormal=_ABN),
    anomalies=make_anomaly_windows(count=4, abnormal=_ABN, score=0.93),
)


def _consumer(
    service: InvestigationService, *, metrics: RcaMetrics | None = None
) -> IncidentEventConsumer:
    return IncidentEventConsumer(
        Settings(),
        service,
        dlq_producer=KafkaJsonProducer("localhost:0", client_id="test"),
        metrics=metrics or RcaMetrics(),
    )


async def test_incident_opened_triggers_and_persists_an_investigation() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_HANDLER, repository=repo)
    await _consumer(service).handle(incident_lifecycle_envelope(), FakeRecord())

    inv = await repo.get_latest_investigation(INCIDENT_ID)
    assert inv is not None
    assert inv.status.is_terminal
    assert inv.trigger is InvestigationTrigger.EVENT
    report = await repo.get_report(inv.id)
    assert report is not None
    assert report.recommended_action.requires_human_approval is True


async def test_duplicate_incident_opened_does_not_start_a_second_investigation() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_HANDLER, repository=repo)
    consumer = _consumer(service)

    env = incident_lifecycle_envelope()
    await consumer.handle(env, FakeRecord())
    first = await repo.get_latest_investigation(INCIDENT_ID)
    assert first is not None

    # redelivery (same event) + a fresh event id — both must be no-ops
    await consumer.handle(env, FakeRecord())
    await consumer.handle(incident_lifecycle_envelope(), FakeRecord())

    # still exactly the first investigation (a second would be the "latest")
    latest = await repo.get_latest_investigation(INCIDENT_ID)
    assert latest is not None and latest.id == first.id


async def test_non_opened_lifecycle_events_are_ignored() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_HANDLER, repository=repo)
    consumer = _consumer(service)

    for event_type, change in [
        ("incident.updated", "evidence-added"),
        ("incident.resolved", "resolved"),
    ]:
        await consumer.handle(
            incident_lifecycle_envelope(event_type=event_type, change=change), FakeRecord()
        )

    assert await repo.get_latest_investigation(INCIDENT_ID) is None


async def test_malformed_incident_opened_is_rejected_to_the_dlq() -> None:
    service = build_service(_HANDLER, repository=InMemoryInvestigationRepository())
    with pytest.raises(MessageRejected):
        await _consumer(service).handle(
            incident_lifecycle_envelope(raw_payload={"garbage": True}), FakeRecord()
        )


async def test_unsupported_event_version_is_rejected_to_the_dlq() -> None:
    service = build_service(_HANDLER, repository=InMemoryInvestigationRepository())
    with pytest.raises(MessageRejected):
        await _consumer(service).handle(incident_lifecycle_envelope(event_version=99), FakeRecord())


async def test_a_bogus_incident_id_in_the_event_is_rejected_not_investigated() -> None:
    repo = InMemoryInvestigationRepository()
    service = build_service(_HANDLER, repository=repo)
    with pytest.raises(MessageRejected):
        await _consumer(service).handle(
            incident_lifecycle_envelope(incident_id="../../etc/passwd"), FakeRecord()
        )
    assert await repo.get_latest_investigation("../../etc/passwd") is None


async def test_failed_investigation_still_counts_as_handled() -> None:
    # Incident API down at load -> the investigation terminates FAILED, but the
    # event is processed (no exception escapes handle -> no infinite redelivery).
    import httpx

    def _down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("incident API is down")

    repo = InMemoryInvestigationRepository()
    service = build_service(_down, repository=repo)
    await _consumer(service).handle(incident_lifecycle_envelope(), FakeRecord())

    inv = await repo.get_latest_investigation(INCIDENT_ID)
    assert inv is not None and inv.status is InvestigationStatus.FAILED
