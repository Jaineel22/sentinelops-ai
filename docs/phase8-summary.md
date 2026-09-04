# Phase 8 — Incident Engine: cross-service correlation (summary)

## Overview

The incident engine — deterministic anomaly→incident correlation, the PostgreSQL
schema (incidents / evidence / state history), the INFO…CRITICAL severity rule
engine, the lifecycle state machine, and the internal Incident API — was built in
Phase 3. Phase 8 completes it with the one layer the architecture blueprint left
open: **cross-service correlation**. When a service and one of its declared
dependencies both have an active incident within a time window, the engine
records a directed `dependent → dependency` link between the two incidents, in
the **same database transaction** as the incident write. The link surfaces on
`GET /incidents/{id}` and to the Phase 4 RCA agent, turning two unlinked
incidents (an `orders-service` outage and the `payments-service` fault that
caused it) into one blast-radius view. The mechanism is deterministic and
explainable — a **static service-dependency graph** plus a **fixed window**, no
topology discovery and no trace-derived edges (consistent with ADR-015). No new
ADR; no Phase 3 behaviour changed.

## Key features

- **Static dependency graph (`topology.py`)** — `SERVICE_DEPENDENCY_GRAPH =
  {"orders-service": ["payments-service", "inventory-service"]}`, overridable via
  the `SERVICE_DEPENDENCY_GRAPH` env var (JSON). Pure helpers: `dependencies_of`,
  `dependents_of`, `related_services`, `incidents_overlap`,
  `find_related_incidents`, `correlate_incidents`.
- **Transactional linking in the processor** — `AnomalyProcessor._link_related_incidents`
  runs after every CREATE / APPEND / SUPERSEDE (never a DUPLICATE): one indexed
  query for active incidents in adjacent services, an interval-overlap check
  against `CROSS_SERVICE_CORRELATION_WINDOW_SECONDS` (default 600 s), then an
  `incident_relations` insert per edge. `O(d)` per anomaly; no history scan.
- **`incident_relations` table (migration `0002`)** — composite PK
  `(incident_id, related_incident_id)`, `relation_type`, `reason`, `created_at`,
  a `incident_id <> related_incident_id` check, two directional indexes, and
  `ON DELETE CASCADE` on both FKs. Edge stored `dependent → dependency`; the
  relation graph is acyclic whenever the dependency graph is.
- **Directional, deduped, race-safe** — direction comes from the graph, not
  insertion order; re-linking an existing edge is a no-op; a cross-transaction
  race raises `DuplicateActiveIncidentError` and rides the processor's existing
  retry loop.
- **API + repository** — `GET /incidents/{id}` gains a `related_incidents[]`
  array (id / service / environment / status / severity / title / timestamps).
  `IncidentRepository` gains `link_incidents(...)` and `get_related_incidents(id)`
  on both the in-memory and SQL implementations; `IncidentRelationType` (
  `dependency` | `cross_service`) is added to the domain model.
- **`domain.IncidentRelationType`** and `IncidentRelation` value object keep the
  linking logic framework-free and unit-testable, like the rest of the engine.

## Correlation logic

```
orders-service anomaly ─► incident inc_O (CREATE)                      [Phase 3]
payments-service anomaly ─► incident inc_P (CREATE)                    [Phase 3]
                             └─ _link_related_incidents(inc_P):        [Phase 8]
                                  related_services("payments-service") = {"orders-service"}
                                  candidates = active incidents in orders-service / same env
                                  inc_O overlaps inc_P within 600s?  ── yes
                                  edge: (inc_O → inc_P, "dependency")
```

`incidents_overlap(a, b, w)` is true when
`a.started_at ≤ b.last_evidence_at + w` **and** `b.started_at ≤ a.last_evidence_at + w`
— a strict interval overlap plus a tolerance for staggered onsets. Links are
environment-scoped: a `production` incident never links to a `staging` one.

## Database schema

| Table | Rows | Added |
| --- | --- | --- |
| `incidents` | one per operational incident | Phase 3 |
| `incident_evidence` | anomaly signals that formed / grew it (`event_id` UNIQUE) | Phase 3 |
| `incident_state_history` | append-only lifecycle audit | Phase 3 |
| **`incident_relations`** | **directed `dependent → dependency` links** | **Phase 8** |

Migration lineage: `0001_initial_incident_schema` → `0002_incident_relations`
(SQLite-compatible via `render_as_batch`).

## Real numbers (actual runs)

- **Tests:** full suite **1070 passed, 18 deselected** (was 1056; +14). 14 new
  Phase 8 tests — `tests/incident_correlator/test_topology.py` (9),
  `tests/incident_correlator/test_cross_service_integration.py` (5). All 73
  `tests/incident_correlator/` tests pass.
- **Quality gates:** `ruff check` clean, `ruff format --check` clean, `mypy
  --strict` clean (343 source files).
- **Migration:** `alembic upgrade head` then `downgrade base` verified on SQLite
  — `incident_relations` + both indexes created and dropped cleanly, lineage
  `0001 → 0002`.
- **End-to-end (in-process, real `AnomalyProcessor` + Incident API):** an
  `orders-service` incident and a concurrent `payments-service` incident are
  linked bidirectionally; `GET /incidents/{orders_id}` returns the payments
  incident under `related_incidents`; an unrelated `shipping-service` incident
  reports `related_incidents: []`; a `payments-service` incident 6000 s later is
  not linked.

## Known limitations

- Static, hand-declared graph — no discovery from traces / mesh / manifests (by
  design; discovery would break reproducibility).
- One default edge ships (`orders-service → {payments,inventory}`); `payments` /
  `inventory` are named dependencies, not yet running services.
- Links are advisory: no incident merging, no severity/status change, no
  `incident.linked` Kafka event.
- `relation_type` from the processor is always `"dependency"`; `"cross_service"`
  is reserved for a future generic linker.
- Cross-service tests run in-memory + SQLite; not in the Postgres `-m integration`
  CI job.

## Commands

```bash
python -m pytest tests/incident_correlator/test_topology.py -v
python -m pytest tests/incident_correlator/test_cross_service_integration.py -v
python -m pytest tests/incident_correlator/ -q
cd services/incident-correlator && alembic upgrade head && cd ../..
curl -s http://localhost:8002/incidents/<id> | python -m json.tool   # .related_incidents[]
```

Full write-up: [architecture/phase-8.md](architecture/phase-8.md).
