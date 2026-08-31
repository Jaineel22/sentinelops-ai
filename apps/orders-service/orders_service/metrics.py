"""Application-level metric instruments.

HTTP-level metrics (request count, duration, in-flight) come for free from the
OpenTelemetry FastAPI instrumentation. This module adds the *business* signals.

Cardinality rule: labels are bounded, low-cardinality dimensions only. Never
put ``order_id``, ``customer_id``, or ``trace_id`` on a metric — each distinct
value creates a new time series, which explodes Prometheus memory/storage and
slows every query. High-cardinality identifiers belong on spans and log lines,
which are built to hold them.
"""

from __future__ import annotations

from functools import lru_cache

from opentelemetry.metrics import Counter, Histogram

from orders_service.telemetry import get_meter


class OrderMetrics:
    def __init__(self) -> None:
        meter = get_meter()

        self.orders_created: Counter = meter.create_counter(
            "orders.created",
            unit="1",
            description="Orders successfully created.",
        )
        self.order_requests_failed: Counter = meter.create_counter(
            "orders.request.failed",
            unit="1",
            description="Order requests that failed, by reason.",
        )
        self.publish_total: Counter = meter.create_counter(
            "orders.publish",
            unit="1",
            description="Kafka publish attempts for order events, by outcome.",
        )
        self.publish_duration: Histogram = meter.create_histogram(
            "orders.publish.duration",
            unit="s",
            description="Latency of publishing an order event to Kafka.",
        )
        self.failure_injection: Counter = meter.create_counter(
            "orders.failure_injection",
            unit="1",
            description="Times a development failure-injection knob altered behaviour.",
        )


@lru_cache
def get_order_metrics() -> OrderMetrics:
    """Process-wide singleton so instruments are created exactly once."""

    return OrderMetrics()
