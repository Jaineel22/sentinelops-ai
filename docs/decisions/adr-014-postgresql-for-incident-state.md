# ADR-014: PostgreSQL for incident state

- Status: Accepted
- Date: 2026-09-01

## Context

Phase 3 introduces the first component that owns **durable, queryable,
mutable** state: incidents, their evidence, and their state-transition history.
Requirements:

* Survive restarts — an incident opened yesterday must still be there.
* Enforce an invariant transactionally: **at most one active incident per
  correlation key**, even with concurrent consumers.
* Idempotent writes keyed by `event_id` (an anomaly event may be redelivered).
* Structured evidence per incident (the anomaly signals, scores, windows) that
  Phase 4 will read.
* Support filtered queries (by status, service, severity, time).

Phases 0–2 had no database (in-memory stores, committed CSV datasets).

## Decision

Use **PostgreSQL** via **SQLAlchemy 2.0 (async)** + **Alembic** migrations.

* Three tables: `incidents`, `incident_evidence`, `incident_state_history`
  (ADR-017 covers the lifecycle they record).
* The "one active incident per key" invariant is a **partial unique index**:
  `UNIQUE (correlation_key) WHERE status <> 'RESOLVED'`. The database rejects a
  concurrent second create; the processor retries and appends.
* Idempotency is a plain `UNIQUE (event_id)` on `incident_evidence` plus a
  dedupe check inside the same transaction.
* Row locking for the correlate-then-write critical section uses
  `SELECT ... FOR UPDATE` on the active incident row.
* JSON columns use `JSONB` on PostgreSQL, degrading to `JSON` on SQLite (unit
  tests run the same repository code against file SQLite; integration tests and
  production run PostgreSQL).

## Alternatives considered

- **Redis** (correlation windows in memory). Rejected as the source of truth:
  no durable history, no relational integrity, no transactional uniqueness.
  Could be added later purely as a cache; not needed at Phase 3 volume.
- **Event sourcing / a dedicated event store.** Overkill for an MVP and
  explicitly out of scope. The state-history table already gives an append-only
  audit trail without the framework.
- **A document store (Mongo).** The data is relational (incident → many
  evidence, many transitions) and the key invariant is a uniqueness constraint —
  exactly what a relational engine does well.
- **SQLite in production.** Fine for tests; no real concurrency story for
  multiple consumer processes.

## Consequences

- New infra: a `postgres` service in compose (dev-only credentials, documented
  as such) and a Postgres service in CI's integration job.
- Schema changes go through Alembic (`make db-migrate`); `docker compose up`
  runs `alembic upgrade head` as a one-shot before the service starts.
- The app depends on the `[incident]` extra (`sqlalchemy`, `asyncpg`,
  `alembic`); other services and the ML pipeline are unaffected.
- Correlation stays O(1) per anomaly: a single indexed lookup of the one active
  incident, never a scan of history (ADR-015).
