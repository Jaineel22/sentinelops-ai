"""Incident API — querying, filtering, and manual lifecycle transitions.

Runs the real FastAPI app (``create_app``) against an in-memory repository, so
no database or Kafka is needed. The consumer is disabled (``run_consumer=False``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from incident_correlator.app import create_app
from incident_correlator.config import Settings
from incident_correlator.correlation import CorrelationConfig
from incident_correlator.domain import AnomalySignal
from incident_correlator.processor import AnomalyProcessor
from incident_correlator.repository import InMemoryIncidentRepository


@pytest.fixture
def api() -> Iterator[tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor]]:
    repo = InMemoryIncidentRepository()
    processor = AnomalyProcessor(repo, correlation_config=CorrelationConfig(window_seconds=300))
    app = create_app(Settings(), repository=repo, run_consumer=False)
    with TestClient(app) as client:
        yield client, repo, processor


async def _seed(processor: AnomalyProcessor, *signals: AnomalySignal) -> None:
    for sig in signals:
        await processor.process(sig)


def test_health_is_ok(api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor]) -> None:
    client, _, _ = api
    assert client.get("/health").json() == {"status": "ok"}


def test_ready_is_ready(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
) -> None:
    client, _, _ = api
    resp = client.get("/ready")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"


def test_metrics_endpoint_exposes_prometheus_text(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
) -> None:
    client, _, _ = api
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]


async def test_list_and_get_incident(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await _seed(
        processor,
        signal_factory(abnormal_signals=["latency_p95_ms"]),
        signal_factory(offset_seconds=20, abnormal_signals=["latency_p95_ms"]),
    )

    listed = client.get("/incidents").json()
    assert len(listed) == 1
    incident_id = listed[0]["id"]
    assert listed[0]["anomaly_count"] == 2

    detail = client.get(f"/incidents/{incident_id}").json()
    assert detail["id"] == incident_id
    assert len(detail["evidence"]) == 2
    assert len(detail["history"]) == 1
    assert detail["severity_reasons"]


def test_unknown_incident_returns_404(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
) -> None:
    client, _, _ = api
    assert client.get("/incidents/inc_missing").status_code == 404
    assert client.get("/incidents/inc_missing/evidence").status_code == 404
    assert client.get("/incidents/inc_missing/history").status_code == 404


async def test_filters_by_service_and_status(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await _seed(
        processor,
        signal_factory(service="orders-service"),
        signal_factory(service="payments-service"),
    )

    assert len(client.get("/incidents", params={"service": "orders-service"}).json()) == 1
    assert len(client.get("/incidents", params={"service": "unknown"}).json()) == 0
    assert len(client.get("/incidents", params={"status": "OPEN"}).json()) == 2
    assert len(client.get("/incidents", params={"status": "RESOLVED"}).json()) == 0


async def test_invalid_status_filter_is_422(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
) -> None:
    client, _, _ = api
    assert client.get("/incidents", params={"status": "BOGUS"}).status_code == 422


async def test_acknowledge_then_resolve_records_history(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await _seed(processor, signal_factory())
    incident_id = client.get("/incidents").json()[0]["id"]

    acked = client.post(f"/incidents/{incident_id}/acknowledge")
    assert acked.status_code == 200
    assert acked.json()["status"] == "ACKNOWLEDGED"
    assert acked.json()["acknowledged_at"] is not None

    resolved = client.post(
        f"/incidents/{incident_id}/resolve", json={"reason": "handled", "actor": "oncall"}
    )
    assert resolved.status_code == 200
    body = resolved.json()
    assert body["status"] == "RESOLVED"
    assert body["resolution"] == "handled" or body["resolution"] == "manual"

    history = client.get(f"/incidents/{incident_id}/history").json()
    assert [h["to_status"] for h in history] == ["OPEN", "ACKNOWLEDGED", "RESOLVED"]


async def test_resolved_incident_rejects_further_transition_with_409(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await _seed(processor, signal_factory())
    incident_id = client.get("/incidents").json()[0]["id"]

    client.post(f"/incidents/{incident_id}/resolve")
    again = client.post(f"/incidents/{incident_id}/acknowledge")
    assert again.status_code == 409


async def test_transition_endpoint_validates_target(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await _seed(processor, signal_factory())
    incident_id = client.get("/incidents").json()[0]["id"]

    ok = client.post(
        f"/incidents/{incident_id}/transition",
        json={"to": "INVESTIGATING", "reason": "digging in"},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "INVESTIGATING"

    bad = client.post(
        f"/incidents/{incident_id}/transition",
        json={"to": "NOPE", "reason": "x"},
    )
    assert bad.status_code == 422
