# ADR-016: Idempotent Kafka consumer with at-least-once semantics

- Status: Accepted
- Date: 2026-09-01

## Context

The incident-correlator consumes `anomaly.detected` from Kafka and writes
incidents to PostgreSQL. Kafka gives **at-least-once** delivery: after a crash,
a rebalance, or a redeployment, the consumer will re-see messages whose offset
was not committed. Phase 1's demo consumer could get away with logging a
duplicate; Phase 3 must not create a duplicate incident or double-count
evidence.

## Decision

**Commit the Kafka offset only after the database transaction has committed**,
and make the handler idempotent.

Per message:

1. `parse_envelope` — a malformed payload goes straight to the dead-letter
   topic (`anomaly.events.dlq`), the offset is committed, processing continues.
2. In **one** database transaction:
   * dedupe — if `incident_evidence` already has this `event_id`, do nothing;
   * `SELECT ... FOR UPDATE` the active incident for the correlation key;
   * apply the correlation verdict (ADR-015): insert/update the incident, insert
     the evidence row, insert a state-history row;
   * commit.
3. Only now commit the Kafka offset.

Failure handling in the shared `IdempotentConsumer`:

* `MessageRejected` (understood but unacceptable — e.g. unknown schema version,
  `is_anomaly=false`) → DLQ + commit, no stack trace, no retry.
* `RetryableError` (transient — DB unavailable, downstream timeout) → retry with
  linear backoff up to `KAFKA_MAX_RETRIES`, then DLQ + commit.
* Any other exception → log with stack trace, DLQ + commit (the partition is
  never blocked forever on one poison message).

Concurrency: if two consumers race to create the first incident for a key, the
partial unique index (ADR-014) makes one `INSERT` fail; that worker catches the
integrity error, re-reads, and appends instead.

## Ordering

Kafka orders messages **only within a partition**. The producer keys
`anomaly.detected` by the correlation service name (ADR-018), so all anomalies
for one service land on one partition and are processed in order. Across
services there is **no global order**, and the correlation logic does not assume
one — it is commutative for `CREATE`/`APPEND` and the window comparison uses
event timestamps, not arrival order.

## Alternatives considered

- **Auto-commit / commit-before-write.** Simple, but a crash between commit and
  write silently drops an anomaly.
- **Exactly-once (Kafka transactions / idempotent producer across the DB).**
  Real complexity, cross-system coordination, not warranted at this scale.
  At-least-once + idempotent writes gives the same observable outcome.
- **An inbox/outbox table.** A reasonable future step for the outbound
  `incident.*` events; for the inbound path the `event_id` unique constraint is
  already the inbox.
- **Blocking retries forever on failure.** One bad message halts the partition
  for every service. The DLQ is the pressure-relief valve.

## Consequences

- Outbound `incident.*` lifecycle events are **best-effort, fire-after-commit**:
  the database is the source of truth, the stream is a Phase 4 wake-up. A lost
  lifecycle event never corrupts state.
- The DLQ needs an operator eye (a future dashboard); for now it is inspectable
  with `kafka-console-consumer`.
- Handlers must stay idempotent — enforced by tests that replay the same event.
