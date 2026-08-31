"""Health and readiness endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orders_service.kafka_producer import InMemoryEventPublisher


def test_health_is_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_is_ok_when_publisher_started(client: TestClient) -> None:
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "kafka": "connected"}


def test_ready_is_503_when_publisher_not_started(
    settings: object, publisher: InMemoryEventPublisher
) -> None:
    from orders_service.app import create_app

    app = create_app(settings, publisher=publisher)  # type: ignore[arg-type]
    # Do not enter the lifespan: the publisher never starts.
    from fastapi.testclient import TestClient as RawClient

    raw = RawClient(app)
    assert raw.get("/health").status_code == 200
    assert raw.get("/ready").status_code == 503
