"""Structured JSON logging + OpenTelemetry wiring, parametrised by service name.

Generalised from ``orders_service.logging_setup`` / ``orders_service.telemetry``
(ADR-007). Same behaviour: one JSON object per log line with ``trace_id`` /
``span_id`` auto-filled from the active span; metrics exposed for Prometheus
scrape; traces generated always, exported only if an OTLP endpoint (or the
console flag) is configured.

Call :func:`configure_observability` once at process start.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME as _RES_SERVICE_NAME
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

_RESERVED = set(
    vars(logging.makeLogRecord({})).keys()
    | {"message", "asctime", "trace_id", "span_id", "taskName"}
)

_configured = False
_service_name = "sentinelops"


class _JsonFormatter(logging.Formatter):
    def __init__(self, *, service: str, env: str) -> None:
        super().__init__()
        self._service = service
        self._env = env

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self._service,
            "environment": self._env,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": getattr(record, "trace_id", None),
            "span_id": getattr(record, "span_id", None),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _TraceContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            record.trace_id = trace.format_trace_id(span_context.trace_id)
            record.span_id = trace.format_span_id(span_context.span_id)
        else:
            record.trace_id = None
            record.span_id = None
        return True


def configure_json_logging(*, service: str, env: str, level: str) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter(service=service, env=env))
    handler.addFilter(_TraceContextFilter())
    root.addHandler(handler)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv = logging.getLogger(name)
        uv.handlers.clear()
        uv.propagate = True


def configure_telemetry(
    *,
    service: str,
    version: str,
    env: str,
    otlp_endpoint: str | None = None,
    console_traces: bool = False,
) -> None:
    global _configured, _service_name
    _service_name = service
    if _configured:
        return

    resource = Resource.create(
        {
            _RES_SERVICE_NAME: service,
            "service.version": version,
            "deployment.environment": env,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{otlp_endpoint}/v1/traces"))
        )
    if console_traces:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    from opentelemetry.exporter.prometheus import PrometheusMetricReader

    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PrometheusMetricReader()])
    )
    _configured = True


def configure_observability(
    *,
    service: str,
    version: str,
    env: str,
    log_level: str = "INFO",
    otlp_endpoint: str | None = None,
    console_traces: bool = False,
) -> None:
    """Telemetry first (so the logging filter can read spans), then logging."""

    configure_telemetry(
        service=service,
        version=version,
        env=env,
        otlp_endpoint=otlp_endpoint,
        console_traces=console_traces,
    )
    configure_json_logging(service=service, env=env, level=log_level)


def shutdown_observability() -> None:
    tp = trace.get_tracer_provider()
    if isinstance(tp, TracerProvider):
        tp.shutdown()
    mp = metrics.get_meter_provider()
    if isinstance(mp, MeterProvider):
        mp.shutdown()


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_service_name)


def get_meter() -> metrics.Meter:
    return metrics.get_meter(_service_name)


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return trace.format_trace_id(ctx.trace_id) if ctx.is_valid else None
