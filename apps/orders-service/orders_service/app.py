"""orders-service application factory and lifecycle wiring.

Startup order matters:

1. structured logging
2. OpenTelemetry (so metrics instruments and trace context exist)
3. metrics instruments
4. failure injector + order store
5. Kafka producer (best-effort: the app still boots if Kafka is down so that
   liveness works; ``/ready`` and ``POST /orders`` then report 503)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from orders_service import __version__
from orders_service.api import admin_router, orders_router, system_router
from orders_service.config import Settings, get_settings
from orders_service.kafka_producer import AIOKafkaEventPublisher, EventPublisher
from orders_service.logging_setup import configure_logging
from orders_service.metrics import get_order_metrics
from orders_service.simulation import FailureInjector
from orders_service.store import OrderStore
from orders_service.telemetry import configure_telemetry, shutdown_telemetry

__all__ = ["app", "create_app", "main"]

logger = logging.getLogger("orders_service.app")


def create_app(
    settings: Settings | None = None,
    *,
    publisher: EventPublisher | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    settings.validate_for_env()

    configure_logging(env=settings.app.env, level=settings.app.log_level)
    configure_telemetry(settings)

    resolved_publisher: EventPublisher = publisher or AIOKafkaEventPublisher(settings.kafka)

    admin_enabled = settings.simulation.sim_admin_enabled and settings.app.env != "production"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if admin_enabled:
            logger.warning(
                "dev-only /admin/simulation endpoint is mounted (APP_ENV=%s)", settings.app.env
            )
        try:
            await resolved_publisher.start()
        except Exception:
            logger.exception("kafka producer failed to start; /ready will report 503")
        try:
            yield
        finally:
            await resolved_publisher.stop()
            # Telemetry is process-global, not per-app: its lifecycle is owned
            # by ``main()`` (see below), not the ASGI lifespan.

    app = FastAPI(
        title="orders-service",
        version=__version__,
        summary="Demo order API that emits business events and OpenTelemetry telemetry (Phase 1).",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.publisher = resolved_publisher
    app.state.metrics = get_order_metrics()
    app.state.injector = FailureInjector(settings.simulation)
    app.state.store = OrderStore()

    app.include_router(system_router)
    app.include_router(orders_router)
    if admin_enabled:
        app.include_router(admin_router)

    FastAPIInstrumentor.instrument_app(app, exclude_spans=["receive", "send"])
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    try:
        uvicorn.run(
            "orders_service.app:app",
            host=settings.app.host,
            port=settings.app.port,
            log_config=None,  # our JSON logging is already configured
        )
    finally:
        shutdown_telemetry()


if __name__ == "__main__":
    main()
