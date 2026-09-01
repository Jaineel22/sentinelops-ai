# ADR-017: Incident lifecycle state machine

- Status: Accepted
- Date: 2026-09-01

## Context

An incident has a lifecycle. Phase 3 needs it to be **explicit** (no arbitrary
status changes), **auditable** (every transition recorded with who and why), and
**simple** (an MVP, not an ITIL implementation).

## Decision

Five states and a fixed transition table:

```
OPEN          -> ACKNOWLEDGED | INVESTIGATING | RESOLVED
ACKNOWLEDGED  -> INVESTIGATING | RESOLVED
INVESTIGATING -> MITIGATING | RESOLVED
MITIGATING    -> RESOLVED
RESOLVED      -> (terminal)
```

* `OPEN` is the only state the correlator creates.
* `ACKNOWLEDGED` / `INVESTIGATING` / `MITIGATING` are **operator-driven**, via
  the Incident API (`POST /incidents/{id}/acknowledge`, `.../transition`).
* `RESOLVED` is reachable from any active state. It is set:
  * by an operator (`POST /incidents/{id}/resolve`), or
  * by the system when the correlation window lapses and a new anomaly
    supersedes the incident (`resolution = "auto:stale"`, ADR-015).
* The `system` actor has one extra power: force any active state → `RESOLVED`.
  It cannot make any other transition.

Every accepted transition writes one `incident_state_history` row:
`(from_status, to_status, actor, reason, severity_at_transition, created_at)`.
`validate_transition` rejects anything else with `409 Conflict` and the list of
allowed targets.

### No reopening

`RESOLVED` is terminal. A new anomaly correlated to a resolved incident's key
opens a **new** incident (supersede), it does not reopen the old one. This keeps
each incident a single bounded episode with a clean start/end, which is what
Phase 4's RCA wants to reason about. If product feedback later demands reopen,
it is an additive change (`RESOLVED -> INVESTIGATING` plus a `reopened_at`
column); the state-history table already accommodates it.

## Alternatives considered

- **Free-form status string.** No guardrails, no meaningful audit.
- **A workflow engine.** Massive overkill for five states.
- **Reopen support now.** Adds "how long may an incident stay reopenable?"
  ambiguity for no MVP benefit; deferred deliberately.
- **Separate `CLOSED` vs `RESOLVED`.** No distinct action attaches to it yet.

## Consequences

- `GET /incidents/{id}/history` is a truthful, ordered audit log.
- The active-incident partial unique index keys off `status <> 'RESOLVED'`, so
  the state machine and the "one active incident per key" invariant are the same
  fact expressed twice — kept consistent by `IncidentStatus.is_active`.
- Metrics: `incident.created` / `incident.resolved` counters let a dashboard
  show open-incident count over time without high-cardinality labels.
