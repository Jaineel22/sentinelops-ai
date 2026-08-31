"""HTTP routes for orders-service.

Route handlers orchestrate; they contain no Kafka or OpenTelemetry setup code.
Collaborators are pulled from ``request.app.state`` (wired in ``app.py``).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from opentelemetry.trace import StatusCode
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel

from orders_service.domain import CreateOrderRequest, CreateOrderResponse, Order
from orders_service.events import EventEnvelope, build_order_created_event
from orders_service.kafka_producer import EventPublisher, PublishError
from orders_service.metrics import OrderMetrics
from orders_service.simulation import FailureInjector, SimulationError, SimulationState
from orders_service.store import OrderStore
from orders_service.telemetry import context_headers, current_trace_id, get_tracer

logger = logging.getLogger("orders_service.api")

system_router = APIRouter(tags=["system"])
orders_router = APIRouter(prefix="/orders", tags=["orders"])
admin_router = APIRouter(prefix="/admin", tags=["admin (dev only)"])


class HealthResponse(BaseModel):
    status: str


class ReadyResponse(BaseModel):
    status: str
    kafka: str


@system_router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness: the process is up and serving. No dependency checks."""

    return HealthResponse(status="ok")


@system_router.get("/ready", response_model=ReadyResponse)
def ready(request: Request) -> ReadyResponse:
    """Readiness: can the service do its job right now (Kafka reachable)?"""

    publisher: EventPublisher = request.app.state.publisher
    if not publisher.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "not-ready", "kafka": "unavailable"},
        )
    return ReadyResponse(status="ready", kafka="connected")


@system_router.get("/metrics", include_in_schema=False)
def metrics() -> Response:
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@orders_router.post("", response_model=CreateOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_order(payload: CreateOrderRequest, request: Request) -> CreateOrderResponse:
    injector: FailureInjector = request.app.state.injector
    order_metrics: OrderMetrics = request.app.state.metrics
    publisher: EventPublisher = request.app.state.publisher
    store: OrderStore = request.app.state.store
    tracer = get_tracer()

    with tracer.start_as_current_span("orders.create_order") as span:
        slept_ms = await injector.apply_request_latency()
        if slept_ms:
            order_metrics.failure_injection.add(1, {"kind": "latency"})
            span.set_attribute("orders.injected_latency_ms", slept_ms)

        try:
            injector.maybe_fail_request()
        except SimulationError as exc:
            order_metrics.failure_injection.add(1, {"kind": "error"})
            order_metrics.order_requests_failed.add(1, {"reason": "simulated_error"})
            span.set_attribute("orders.injected_error", True)
            logger.warning(
                "order rejected by failure injection", extra={"outcome": "simulated_error"}
            )
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "simulated failure") from exc

        order = Order.create(payload)
        span.set_attribute("orders.order_id", order.order_id)
        span.set_attribute("orders.currency", order.currency)

        event = build_order_created_event(order, trace_id=current_trace_id())
        await _publish_event(publisher, injector, order_metrics, order=order, event=event)

        store.add(order)
        order_metrics.orders_created.add(1, {"currency": order.currency})
        logger.info(
            "order created",
            extra={
                "outcome": "created",
                "event_id": event.event_id,
                "event_type": event.event_type,
                "order_currency": order.currency,
            },
        )
        return CreateOrderResponse(order_id=order.order_id, status="created")


async def _publish_event(
    publisher: EventPublisher,
    injector: FailureInjector,
    order_metrics: OrderMetrics,
    *,
    order: Order,
    event: EventEnvelope,
) -> None:
    tracer = get_tracer()
    with tracer.start_as_current_span("orders.publish_event") as span:
        span.set_attribute("messaging.system", "kafka")
        span.set_attribute("messaging.destination.name", "orders.events")
        span.set_attribute("messaging.message.id", event.event_id)
        started = time.perf_counter()
        try:
            injector.maybe_fail_publish()
            # Build headers inside this span so the injected `traceparent` points
            # at the publish span the consumer will continue.
            headers = [
                *context_headers(),
                ("event-type", event.event_type.encode("utf-8")),
                ("event-id", event.event_id.encode("utf-8")),
            ]
            await publisher.publish(event, key=order.order_id, headers=headers)
        except (PublishError, SimulationError) as exc:
            elapsed = time.perf_counter() - started
            order_metrics.publish_total.add(1, {"outcome": "failure"})
            order_metrics.publish_duration.record(elapsed, {"outcome": "failure"})
            if isinstance(exc, SimulationError):
                order_metrics.failure_injection.add(1, {"kind": "publish_error"})
            order_metrics.order_requests_failed.add(1, {"reason": "publish_failed"})
            span.set_status(StatusCode.ERROR, "publish failed")
            span.record_exception(exc)
            logger.error("failed to publish order event", extra={"outcome": "publish_failed"})
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "event not published") from exc

        elapsed = time.perf_counter() - started
        order_metrics.publish_total.add(1, {"outcome": "success"})
        order_metrics.publish_duration.record(elapsed, {"outcome": "success"})
        span.set_attribute("orders.publish_duration_seconds", round(elapsed, 6))


@orders_router.get("/{order_id}", response_model=Order)
def get_order(order_id: str, request: Request) -> Order:
    store: OrderStore = request.app.state.store
    order = store.get(order_id)
    if order is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "order not found")
    return order


@admin_router.get("/simulation", response_model=SimulationState)
def get_simulation(request: Request) -> SimulationState:
    injector: FailureInjector = request.app.state.injector
    return injector.state


@admin_router.put("/simulation", response_model=SimulationState)
def put_simulation(new_state: SimulationState, request: Request) -> SimulationState:
    """Adjust failure injection at runtime. Dev only — not mounted in production."""

    injector: FailureInjector = request.app.state.injector
    updated = injector.update(new_state)
    logger.warning(
        "failure-injection state changed",
        extra={
            "outcome": "simulation_updated",
            "latency_ms": updated.latency_ms,
            "error_rate": updated.error_rate,
            "publish_error_rate": updated.publish_error_rate,
        },
    )
    return updated
