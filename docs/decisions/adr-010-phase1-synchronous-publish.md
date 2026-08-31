# ADR-010: Phase 1 order publishing is synchronous and fail-closed

- Status: Accepted
- Date: 2026-08-31

## Context

`POST /orders` creates an order and must emit an `order.created` event. Kafka
can be slow or unavailable. We must choose what the HTTP request does when the
publish cannot be confirmed, without building a large delivery subsystem in
Phase 1.

## Decision

**Publish synchronously inside the request; fail the request if it cannot be
confirmed.**

- The handler `await`s `send_and_wait` with `acks=all` and an idempotent
  producer, bounded by a hard `publish_timeout` (~6 s) so a bad broker cannot
  hang the request.
- On success: `201 Created`.
- On publish failure/timeout: `503 Service Unavailable`, an error log, and
  `orders.publish{outcome="failure"}` + `orders.request.failed{reason=...}`
  metrics. No order is stored — the operation is treated as failed as a whole.
- `GET /ready` reports `503` while the producer is not connected, so an
  orchestrator can keep traffic away from a broker-less instance.

For this platform, an order that downstream systems (correlation, ML, audit)
never hear about is worse than a visibly failed request the caller can retry.

## Alternatives considered

- **Accept the order (`202`) and publish asynchronously / buffer in memory.**
  Better availability, but a crash loses the event silently — the exact failure
  mode this platform exists to catch. Rejected for Phase 1.
- **Transactional outbox** (persist order + event in one DB tx, relay
  separately). The correct long-term design, but needs the database that
  arrives in Phase 3. Explicitly deferred.
- **Retry queue / dead-letter.** Same — needs infrastructure not present yet.

## Consequences

- `orders-service` availability is coupled to Kafka availability by design; this
  is acceptable for a demo/telemetry service and is surfaced honestly via
  `/ready` and metrics.
- Delivery is **at-least-once** at the API boundary (a timeout may occur after
  the broker persisted the record). Every consumer must treat `event_id` as an
  idempotency key — see `docs/architecture/events.md`.
- Revisited in Phase 3 with a transactional outbox once PostgreSQL exists.
