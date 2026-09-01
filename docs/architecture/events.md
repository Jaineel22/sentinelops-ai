# Business Events

How domain facts travel through SentinelOps' Kafka backbone. This is the
**business event** plane, kept separate from observability telemetry
([ADR-008](../decisions/adr-008-events-vs-telemetry.md)).

## Envelope

Every event is a JSON object with the same envelope. Implemented in
`libs/sentinelops_common/events.py` (`EventEnvelope`); `orders_service.events`
re-exports it. Payload contracts for cross-service events live in
`libs/sentinelops_common/contracts.py`.

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
| Topic | `<aggregate>.events` — `orders.events`, `anomaly.events`, `incident.events` |
| Key | a stable correlation id — `order_id`, `service`, `correlation_key` — so related events share a partition and keep order |
| Value | the envelope, UTF-8 JSON |
| Headers | `traceparent` (W3C), `tracestate` (if any), `event-type`, `event-id`, `event-version` |

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

## Topic: `anomaly.events` (Phase 3)

- **Payload:** `AnomalyDetectedV1` (`event_type = "anomaly.detected"`, v1) —
  detector + version, service, environment, window bounds, score/threshold,
  `is_anomaly`, the 11 operational `signals`, and coarse `abnormal_signals`
  triage flags.
- **Key:** `service` — all anomalies for one service share a partition and stay
  ordered ([ADR-018](../decisions/adr-018-kafka-partitioning-strategy.md)).
- **DLQ:** `anomaly.events.dlq` — malformed payloads, unknown versions,
  non-anomaly events, and retry-exhausted failures land here with a
  `dlq-reason` header ([ADR-016](../decisions/adr-016-idempotent-kafka-consumer.md)).
- **Creation:** `anomaly-detector` / `incident-correlator` create it on startup
  if missing (`KAFKA_AUTO_CREATE_TOPICS=true`).

## Topic: `incident.events` (Phase 3)

- **Payload:** `IncidentLifecycleV1` (`incident.opened` / `incident.updated` /
  `incident.resolved`, v1) — a **best-effort** notification that an incident
  changed. The Incident API / PostgreSQL is authoritative; this stream is a
  wake-up for Phase 4.
- **Key:** `correlation_key` (`service:environment`).
- Published **after** the database transaction commits; a lost lifecycle event
  never corrupts state.

## Producers

| Producer | Events | Trigger |
| --- | --- | --- |
| `orders-service` | `order.created` v1 | `POST /orders` |
| `anomaly-detector` | `anomaly.detected` v1 | telemetry window scored as anomalous |
| `incident-correlator` | `incident.opened` / `incident.updated` / `incident.resolved` v1 | incident created / grew / resolved |

Order publishing is synchronous and fail-closed in Phase 1
([ADR-010](../decisions/adr-010-phase1-synchronous-publish.md)). `incident.*`
publishing is best-effort, after the DB commit (ADR-016).

## Consumers

| Consumer | Status | Purpose |
| --- | --- | --- |
| `orders-service` demo consumer | **implemented** | Proves producer → Kafka → consumer; logs receipt with continued trace. Not a real processor. |
| `incident-correlator` | **implemented** (Phase 3) | Consumes `anomaly.events`; correlates anomalies into incidents. Idempotent, at-least-once, offset committed only after the DB transaction. |
| ML feature capture | not planned as a stream consumer | *Reads telemetry, not this stream.* |
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
