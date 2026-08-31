# ADR-007: OpenTelemetry is the telemetry instrumentation standard

- Status: Accepted
- Date: 2026-08-31

## Context

Every service SentinelOps builds or observes needs metrics, traces, and logs in
a consistent shape. The ML anomaly-detection pipeline (Phase 2) will consume
this telemetry, so the instrumentation choice made now propagates everywhere.
The observability *backends* (Prometheus, Loki, Tempo, Grafana) are Phase 7 —
the instrumentation must not depend on them being present.

## Decision

Use **OpenTelemetry** as the single instrumentation API/SDK across all services.

- **Traces:** OTel SDK + `opentelemetry-instrumentation-fastapi` for automatic
  HTTP server spans, plus explicit spans around business operations
  (`orders.create_order`, `orders.publish_event`). Spans are always *generated*;
  they are *exported* only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set (OTLP/HTTP)
  or `OTEL_TRACES_CONSOLE_EXPORT=true`.
- **Metrics:** the OTel metrics API, exported via the OpenTelemetry Prometheus
  exporter and scraped at `GET /metrics`. Prometheus-native exposition now, no
  collector required; the same instruments can push OTLP later.
- **Logs:** structured JSON on stdout with `trace_id`/`span_id` injected from the
  active span. (Not the OTel logs SDK yet — that is revisited when Loki/an OTel
  Collector land in Phase 7.)

Collection will prefer an **OpenTelemetry Collector / Grafana Alloy** pipeline
over Promtail.

## Alternatives considered

- **`prometheus-client` for metrics + raw `logging` + no tracing.** Less to
  learn now, but no distributed traces and a second metrics model to migrate
  later. Rejected.
- **Vendor agent (Datadog/New Relic).** Lock-in; not aligned with the
  open-source, self-hosted direction. Rejected.
- **Full OTel Collector in Phase 1.** Real value only once there are backends to
  route to. Deferred to Phase 7.

## Consequences

- A consistent telemetry vocabulary from the first service onward.
- Several `opentelemetry-*` packages with a split version scheme (`1.x` SDK,
  `0.x` instrumentation) — pinned in `pyproject.toml`.
- Phase 1 traces are ephemeral unless console/OTLP export is switched on; that
  is acceptable because `trace_id` still flows into logs and Kafka headers for
  correlation.
