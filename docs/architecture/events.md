# Business Events

How domain facts travel through SentinelOps' Kafka backbone. This is the
**business event** plane, kept separate from observability telemetry
([ADR-008](../decisions/adr-008-events-vs-telemetry.md)).

## Envelope

Every event is a JSON object with the same envelope. Implemented in
`apps/orders-service/orders_service/events.py` (`EventEnvelope`).

```json
{
  "event_id": "0f7e8c9a-6b1d-4a2e-9c3f-2b7d1e5a4c88",
  "event_type": "order.created",
  "event_version": 1,
  "occurred_at": "2026-08-31T12:34:56.789012Z",
  "source": "orders-service",
  "trace_id": "3b8e1f0c2d4a5b6c7d8e9f0a1b2c3d4e",
  "payload": {
    "order_id": "ord_4f1ff9ac2011fcfe",
    "customer_id": "customer-123",
    "amount": "1499.00",
    "currency": "INR"
  }
}
```

| Field | Type | Notes |
| --- | --- | --- |
| `event_id` | string (UUID v4) | Unique per event. **Idempotency key** for consumers. |
| `event_type` | string | Namespaced, dot-separated: `<aggregate>.<past-tense>`. |
| `event_version` | integer | Schema version of `(event_type, payload)`. Starts at 1. |
| `occurred_at` | RFC 3339 UTC | When the fact happened, set by the producer. |
| `source` | string | Producing service name. |
| `trace_id` | string \| null | 32-hex id of the originating trace, or null. Correlation only. |
| `payload` | object | Shape determined by `(event_type, event_version)`. |

## Kafka message mapping

| Message part | Value |
| --- | --- |
| Topic | `orders.events` (configurable: `KAFKA_ORDERS_TOPIC`) |
| Key | `order_id` — so all events for one order share a partition and keep order |
| Value | the envelope, UTF-8 JSON |
| Headers | `traceparent` (W3C), `tracestate` (if any), `event-type`, `event-id` |

## Topic: `orders.events`

- **Partitions:** 1 locally (dev). Real deployments size this for throughput and
  consumer parallelism; the `order_id` key preserves per-order ordering
  regardless.
- **Replication factor:** 1 locally (single-node KRaft — no durability). A
  cluster would use 3.
- **Creation:** `orders-service` creates it on startup if missing
  (`KAFKA_AUTO_CREATE_TOPIC=true`). Broker-side auto-create is disabled. A real
  deployment provisions topics via IaC / an ops runbook.
- **Retention:** broker default for now. A deliberate retention policy is a
  later-phase decision (it affects replayability for ML/backfill).

## Producers

| Producer | Events | Trigger |
| --- | --- | --- |
| `orders-service` | `order.created` v1 | `POST /orders` |

Publishing is synchronous and fail-closed in Phase 1
([ADR-010](../decisions/adr-010-phase1-synchronous-publish.md)).

## Consumers

| Consumer | Status | Purpose |
| --- | --- | --- |
| `orders-service` demo consumer | **implemented** | Proves producer → Kafka → consumer; logs receipt with continued trace. Not a real processor. |
| Incident correlation | planned (Phase 3) | Group domain facts / anomalies into incidents. |
| ML feature capture | planned (Phase 2) | *Reads telemetry, not this stream* — but may use event counts as context. |
| Audit | planned (Phase 5) | Durable record. |

## Delivery semantics & idempotency

At-least-once at the API boundary. `acks=all` + an idempotent producer make
broker-side duplicates unlikely, but a publish timeout can still occur *after*
the broker persisted the record, so the API may retry and produce a duplicate.

**Every consumer must be idempotent, keyed on `event_id`.** Kafka does not give
exactly-once *application* semantics and SentinelOps does not claim it.

## Versioning strategy

- `event_version` is bumped for any **breaking** payload change (field removed,
  renamed, type or meaning changed).
- Additive, optional fields do **not** bump the version.
- Producers emit exactly one current version. Consumers switch on
  `(event_type, event_version)` and must tolerate unknown newer versions
  (log + skip, don't crash).
- Multiple versions may coexist on the topic during a migration.

Why version at all: consumers deploy independently of producers. Without an
explicit version, a payload change silently breaks a consumer in production. The
version makes the contract change visible and lets a consumer opt in.
