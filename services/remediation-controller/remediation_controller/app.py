"""remediation-controller application factory and lifecycle wiring (Phase 5C/5D).

Startup order mirrors the other services (ADR-007):

1. structured logging + OpenTelemetry
2. metrics instruments
3. database + remediation repository
4. PolicyEngine (code-defined config) + LocalSimulationExecutor -> RemediationService

The database is the source of truth; the schema is owned by Alembic
(``alembic upgrade head``, run as the ``remediation-migrate`` one-shot in
compose). The app only checks connectivity for ``/ready``.

Phase 5G adds **outbound** Kafka: after each committed transition the service
mirrors the audit facts onto ``remediation.events`` (best-effort, ADR-030). The
service consumes no topic. The only executor is :class:`LocalSimulationExecutor`
— it touches no real infrastructure (no ``subprocess``, Docker, Kubernetes, SSH,
cloud SDK).
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response

from remediation_controller import SERVICE_NAME, __version__
from remediation_controller.api import remediations_router, system_router
from remediation_controller.config import Settings, get_settings
from remediation_controller.db import Database, SqlRemediationRepository
from remediation_controller.domain.enums import ExecutorType
from remediation_controller.executor import Executor, build_executor
from remediation_controller.kafka import RemediationEventPublisher
from remediation_controller.metrics import get_metrics
from remediation_controller.policy import PolicyEngine
from remediation_controller.recovery import RecoveryVerificationConfig
from remediation_controller.repository import RemediationRepository
from remediation_controller.service import RemediationService
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from sentinelops_common.obs import configure_observability, shutdown_observability

__all__ = ["app", "create_app", "main"]

logger = logging.getLogger("remediation_controller.app")


def create_app(
    settings: Settings | None = None,
    *,
    repository: RemediationRepository | None = None,
    executor: Executor | None = None,
    verify_config: RecoveryVerificationConfig | None = None,
    run_publisher: bool = True,
    event_publisher: RemediationEventPublisher | None = None,
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
            repo = SqlRemediationRepository(database)

        active_executor = executor or build_executor(ExecutorType.LOCAL_SIMULATION)
        active_verify_config = verify_config or RecoveryVerificationConfig(
            timeout_seconds=settings.app.verification_timeout_seconds,
            poll_interval_seconds=settings.app.verification_poll_interval_seconds,
        )

        # Phase 5G: outbound lifecycle events. Best-effort — the service still
        # works (audit-trail-only) if Kafka is disabled or the broker is down.
        producer: KafkaJsonProducer | None = None
        publisher = event_publisher
        kafka_active = run_publisher and settings.kafka.enabled and publisher is None
        if kafka_active:
            producer = KafkaJsonProducer(
                settings.kafka.bootstrap_servers, client_id=settings.kafka.client_id
            )
            publisher = RemediationEventPublisher(
                producer,
                topic=settings.kafka.remediation_topic,
                source=SERVICE_NAME,
                metrics=metrics,
            )

        service = RemediationService(
            repository=repo,
            policy_engine=PolicyEngine(),
            executor=active_executor,
            verify_config=active_verify_config,
            event_publisher=publisher,
        )

        app.state.settings = settings
        app.state.repository = repo
        app.state.database = database
        app.state.executor = active_executor
        app.state.service = service
        app.state.metrics = metrics
        app.state.publisher = publisher
        app.state.kafka_enabled = kafka_active

        if kafka_active and producer is not None:
            try:
                if settings.kafka.auto_create_topics:
                    await ensure_topics(
                        settings.kafka.bootstrap_servers,
                        [settings.kafka.remediation_topic],
                        client_id=settings.kafka.client_id,
                    )
                await producer.start()
            except Exception:
                logger.exception(
                    "kafka producer failed to start; lifecycle events will be "
                    "audit-trail-only until the broker is reachable"
                )

        try:
            yield
        finally:
            if producer is not None:
                await producer.stop()
            if database is not None:
                await database.dispose()

    app = FastAPI(
        title=SERVICE_NAME,
        version=__version__,
        summary="Remediation proposals, human approval, and allow-listed simulated execution.",
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
    app.include_router(remediations_router)
    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    try:
        uvicorn.run(
            "remediation_controller.app:app",
            host=settings.app.host,
            port=settings.app.port,
            log_config=None,
        )
    finally:
        shutdown_observability()


if __name__ == "__main__":
    main()
