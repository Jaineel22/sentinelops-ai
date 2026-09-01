"""``order.created`` event, built on the shared platform envelope.

The envelope (schema, versioning strategy, idempotency contract) lives in
``sentinelops_common.events`` and is documented in docs/architecture/events.md.
This module only defines the ``order.created`` payload and its builder.

This is a *business* event — not observability telemetry (ADR-008). The
envelope's ``trace_id`` lets a consumer correlate the event with the request
that produced it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from orders_service import SERVICE_NAME
from orders_service.domain import Order
from sentinelops_common.events import EventEnvelope

__all__ = [
    "EVENT_TYPE_ORDER_CREATED",
    "ORDER_CREATED_VERSION",
    "EventEnvelope",
    "OrderCreatedPayload",
    "build_order_created_event",
]

EVENT_TYPE_ORDER_CREATED = "order.created"
ORDER_CREATED_VERSION = 1


class OrderCreatedPayload(BaseModel):
    """Frozen shape of ``order.created`` v1's payload (documentation + tests)."""

    order_id: str
    customer_id: str
    amount: str
    currency: Literal["INR", "USD", "EUR", "GBP"]


def build_order_created_event(order: Order, *, trace_id: str | None) -> EventEnvelope:
    payload = OrderCreatedPayload(
        order_id=order.order_id,
        customer_id=order.customer_id,
        amount=f"{order.amount:.2f}",
        currency=order.currency,
    )
    return EventEnvelope(
        event_type=EVENT_TYPE_ORDER_CREATED,
        event_version=ORDER_CREATED_VERSION,
        source=SERVICE_NAME,
        trace_id=trace_id,
        payload=payload.model_dump(),
    )
