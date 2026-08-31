# Telemetry Scenarios

Controlled, **development-only** experiments that make `orders-service` produce
specific operational patterns. Later phases (anomaly detection, incident
correlation) use these as evaluation material.

These are **not** real production incidents. They are reproducible experiments
driven by the failure-injection mechanism
([ADR-009](../decisions/adr-009-controlled-failure-injection.md)).

## Prerequisites

```bash
docker compose up --build -d kafka orders-service orders-consumer
# or run orders-service on the host:  make run-orders
```

Drive scenarios with the traffic generator:

```bash
python scripts/generate_traffic.py --scenario <name> --rate 5 --duration 60
python scripts/generate_traffic.py --scenario sequence --duration 45   # A→B→C→D→E
```

It sets the scenario via `PUT /admin/simulation`, sends load, and restores
`normal` at the end. Inspect results at `http://localhost:8001/metrics`, in the
JSON logs (`docker compose logs orders-service orders-consumer`), and — if
`OTEL_TRACES_CONSOLE_EXPORT=true` — spans in the logs.

`--no-admin` sends traffic without changing injection (e.g. to observe steady
state).

---

## Scenario A — Normal

| | |
| --- | --- |
| Injection | none (`latency_ms=0, error_rate=0, publish_error_rate=0`) |
| Traffic | steady, ~5 req/s |
| Expected behaviour | ~all `201`; publish latency low and stable |
| Metrics | `orders_created_total` rises linearly; `orders_publish_total{outcome="success"}` tracks it; `orders_request_failed_total` flat; `http_server_duration` low |
| Traces | `create_order` → `publish_event`, no errors |
| Logs | `order created` info lines; consumer `order event received` lines with matching `trace_id` |

Baseline. Everything else is read relative to this.

## Scenario B — Latency degradation

| | |
| --- | --- |
| Injection | `latency_ms = 400` |
| Traffic | steady, ~5 req/s |
| Expected behaviour | still ~all `201`, but each response ~0.4 s slower |
| Metrics | `http_server_duration` p50/p90 shift up by ~0.4 s; `orders_failure_injection_total{kind="latency"}` increments per request; publish latency unchanged |
| Traces | `create_order` span duration up; `orders.injected_latency_ms=400` attribute; `publish_event` duration normal |
| Logs | normal `order created` lines, just less frequent per connection |

Isolates *request* latency from *publish* latency.

## Scenario C — Error spike

| | |
| --- | --- |
| Injection | `error_rate = 0.25` |
| Traffic | steady, ~5 req/s |
| Expected behaviour | ~25% of requests return `500`; the rest `201` |
| Metrics | `orders_request_failed_total{reason="simulated_error"}` ≈ 25% of attempts; `orders_failure_injection_total{kind="error"}` likewise; `http_server` 5xx ratio ~0.25; `orders_created_total` grows ~25% slower |
| Traces | ~1 in 4 `create_order` spans marked error with `orders.injected_error=true`; no `publish_event` child on those |
| Logs | `order rejected by failure injection` warnings interleaved with successes |

Note: failed requests publish **no** event — the consumer sees ~25% fewer.

## Scenario D — Traffic surge

| | |
| --- | --- |
| Injection | none |
| Traffic | ~4× baseline (generator `rate_multiplier=4`) |
| Expected behaviour | mostly `201`; latency may rise from contention |
| Metrics | `http_server` request rate ~4×; `orders_created_total` slope ~4×; publish latency histogram may widen |
| Traces | span volume ~4×; durations possibly up |
| Logs | ~4× `order created` and consumer lines; consumer lag may briefly grow |

Volume change with no fault — distinguishes "busy" from "broken".

## Scenario E — Recovery

| | |
| --- | --- |
| Injection | reset to none |
| Traffic | back to baseline |
| Expected behaviour | error ratio returns to ~0; latencies return to Scenario A levels |
| Metrics | rates/ratios return to baseline; cumulative counters stay elevated (they don't decrease) |
| Traces | clean span trees again |
| Logs | steady `order created`; a `failure-injection state changed` line marks the reset |

Recovery = return to baseline *rates*, visible against the still-elevated
*totals*.

---

## Optional: publish-failure scenario

`--scenario publish-errors` sets `publish_error_rate = 0.25`: ~25% of requests
return `503`, `orders_publish_total{outcome="failure"}` rises,
`orders_request_failed_total{reason="publish_failed"}` rises, `publish_event`
spans are marked error, and the affected orders are **not** stored. Simulates a
flaky broker without stopping Kafka.
