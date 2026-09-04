"""Cross-service correlation end to end (Phase 8).

Drives the real :class:`AnomalyProcessor` against the in-memory repo and the real
FastAPI app (no DB, no Kafka), the same way ``test_processor`` / ``test_api`` do.
The ``sqlite_repo`` case pins that the SQL mapping and the ``incident_relations``
table behave identically.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from incident_correlator.app import create_app
from incident_correlator.config import Settings
from incident_correlator.correlation import CorrelationConfig
from incident_correlator.db import SqlIncidentRepository
from incident_correlator.domain import AnomalySignal
from incident_correlator.processor import AnomalyProcessor, ProcessResult
from incident_correlator.repository import InMemoryIncidentRepository
from incident_correlator.topology import TopologyConfig


def _processor(repo: object) -> AnomalyProcessor:
    return AnomalyProcessor(
        repo,  # type: ignore[arg-type]
        correlation_config=CorrelationConfig(window_seconds=300),
        topology_config=TopologyConfig(correlation_window_seconds=600),
    )


@pytest.fixture
def api() -> Iterator[tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor]]:
    repo = InMemoryIncidentRepository()
    processor = _processor(repo)
    app = create_app(Settings(), repository=repo, run_consumer=False)
    with TestClient(app) as client:
        yield client, repo, processor


async def test_orders_and_payments_incidents_linked(
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    proc = _processor(repo)
    orders = await proc.process(signal_factory(service="orders-service", offset_seconds=0))
    payments = await proc.process(signal_factory(service="payments-service", offset_seconds=60))
    assert orders.result is ProcessResult.CREATED
    assert payments.result is ProcessResult.CREATED

    related = await repo.get_related_incidents(orders.incident_id or "")
    assert [r.id for r in related] == [payments.incident_id]
    # symmetric: payments -> orders
    back = await repo.get_related_incidents(payments.incident_id or "")
    assert [r.id for r in back] == [orders.incident_id]


async def test_unrelated_services_are_not_linked(
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    proc = _processor(repo)
    a = await proc.process(signal_factory(service="orders-service", offset_seconds=0))
    b = await proc.process(signal_factory(service="shipping-service", offset_seconds=30))
    assert await repo.get_related_incidents(a.incident_id or "") == []
    assert await repo.get_related_incidents(b.incident_id or "") == []


async def test_cross_service_correlation_window(
    repo: InMemoryIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    proc = AnomalyProcessor(
        repo,
        correlation_config=CorrelationConfig(window_seconds=300),
        topology_config=TopologyConfig(correlation_window_seconds=120),
    )
    orders = await proc.process(signal_factory(service="orders-service", offset_seconds=0))
    # payments incident opens well outside the 120s cross-service window
    payments = await proc.process(signal_factory(service="payments-service", offset_seconds=6000))
    assert await repo.get_related_incidents(orders.incident_id or "") == []
    assert await repo.get_related_incidents(payments.incident_id or "") == []


async def test_incident_relations_persisted(
    sqlite_repo: SqlIncidentRepository,
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    proc = _processor(sqlite_repo)
    orders = await proc.process(signal_factory(service="orders-service", offset_seconds=0))
    inventory = await proc.process(signal_factory(service="inventory-service", offset_seconds=45))

    related = await sqlite_repo.get_related_incidents(orders.incident_id or "")
    assert [r.id for r in related] == [inventory.incident_id]

    # idempotent: re-linking the same edge is a no-op
    await sqlite_repo.link_incidents(
        orders.incident_id or "", inventory.incident_id or "", "dependency"
    )
    assert len(await sqlite_repo.get_related_incidents(orders.incident_id or "")) == 1


async def test_get_incident_includes_related(
    api: tuple[TestClient, InMemoryIncidentRepository, AnomalyProcessor],
    signal_factory: Callable[..., AnomalySignal],
) -> None:
    client, _, processor = api
    await processor.process(signal_factory(service="orders-service", offset_seconds=0))
    await processor.process(signal_factory(service="payments-service", offset_seconds=60))

    listed = client.get("/incidents", params={"service": "orders-service"}).json()
    orders_id = listed[0]["id"]

    detail = client.get(f"/incidents/{orders_id}").json()
    assert len(detail["related_incidents"]) == 1
    linked = detail["related_incidents"][0]
    assert linked["service"] == "payments-service"
    assert linked["status"] == "OPEN"

    # an incident with no cross-service link reports an empty list
    await processor.process(signal_factory(service="shipping-service", offset_seconds=0))
    shipping_id = client.get("/incidents", params={"service": "shipping-service"}).json()[0]["id"]
    assert client.get(f"/incidents/{shipping_id}").json()["related_incidents"] == []
