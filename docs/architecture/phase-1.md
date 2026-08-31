# Phase 1 — Event Backbone + First Instrumented Service

## Goal

Stand up a small production-like service that generates realistic operational
activity, emits structured business events through Kafka, and is instrumented
with OpenTelemetry — so later phases have **real telemetry** to observe. This is
not anomaly detection; it is the telemetry-producing foundation.

## What was built

```
                    ┌─────────────────────────────┐
   HTTP client ───▶ │        orders-service       │
   (traffic gen)    │  FastAPI                    │
                    │  order simulation           │
                    │  failure injection (dev)    │
                    │  Kafka producer  ───────────┼──▶  Kafka topic: orders.events
                    │  OpenTelemetry              │           │
                    └───────┬───────────┬─────────┘           │
                            │           │                     ▼
                       /metrics     spans (OTLP/console   ┌──────────────────┐
                     (Prometheus)    if configured)       │ orders-consumer  │
                            │           │                 │ (demo sink,      │
                            ▼           ▼                 │  continues trace)│
                     future Prometheus / Tempo            └──────────────────┘
                          (Phase 7)                              │
                                                                 ▼
                                                    future: ML pipeline (P2),
                                                    incident correlation (P3)
```

### Components

| Component | Path | Role |
| --- | --- | --- |
| `orders-service` | `apps/orders-service/` | Demo order API. `POST /orders`, `GET /orders/{id}`, `GET /health`, `GET /ready`, `GET /metrics`, dev-only `/admin/simulation`. |
| Kafka | `docker-compose.yml` | Single-node KRaft broker ([ADR-006](../decisions/adr-006-kafka-local-deployment-and-client.md)). |
| `orders-consumer` | `apps/orders-service/orders_service/consumer.py` | Demo sink; proves producer → Kafka → consumer and trace continuation. |
| Traffic generator | `scripts/generate_traffic.py` | Drives scenarios (normal / latency / errors / surge / recovery). |

### Order flow (`POST /orders`)

1. `orders.create_order` span opens.
2. Injected latency (if configured) is applied.
3. Injected error (if configured) → `500`.
4. `Order` is built (`ord_` + random id).
5. `order.created` event is built with the current `trace_id`.
6. `orders.publish_event` span: `traceparent` injected into Kafka headers, event
   published synchronously (`acks=all`), bounded by a timeout.
7. Publish failure/timeout → `503`, nothing stored.
8. Success → order stored (in-memory, bounded), metrics + structured log,
   `201 Created`.

## Telemetry

### Metrics (`GET /metrics`, Prometheus exposition)

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `http.server.*` | from FastAPI instrumentation | method, route, status | HTTP request count / duration |
| `orders.created` | counter | `currency` | Orders created |
| `orders.request.failed` | counter | `reason` | Failed order requests |
| `orders.publish` | counter | `outcome` | Kafka publish attempts |
| `orders.publish.duration` | histogram | `outcome` | Publish latency (s) |
| `orders.failure_injection` | counter | `kind` | Injection knob fired |

**Cardinality rule:** no `order_id`, `customer_id`, or `trace_id` on metrics —
each distinct value is a new time series. Those identifiers live on spans and
logs. Test: `tests/orders_service/test_telemetry.py`.

### Traces

Always generated. HTTP server span (auto) → `orders.create_order` →
`orders.publish_event`. Exported only if `OTEL_EXPORTER_OTLP_ENDPOINT` is set
(OTLP/HTTP) or `OTEL_TRACES_CONSOLE_EXPORT=true`. The consumer extracts
`traceparent` from headers and continues the same trace in `orders.consume_event`.

### Logs

Structured JSON on stdout. Every line carries `timestamp`, `level`, `service`,
`environment`, `logger`, `message`, and — when a span is active — `trace_id` and
`span_id`. Order/publish lines also carry `event_id`, `event_type`, `outcome`.
No secrets or credentials are logged.

## Failure behaviour

| Case | API | Logs | Metrics | Trace |
| --- | --- | --- | --- | --- |
| Kafka available | `201` | `order created` info | `orders.created`, `orders.publish{outcome=success}` | full span tree |
| Kafka down at startup | app boots; `/health` `200`, `/ready` `503`, `POST /orders` `503` | `kafka producer failed to start` | `orders.publish{outcome=failure}` on each attempt | publish span = ERROR |
| Kafka drops mid-run | `POST /orders` `503` until reconnect | `failed to publish order event` error | `orders.request.failed{reason=publish_failed}` | publish span = ERROR |
| Invalid request | `422` (FastAPI validation) | access log | `http.server` with 422 | server span only |
| Injected error | `500` | `order rejected by failure injection` warning | `orders.failure_injection{kind=error}`, `orders.request.failed{reason=simulated_error}` | `orders.injected_error=true` |
| Injected latency | `201`, slower | info | `orders.failure_injection{kind=latency}` | `orders.injected_latency_ms` attribute |
| Service restart | in-memory order store is lost; events already in Kafka remain | startup logs | counters reset (process-local) | new traces |
| Traffic surge | `201`s, latency may rise | volume of info lines | `http.server` rate up | more spans |

## Scope boundary

Phase 1 has **no** ML, model training, datasets, MLflow, AI agent, LangGraph,
LLM calls, RCA, incident correlation, remediation, approval workflow,
Kubernetes, AWS, Terraform, auth, or a deployed observability stack. See the
roadmap for when each arrives.
