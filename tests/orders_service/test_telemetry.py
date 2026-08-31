"""Instrumentation must be active and must not break the application."""

from __future__ import annotations

from fastapi.testclient import TestClient

from orders_service.telemetry import current_trace_id


def test_metrics_endpoint_exposes_prometheus_exposition(client: TestClient) -> None:
    client.post("/orders", json={"customer_id": "c-1", "amount": 5, "currency": "INR"})

    body = client.get("/metrics").text

    assert "orders_created" in body
    assert "orders_publish" in body
    # HTTP-level metrics from the FastAPI instrumentation
    assert "http_server" in body


def test_no_high_cardinality_identifiers_in_metric_labels(client: TestClient) -> None:
    order_id = client.post(
        "/orders", json={"customer_id": "customer-xyz", "amount": 5, "currency": "INR"}
    ).json()["order_id"]

    body = client.get("/metrics").text

    assert order_id not in body
    assert "customer-xyz" not in body


def test_trace_id_is_available_within_a_request(client: TestClient) -> None:
    # The FastAPI instrumentation starts a server span, so a valid trace id
    # exists while handling the request (exercised indirectly via the event).
    from orders_service.kafka_producer import InMemoryEventPublisher

    publisher: InMemoryEventPublisher = client.app.state.publisher  # type: ignore[attr-defined]
    client.post("/orders", json={"customer_id": "c-1", "amount": 5, "currency": "INR"})
    envelope, _key, _headers = publisher.published[0]
    assert envelope.trace_id is not None
    assert len(envelope.trace_id) == 32


def test_current_trace_id_is_none_outside_a_span() -> None:
    assert current_trace_id() is None
