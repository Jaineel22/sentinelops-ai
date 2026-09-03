"""anomaly-detector application factory.

A thin FastAPI app (``/health``, ``/ready``, ``/metrics``) whose lifespan owns
the scrape/score/publish loop (:class:`~anomaly_detector.runner.DetectorRunner`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from anomaly_detector import __version__
from anomaly_detector.config import Settings, get_settings
from anomaly_detector.metrics import get_metrics
from anomaly_detector.runner import DetectorRunner
from anomaly_detector.training import ensure_detector, get_detector_source
from sentinelops_common.kafka import KafkaJsonProducer, ensure_topics
from sentinelops_common.obs import configure_observability, shutdown_observability

__all__ = ["app", "create_app", "main"]

logger = logging.getLogger("anomaly_detector.app")


def create_app(settings: Settings | None = None) -> FastAPI:
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
        # Phase 6C: `ensure_detector` resolves the MLflow `champion` alias when
        # `settings.detector.mlflow` is populated (MLFLOW_TRACKING_URI set); a
        # registry failure with MLFLOW_REQUIRED=true propagates and the app does
        # not start. Otherwise it loads/trains the local bundle as before.
        detector = ensure_detector(
            settings.detector.model_path,
            seed=settings.detector.seed,
            mlflow_settings=settings.detector.mlflow,
        )
        app.state.detector = detector
        logger.info("detector ready", extra=get_detector_source(detector))
        producer = KafkaJsonProducer(
            settings.kafka.bootstrap_servers, client_id=settings.kafka.client_id
        )
        client = httpx.AsyncClient(timeout=5.0)
        runner = DetectorRunner(
            settings, detector=detector, producer=producer, metrics=metrics, client=client
        )
        app.state.runner = runner
        try:
            if settings.kafka.auto_create_topics:
                await ensure_topics(
                    settings.kafka.bootstrap_servers,
                    [settings.kafka.anomaly_topic],
                    client_id=settings.kafka.client_id,
                )
            await producer.start()
            await runner.start()
        except Exception:
            logger.exception("detector loop failed to start; /ready will report 503")
        try:
            yield
        finally:
            await runner.stop()
            await producer.stop()
            await client.aclose()

    app = FastAPI(
        title="anomaly-detector",
        version=__version__,
        summary="Scores orders-service telemetry and emits anomaly.detected events (Phase 3).",
        lifespan=lifespan,
    )
    app.state.settings = settings

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    def ready(request: Request) -> Response:
        runner = getattr(request.app.state, "runner", None)
        detector = getattr(request.app.state, "detector", None)
        ok = runner is not None and runner.healthy and request.app.state.settings is not None
        body = {
            "status": "ready" if ok else "not-ready",
            "model_loaded": detector is not None,
            "model_source": detector.source if detector is not None else "unknown",
            "model_version": detector.model_version if detector is not None else "unknown",
            "model_type": detector.model_type if detector is not None else "unknown",
        }
        return Response(
            json.dumps(body).encode(),
            status_code=200 if ok else 503,
            media_type="application/json",
        )

    @app.get("/model-info")
    def model_info(request: Request) -> Response:
        detector = getattr(request.app.state, "detector", None)
        if detector is None:
            return Response(
                b'{"model_loaded": false}', status_code=503, media_type="application/json"
            )
        info = {
            "model_loaded": True,
            **get_detector_source(detector),
            "source_details": detector.source_details,
        }
        return Response(json.dumps(info).encode(), media_type="application/json")

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    try:
        uvicorn.run(
            "anomaly_detector.app:app",
            host=settings.app.host,
            port=settings.app.port,
            log_config=None,
        )
    finally:
        shutdown_observability()


if __name__ == "__main__":
    main()
