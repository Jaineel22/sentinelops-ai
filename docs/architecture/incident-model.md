# Incident data model

The Phase 3 database schema. Owned by Alembic migrations
(`services/incident-correlator/migrations/`); the initial revision is `0001`.
JSON columns are `JSONB` on PostgreSQL, `JSON` on SQLite (unit tests).

See also: [phase-3.md](phase-3.md) ·
[ADR-014](../decisions/adr-014-postgresql-for-incident-state.md) ·
[ADR-017](../decisions/adr-017-incident-state-machine.md).

---

## `incidents`

One row per operational incident. Carries **running aggregates** so severity is
an O(1) recompute per anomaly (no scan of evidence).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `varchar(40)` PK | `inc_<16 hex>` |
| `correlation_key` | `varchar(200)` | `"<service>:<environment>"` |
| `service` | `varchar(128)` | |
| `environment` | `varchar(64)` | |
| `status` | `varchar(20)` | `OPEN` \| `ACKNOWLEDGED` \| `INVESTIGATING` \| `MITIGATING` \| `RESOLVED` (check constraint) |
| `severity` | `varchar(12)` | `INFO` \| `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` (check constraint) |
| `severity_reasons` | json | every firing rule's human-readable reason |
| `title` | text | generated, e.g. `"CRITICAL - error_rate, latency_p95_ms in orders-service (development)"` |
| `anomaly_count` | int | number of evidence rows |
| `abnormal_signal_names` | json | sorted distinct signal names ever flagged; `len()` = "distinct abnormal signals" |
| `max_anomaly_score` | float | running max |
| `max_error_rate` | float | running max |
| `max_latency_p95_ms` | float | running max |
| `detector` | `varchar(64)` | detector of the first anomaly |
| `started_at` | `timestamptz` | earliest evidence window start |
| `last_evidence_at` | `timestamptz` | latest evidence window end — the correlation-window anchor |
| `created_at` / `updated_at` | `timestamptz` | row lifecycle |
| `acknowledged_at` | `timestamptz` null | set on first `→ ACKNOWLEDGED` |
| `resolved_at` | `timestamptz` null | set on `→ RESOLVED` |
| `resolution` | `varchar(64)` null | `"auto:stale"` (superseded) \| `"manual"` \| operator-supplied |

**Indexes**

| Name | Definition | Purpose |
| --- | --- | --- |
| `uq_incidents_active_key` | `UNIQUE (correlation_key) WHERE status <> 'RESOLVED'` | **the core invariant** — at most one active incident per key; backstop for the concurrent-create race |
| `ix_incidents_service_status` | `(service, status)` | API filter, active-incident lookup |
| `ix_incidents_severity` | `(severity)` | API filter |
| `ix_incidents_created_at` | `(created_at)` | API `since` filter / ordering |

---

## `incident_evidence`

One row per contributing `anomaly.detected` event. `event_id` is the
**idempotency key**: a redelivered anomaly hits the unique constraint / dedupe
check and is skipped.

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `bigint` PK | autoincrement |
| `incident_id` | FK → `incidents.id` `ON DELETE CASCADE` | |
| `event_id` | `varchar(64)` | **`UNIQUE (uq_evidence_event_id)`** — the dedupe key |
| `detector` / `detector_version` | str | from the event |
| `anomaly_score` / `threshold` | float | from the detector |
| `window_start` / `window_end` | `timestamptz` | the scored telemetry window |
| `signals` | json | the 11 per-window signals (`ml.data.schema.SIGNAL_COLUMNS`) |
| `abnormal_signals` | json | coarse triage flags carried on the event |
| `trace_id` | `varchar(64)` null | for cross-service trace linking |
| `occurred_at` | `timestamptz` | envelope `occurred_at` |
| `received_at` | `timestamptz` | when the correlator persisted it |
| `correlation_reason` | text | *why* this evidence attached — `"no active incident for this service"`, `"within correlation window (gap 90s <= 300s)"`, … |

**Index:** `ix_evidence_incident (incident_id, occurred_at)`.

---

## `incident_state_history`

Append-only. One row per **accepted** lifecycle transition (ADR-017). Never
updated or deleted (except by the incident's cascade).

| Column | Type | Notes |
| --- | --- | --- |
| `id` | `bigint` PK | autoincrement |
| `incident_id` | FK → `incidents.id` `ON DELETE CASCADE` | |
| `from_status` | `varchar(20)` null | `null` for the opening row |
| `to_status` | `varchar(20)` | |
| `actor` | `varchar(64)` | `"system"` (correlation) \| `"api"` \| operator-supplied |
| `reason` | text | correlation reason, `"acknowledged via API"`, `"auto-resolved: previous incident quiet for 420s…"`, … |
| `severity_at_transition` | `varchar(12)` null | severity when the transition happened |
| `created_at` | `timestamptz` | |

**Index:** `ix_history_incident (incident_id, created_at)`.

A fresh incident always has an opening row `(null → OPEN, actor="system")`.
`GET /incidents/{id}/history` returns these ordered by `created_at`.

---

## Lifecycle at a glance

```
anomaly.detected ─► CREATE  ─► incidents row (OPEN) + evidence row + history(null→OPEN)
                 ─► APPEND  ─► evidence row + aggregates recomputed + [history(severity-changed)]
                 ─► SUPERSEDE ─► old incident: history(→RESOLVED, "auto:stale"), resolved_at set
                                 new incident: as CREATE

operator ─► POST /acknowledge ─► incidents.status=ACKNOWLEDGED, acknowledged_at set, history row
         ─► POST /resolve     ─► incidents.status=RESOLVED, resolved_at set, history row
         ─► POST /transition  ─► validated against the state machine, history row
```

Deleting an incident cascades to its evidence and history. Phase 3 exposes no
delete endpoint; incidents are retained.
