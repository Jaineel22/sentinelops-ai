"""Event envelope contract."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from fastapi.testclient import TestClient

from orders_service.domain import CreateOrderRequest, Order
from orders_service.events import (
    EVENT_TYPE_ORDER_CREATED,
    ORDER_CREATED_VERSION,
    OrderCreatedPayload,
    build_order_created_event,
)
from orders_service.kafka_producer import InMemoryEventPublisher


def _order() -> Order:
    return Order.create(
        CreateOrderRequest(customer_id="c-1", amount=Decimal("99.90"), currency="USD")
    )


def test_envelope_has_required_fields() -> None:
    event = build_order_created_event(_order(), trace_id="0" * 32)

    assert event.event_type == EVENT_TYPE_ORDER_CREATED
    assert event.event_version == ORDER_CREATED_VERSION
    assert event.source == "orders-service"
    assert isinstance(event.occurred_at, datetime)
    assert event.trace_id == "0" * 32
    OrderCreatedPayload.model_validate(event.payload)  # payload matches the frozen shape


def test_event_ids_are_unique() -> None:
    ids = {build_order_created_event(_order(), trace_id=None).event_id for _ in range(1000)}
    assert len(ids) == 1000


def test_trace_id_is_optional() -> None:
    assert build_order_created_event(_order(), trace_id=None).trace_id is None


def test_event_serialises_to_json_bytes() -> None:
    raw = build_order_created_event(_order(), trace_id=None).to_json_bytes()
    assert isinstance(raw, bytes)
    assert b'"event_type":"order.created"' in raw


def test_published_event_carries_trace_and_correlation_headers(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    client.post("/orders", json={"customer_id": "c-1", "amount": 5, "currency": "INR"})

    envelope, key, headers = publisher.published[0]
    header_keys = {k for k, _ in headers}

    assert key == envelope.payload["order_id"]
    assert "traceparent" in header_keys
    assert ("event-id", envelope.event_id.encode()) in headers
    assert ("event-type", b"order.created") in headers
    # trace_id in the payload matches the one in the traceparent header
    traceparent = next(v for k, v in headers if k == "traceparent").decode()
    assert envelope.trace_id is not None
    assert envelope.trace_id in traceparent
