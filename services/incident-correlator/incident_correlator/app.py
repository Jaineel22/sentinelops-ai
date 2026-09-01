"""incident-correlator application factory and lifecycle wiring.

Startup order (mirrors orders-service, ADR-007):

1. structured logging + OpenTelemetry (so instruments and trace context exist)
2. metrics instruments
3. database + repository
4. Kafka producer (lifecycle events + DLQ) — best-effort, the app still boots
   if Kafka is down so liveness works; ``/ready`` then reports 503
5. anomaly consumer background task

The database is the source of truth. Schema is owned by Alembic migrations
(``alembic upgrade head``, run as a one-shot before the service in compose);
the app only checks connectivity.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from incident_correlator import __version__
from incident_correlator.api import incidents_router, system_router
from incident_correlator.config import Settings, get_settings
from incident_correlator.consumer import AnomalyConsumer
from incident_correlator.db import Database, SqlIncidentRepository
from incident_correlator.metrics import get_metrics
from incident_correlator.processor import AnomalyProcessor
from incident_correlator.repository import IncidentRepository
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from sentinelops_common.obs import configure_observability, shutdown_observability

__all__ = ["app", "create_app", "main"]

logger = logging.getLogger("incident_correlator.app")


def create_app(
    settings: Settings | None = None,
    *,
    repository: IncidentRepository | None = None,
    run_consumer: bool = True,
) -> FastAPI:
    settings = settings or get_settings()

    configure_observability(
        service=settings.otel.service_name,
        version=__version__,
        env=settings.app.env,
        log_level=settings.app.log_level,
        otlp_endpoint=settings.otel.exporter_otlp_endpoint,
        console_traces=settings.otel.traces_console_export,
    )

    metrics = get_metrics()

    database: Database | None = None
    if repository is None:
        database = Database(
            settings.db.url,
            echo=settings.db.echo,
            pool_size=settings.db.pool_size,
            max_overflow=settings.db.pool_max_overflow,
        )
        repository = SqlIncidentRepository(database)

    producer = KafkaJsonProducer(
        settings.kafka.bootstrap_servers, client_id=settings.kafka.client_id
    )
    processor = AnomalyProcessor(
        repository,
        lifecycle_producer=producer,
        incident_topic=settings.kafka.incident_topic,
        correlation_config=settings.correlation,
        severity_config=settings.severity,
    )
    consumer = (
        AnomalyConsumer(settings, processor, dlq_producer=producer, metrics=metrics)
        if run_consumer
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if consumer is not None:
            try:
                if settings.kafka.auto_create_topics:
                    await ensure_topics(
                        settings.kafka.bootstrap_servers,
                        [
                            settings.kafka.anomaly_topic,
                            settings.kafka.anomaly_dlq_topic,
                            settings.kafka.incident_topic,
                        ],
                        client_id=settings.kafka.client_id,
                    )
                await producer.start()
                await consumer.start()
            except Exception:
                logger.exception("kafka wiring failed to start; /ready will report 503")
        try:
            yield
        finally:
            if consumer is not None:
                await consumer.stop()
            await producer.stop()
            if database is not None:
                await database.dispose()

    app = FastAPI(
        title="incident-correlator",
        version=__version__,
        summary="Correlates anomaly events into incidents with deterministic rules (Phase 3).",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.repository = repository
    app.state.consumer = consumer
    app.state.metrics = metrics

    app.include_router(system_router)
    app.include_router(incidents_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    try:
        uvicorn.run(
            "incident_correlator.app:app",
            host=settings.app.host,
            port=settings.app.port,
            log_config=None,
        )
    finally:
        shutdown_observability()


if __name__ == "__main__":
    main()
