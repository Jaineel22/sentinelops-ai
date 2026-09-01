# ADR-018: Kafka partitioning by correlation key

- Status: Accepted
- Date: 2026-09-01

## Context

`anomaly.detected` events feed the incident-correlator, whose correlation logic
(ADR-015) compares each anomaly against the **one active incident** for its
`correlation_key`. If two anomalies for the same service are processed
concurrently on different consumer instances, they race to create that incident.
The partial unique index (ADR-014) makes the race *safe* (one wins, the other
retries and appends), but avoiding the race entirely is cheaper and keeps
per-service processing ordered.

Kafka guarantees message order **only within a partition**, and routes a
message to a partition by hashing its key.

## Decision

Publish `anomaly.detected` **keyed by the correlation service name** (the
`service` field, which is the stable half of the `correlation_key`). All
anomalies for one service therefore land on the same partition and are consumed
in order by a single consumer in the group.

`incident.*` lifecycle events are likewise keyed by `correlation_key`, so a
Phase 4 consumer sees one incident's events in order.

Phase 3 runs **one partition per topic** (single-node dev Kafka, one correlator
instance). The keying choice is what matters: it means the system stays correct
when partitions and consumers are scaled up later, with no code change.

## Explicitly not claimed

- **No global ordering.** Anomalies for *different* services have no ordering
  guarantee relative to each other. The correlation logic does not need one:
  `CREATE`/`APPEND` for different keys are independent, and the window check
  uses event timestamps, not arrival order.
- **No exactly-once.** Delivery is at-least-once; idempotency is handled at the
  database (ADR-016), not by the partitioning.

## Alternatives considered

- **Key by `event_id`.** Spreads load evenly but destroys per-service ordering
  and reintroduces the create race as the common case.
- **Key by full `correlation_key` (`service:environment`).** Equivalent for
  correlation; `service` alone is used because the detector always knows the
  service it is scraping and environments are few. Revisit if environment
  cardinality grows.
- **Single partition forever.** Simplest, but caps consumer throughput at one
  instance. The keying decision avoids baking that limit in.

## Consequences

- Hot-service skew is possible (one very noisy service saturates its partition).
  Acceptable at Phase 3 scale; the fix later is a more specific key, not a
  different strategy.
- Topic creation (dev convenience, `ensure_topics`) uses one partition; a real
  deployment provisions partition counts out of band.
