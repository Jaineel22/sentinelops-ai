"""AnomalyProcessor — correlation + dedup + severity, against the in-memory repo.

These are the section-23/24 scenarios: same service -> one incident with N
evidence; different service -> separate incidents; duplicate event -> no change.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from incident_correlator.correlation import CorrelationConfig
from incident_correlator.domain import AnomalySignal, IncidentStatus, Severity
from incident_correlator.processor import AnomalyProcessor, ProcessResult
from incident_correlator.repository import IncidentFilter, InMemoryIncidentRepository


@pytest.fixture
def processor(repo: InMemoryIncidentRepository) -> AnomalyProcessor:
    return AnomalyProcessor(repo, correlation_config=CorrelationConfig(window_seconds=300))


async def test_first_anomaly_creates_one_incident(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    outcome = await processor.process(signal_factory(abnormal_signals=["latency_p95_ms"]))
    assert outcome.result is ProcessResult.CREATED

    incidents = await repo.list_incidents(IncidentFilter())
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.status is IncidentStatus.OPEN
    assert inc.anomaly_count == 1
    assert len(inc.evidence) == 1
    assert len(inc.history) == 1 and inc.history[0].to_status is IncidentStatus.OPEN


async def test_related_anomalies_correlate_into_one_incident(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    await processor.process(
        signal_factory(
            offset_seconds=0,
            abnormal_signals=["latency_p95_ms"],
            signals={"latency_p95_ms": 800.0, "error_rate": 0.0},
        )
    )
    await processor.process(
        signal_factory(
            offset_seconds=10,
            abnormal_signals=["error_rate"],
            signals={"latency_p95_ms": 50.0, "error_rate": 0.25},
        )
    )
    await processor.process(
        signal_factory(
            offset_seconds=20,
            abnormal_signals=["publish_error_rate"],
            signals={"publish_error_rate": 0.3, "error_rate": 0.0},
        )
    )

    incidents = await repo.list_incidents(IncidentFilter())
    assert len(incidents) == 1
    inc = incidents[0]
    assert inc.anomaly_count == 3
    assert len(inc.evidence) == 3
    assert inc.distinct_abnormal_signals == 3
    # 3 windows + 2 distinct signals + high error rate -> HIGH (or CRITICAL if error_rate>=.3)
    assert inc.severity.rank >= Severity.HIGH.rank


async def test_different_service_makes_a_second_incident(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    await processor.process(signal_factory(service="orders-service"))
    await processor.process(signal_factory(service="payments-service"))
    assert len(await repo.list_incidents(IncidentFilter())) == 2


async def test_different_environment_makes_a_second_incident(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    await processor.process(signal_factory(environment="development"))
    await processor.process(signal_factory(environment="staging"))
    assert len(await repo.list_incidents(IncidentFilter())) == 2


async def test_duplicate_event_is_a_noop(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    sig = signal_factory(event_id="evt-1")
    first = await processor.process(sig)
    assert first.result is ProcessResult.CREATED

    second = await processor.process(sig)  # same event_id, redelivered
    assert second.result is ProcessResult.DUPLICATE

    incidents = await repo.list_incidents(IncidentFilter())
    assert len(incidents) == 1
    assert incidents[0].anomaly_count == 1
    assert len(incidents[0].evidence) == 1


async def test_duplicate_after_append_does_not_double_count(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    a = signal_factory(event_id="a", offset_seconds=0)
    b = signal_factory(event_id="b", offset_seconds=10)
    await processor.process(a)
    await processor.process(b)
    await processor.process(b)  # redelivery of the appended event

    inc = (await repo.list_incidents(IncidentFilter()))[0]
    assert inc.anomaly_count == 2
    assert len(inc.evidence) == 2


async def test_stale_incident_is_superseded(
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    proc = AnomalyProcessor(repo, correlation_config=CorrelationConfig(window_seconds=60))
    await proc.process(signal_factory(event_id="old", offset_seconds=0))
    out = await proc.process(signal_factory(event_id="new", offset_seconds=600))
    assert out.result is ProcessResult.SUPERSEDED

    everything = await repo.list_incidents(IncidentFilter())
    assert len(everything) == 2
    resolved = [i for i in everything if i.status is IncidentStatus.RESOLVED]
    active = [i for i in everything if i.status.is_active]
    assert len(resolved) == 1 and resolved[0].resolution == "auto:stale"
    assert len(active) == 1


async def test_severity_escalates_as_evidence_accumulates(
    processor: AnomalyProcessor,
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    out = await processor.process(
        signal_factory(event_id="1", offset_seconds=0, signals={"error_rate": 0.0})
    )
    inc = await repo.get_incident(out.incident_id or "")
    assert inc is not None and inc.severity is Severity.LOW

    await processor.process(
        signal_factory(
            event_id="2",
            offset_seconds=10,
            abnormal_signals=["error_rate"],
            signals={"error_rate": 0.35},
        )
    )
    inc = await repo.get_incident(out.incident_id or "")
    assert inc is not None and inc.severity is Severity.CRITICAL


async def test_lifecycle_events_published_when_producer_present(
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    class _FakeProducer:
        def __init__(self) -> None:
            self.ready = True
            self.published: list[str] = []

        async def publish(self, topic: str, envelope: object, *, key: str) -> None:
            self.published.append(getattr(envelope, "event_type", "?"))

    fake = _FakeProducer()
    proc = AnomalyProcessor(repo, lifecycle_producer=fake)  # type: ignore[arg-type]
    await proc.process(signal_factory(event_id="1", offset_seconds=0))
    await proc.process(signal_factory(event_id="2", offset_seconds=10))
    assert fake.published == ["incident.opened", "incident.updated"]
