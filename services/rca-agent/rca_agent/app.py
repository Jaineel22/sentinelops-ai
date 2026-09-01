"""rca-agent application factory and lifecycle wiring (Sub-phase 4E).

Startup order mirrors the other services (ADR-007):

1. structured logging + OpenTelemetry
2. metrics instruments
3. database + investigation repository
4. HTTP client -> read-only tool registry (ADR-020) -> LLM client (ADR-021/022)
   -> InvestigationService -> background runner
5. Kafka producer (DLQ) + ``incident.opened`` consumer

The database is the source of truth; the schema is owned by Alembic
(``alembic upgrade head``, run as the ``rca-migrate`` one-shot in compose). The
app only checks connectivity for ``/ready``.

``RCA_MODE=mock`` (default) needs no API key and no Kafka to serve the HTTP API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from rca_agent import SERVICE_NAME, __version__
from rca_agent.api import BackgroundInvestigationRunner, investigations_router, system_router
from rca_agent.config import Settings, get_settings
from rca_agent.db import Database, SqlInvestigationRepository
from rca_agent.engine import InvestigationService
from rca_agent.kafka import IncidentEventConsumer
from rca_agent.llm import build_llm_client
from rca_agent.llm.base import LlmClient
from rca_agent.metrics import get_metrics
from rca_agent.repository import InvestigationRepository
from rca_agent.tools import build_registry
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from sentinelops_common.obs import configure_observability, shutdown_observability

__all__ = ["app", "create_app", "main"]

logger = logging.getLogger("rca_agent.app")


def create_app(
    settings: Settings | None = None,
    *,
    repository: InvestigationRepository | None = None,
    http_client: httpx.AsyncClient | None = None,
    llm_client: LlmClient | None = None,
    run_consumer: bool = True,
    run_investigations_in_background: bool = True,
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

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        database: Database | None = None
        repo = repository
        if repo is None:
            database = Database(
                settings.db.url,
                echo=settings.db.echo,
                pool_size=settings.db.pool_size,
                max_overflow=settings.db.pool_max_overflow,
            )
            repo = SqlInvestigationRepository(database)

        owns_http = http_client is None
        client = http_client or httpx.AsyncClient(timeout=settings.rca.http_timeout_seconds)
        registry = build_registry(settings, http_client=client)
        llm = llm_client or build_llm_client(settings)
        service = InvestigationService(
            repository=repo, registry=registry, llm_client=llm, settings=settings
        )
        runner = BackgroundInvestigationRunner(
            service, metrics=metrics, run_in_background=run_investigations_in_background
        )

        producer = KafkaJsonProducer(
            settings.kafka.bootstrap_servers, client_id=settings.kafka.client_id
        )
        consumer = (
            IncidentEventConsumer(settings, service, dlq_producer=producer, metrics=metrics)
            if run_consumer
            else None
        )

        app.state.settings = settings
        app.state.repository = repo
        app.state.database = database
        app.state.service = service
        app.state.runner = runner
        app.state.consumer = consumer
        app.state.metrics = metrics

        if consumer is not None:
            try:
                if settings.kafka.auto_create_topics:
                    await ensure_topics(
                        settings.kafka.bootstrap_servers,
                        [settings.kafka.incident_topic, settings.kafka.incident_dlq_topic],
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
            await runner.drain(timeout=settings.rca.investigation_timeout_seconds + 5.0)
            await producer.stop()
            if owns_http:
                await client.aclose()
            if database is not None:
                await database.dispose()

    app = FastAPI(
        title=SERVICE_NAME,
        version=__version__,
        summary="Investigates Phase 3 incidents into evidence-grounded RCA reports (Phase 4).",
        lifespan=lifespan,
    )

    @app.middleware("http")
    async def _count_requests(request: Request, call_next: RequestResponseEndpoint) -> Response:
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        metrics.api_requests.add(
            1,
            {
                "route": f"{request.method} {template}",
                "status_class": f"{response.status_code // 100}xx",
            },
        )
        response.headers["x-response-time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response

    app.include_router(system_router)
    app.include_router(investigations_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    try:
        uvicorn.run(
            "rca_agent.app:app",
            host=settings.app.host,
            port=settings.app.port,
            log_config=None,
        )
    finally:
        shutdown_observability()


if __name__ == "__main__":
    main()
