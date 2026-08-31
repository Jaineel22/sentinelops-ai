# apps/orders-service

A small, production-like **demo application** — not a SentinelOps internal
component. It exists to generate realistic operational activity (HTTP traffic,
business events, OpenTelemetry telemetry) for later phases to observe.

Phase 1 scope. No ML, incident logic, remediation, auth, or database.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/orders` | Create an order → publish `order.created` to Kafka → `201` |
| `GET` | `/orders/{order_id}` | Fetch a recent order (in-memory, bounded) |
| `GET` | `/health` | Liveness — process is up |
| `GET` | `/ready` | Readiness — Kafka producer connected (`503` otherwise) |
| `GET` | `/metrics` | Prometheus exposition (OpenTelemetry metrics) |
| `GET`/`PUT` | `/admin/simulation` | **Dev only** — inspect/adjust failure injection |

## Layout

| File | Responsibility |
| --- | --- |
| `orders_service/app.py` | App factory + lifecycle wiring |
| `orders_service/api.py` | HTTP routes (orchestration only) |
| `orders_service/domain.py` | `Order`, request/response models |
| `orders_service/events.py` | Versioned event envelope |
| `orders_service/kafka_producer.py` | `EventPublisher` protocol + aiokafka / in-memory impls |
| `orders_service/consumer.py` | Demo sink (`python -m orders_service.consumer`) |
| `orders_service/telemetry.py` | OpenTelemetry setup (traces + metrics) |
| `orders_service/metrics.py` | Application metric instruments |
| `orders_service/logging_setup.py` | Structured JSON logging + trace correlation |
| `orders_service/simulation.py` | Failure injection + production guards |
| `orders_service/config.py` | Typed settings (`APP_` / `KAFKA_` / `OTEL_` / `ORDERS_`) |
| `orders_service/store.py` | In-memory order store |

## Run locally

```bash
# needs a broker: docker compose up -d kafka   (host access on localhost:29092)
make run-orders
# -> http://localhost:8001  (docs at /docs)
```

Tests: `tests/orders_service/` (run via `make test`; integration test via
`make test-integration`).

See [docs/architecture/phase-1.md](../../docs/architecture/phase-1.md),
[docs/architecture/events.md](../../docs/architecture/events.md), and
[docs/development/telemetry-scenarios.md](../../docs/development/telemetry-scenarios.md).
