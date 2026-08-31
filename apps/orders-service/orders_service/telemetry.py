"""OpenTelemetry wiring: traces + metrics.

Kept out of business logic on purpose (ADR-007/ADR-008). Nothing here knows what
an "order" is; the app calls :func:`configure_telemetry` once at startup and
uses :func:`get_tracer` / the instruments in ``metrics.py`` elsewhere.

Phase 1 has no collector deployed (Phase 7). So:

* **Metrics** are exposed for Prometheus to scrape at ``/metrics`` via the
  OpenTelemetry Prometheus exporter — always on.
* **Traces** are always *generated* (so ``trace_id`` shows up in logs and Kafka
  headers). They are only *exported* if ``OTEL_EXPORTER_OTLP_ENDPOINT`` is set,
  or to the console if ``OTEL_TRACES_CONSOLE_EXPORT=true``.
"""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.propagate import inject
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.resources import SERVICE_NAME as RES_SERVICE_NAME
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from orders_service import __version__
from orders_service.config import Settings

_INSTRUMENTATION_SCOPE = "orders_service"
_configured = False


def configure_telemetry(settings: Settings) -> None:
    global _configured
    if _configured:
        return

    resource = Resource.create(
        {
            RES_SERVICE_NAME: settings.otel.service_name,
            "service.version": __version__,
            "deployment.environment": settings.app.env,
        }
    )

    tracer_provider = TracerProvider(resource=resource)
    if settings.otel.exporter_otlp_endpoint:
        # Imported lazily: the OTLP exporter pulls in protobuf/requests and is
        # only needed when an endpoint is actually configured.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=f"{settings.otel.exporter_otlp_endpoint}/v1/traces")
            )
        )
    if settings.otel.traces_console_export:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    from opentelemetry.exporter.prometheus import PrometheusMetricReader

    metrics.set_meter_provider(
        MeterProvider(resource=resource, metric_readers=[PrometheusMetricReader()])
    )

    _configured = True


def shutdown_telemetry() -> None:
    tracer_provider = trace.get_tracer_provider()
    if isinstance(tracer_provider, TracerProvider):
        tracer_provider.shutdown()
    meter_provider = metrics.get_meter_provider()
    if isinstance(meter_provider, MeterProvider):
        meter_provider.shutdown()


def get_tracer() -> trace.Tracer:
    return trace.get_tracer(_INSTRUMENTATION_SCOPE, __version__)


def get_meter() -> metrics.Meter:
    return metrics.get_meter(_INSTRUMENTATION_SCOPE, __version__)


def current_trace_id() -> str | None:
    """Lowercase 32-hex id of the active trace, or ``None`` if none is active."""

    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return trace.format_trace_id(span_context.trace_id)


def context_headers() -> list[tuple[str, bytes]]:
    """W3C trace-context as Kafka message headers.

    Correlation is explicit: we inject ``traceparent`` (and ``tracestate`` if
    present) into the outgoing Kafka message so a future consumer can continue
    the same trace. Kafka itself does nothing here — see ADR-008 and
    docs/architecture/events.md.
    """

    carrier: dict[str, str] = {}
    inject(carrier)
    return [(key, value.encode("utf-8")) for key, value in carrier.items()]
