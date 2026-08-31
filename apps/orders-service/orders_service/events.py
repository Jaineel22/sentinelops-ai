"""Business event envelope.

Every event SentinelOps' backbone carries uses this envelope. The schema, its
versioning strategy, and the idempotency contract are documented in
docs/architecture/events.md.

This is a *business* event (``order.created``). It is not observability
telemetry — metrics, logs, and traces travel through OpenTelemetry, not Kafka
(ADR-008). The envelope carries a ``trace_id`` purely so a future consumer can
*correlate* an event back to the request that produced it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from orders_service import SERVICE_NAME
from orders_service.domain import Order

EVENT_TYPE_ORDER_CREATED = "order.created"
ORDER_CREATED_VERSION = 1


class EventEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: int
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    source: str = SERVICE_NAME
    # 32-char lowercase hex of the active trace, or None when no trace is active.
    trace_id: str | None = None
    # Business payload. Shape depends on (event_type, event_version).
    payload: dict[str, Any]

    def to_json_bytes(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


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
        trace_id=trace_id,
        payload=payload.model_dump(),
    )
