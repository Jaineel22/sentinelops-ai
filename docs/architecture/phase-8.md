# Phase 8 — Incident Engine (cross-service correlation)

> Status: **complete.** The incident engine — deterministic anomaly→incident
> correlation, the PostgreSQL schema, the lifecycle state machine, and the
> Incident API — landed in Phase 3. Phase 8 adds the layer the architecture
> blueprint left open: **cross-service correlation** via a static
> service-dependency graph, so incidents in a service and its dependencies are
> linked into one blast-radius view. One-line recap:
> [docs/phase8-summary.md](../phase8-summary.md).
>
> Numbering note: this repo's roadmap previously used "Phase 8" for
> orchestration / cloud / IaC; that work is now **Phase 9**.

## 1. Overview

Phase 3 gave the platform a working incident engine:

- `incident-correlator` consumes `anomaly.detected` and folds related anomalies
  for one `service:environment` into a single **incident**
  ([ADR-015](../decisions/adr-015-deterministic-anomaly-correlation.md)).
- Incidents, evidence, and an append-only state-transition history live in
  **PostgreSQL** ([ADR-014](../decisions/adr-014-postgresql-for-incident-state.md)),
  behind SQLAlchemy 2.0 async + Alembic.
- A deterministic **severity** rule engine (INFO…CRITICAL) and an explicit
  **state machine** ([ADR-017](../decisions/adr-017-incident-state-machine.md))
  drive the incident lifecycle.
- The **Incident API** (`:8002`, internal) serves list / detail / evidence /
  history plus manual acknowledge / resolve / transition.
- `incident.*` lifecycle events are published best-effort after each commit.

What Phase 3 explicitly deferred (see ADR-015 "Finer correlation … dependency
graph … is deferred"): correlation **across** services. An `orders-service`
outage caused by `payments-service` produced two unlinked incidents. Phase 8
closes that gap.

**What Phase 8 delivers:** when a service and one of its declared dependencies
both have an active incident within a time window, the incident engine records a
directed link between them. The link is surfaced on `GET /incidents/{id}` and to
the Phase 4 RCA agent. The mechanism is deterministic and explainable — a
**static dependency graph** plus a **fixed window**, no topology discovery, no
trace-derived edges.

```
anomaly.detected ─► AnomalyProcessor.process()
                      │  (one DB transaction)
                      ├─ dedupe → correlate (CREATE / APPEND / SUPERSEDE)   [Phase 3]
                      ├─ write incident + evidence + state history          [Phase 3]
                      └─ _link_related_incidents()                          [Phase 8]
                           │  static graph: orders-service → {payments, inventory}
                           │  candidates = active incidents in adjacent services
                           │  keep those overlapping within CROSS_SERVICE_… window
                           └─ INSERT incident_relations (dependent → dependency)
```

## 2. Correlation rules (recap — Phase 3, unchanged)

Per `correlation_key = "<service>:<environment>"`, against the single active
incident for that key:

| Situation | Decision |
| --- | --- |
| No active incident | **CREATE** |
| Active incident, anomaly within `CORRELATION_WINDOW_SECONDS` (default 300 s) of its last evidence | **APPEND** |
| Active incident, gap exceeds the window | **SUPERSEDE** (auto-resolve stale, open fresh) |

One active incident per key is enforced by a partial unique index
(`WHERE status <> 'RESOLVED'`). All of this is untouched by Phase 8.

## 3. Cross-service correlation

### 3.1 The dependency graph

`incident_correlator/topology.py` holds a static, explicit graph:

```python
SERVICE_DEPENDENCY_GRAPH = {
    "orders-service": ["payments-service", "inventory-service"],
}
```

Read as "`orders-service` depends on `payments-service` and `inventory-service`"
— it calls them synchronously, so a fault in either surfaces upward as an
orders-service anomaly. Override with the `SERVICE_DEPENDENCY_GRAPH` env var
(JSON); the window is `CROSS_SERVICE_CORRELATION_WINDOW_SECONDS` (default 600 s).

Pure helpers over the graph:

| Function | Returns |
| --- | --- |
| `dependencies_of(service)` | services `service` depends on (downstream) |
| `dependents_of(service)` | services that depend on `service` (upstream callers) |
| `related_services(service)` | union of both — every adjacent node |
| `incidents_overlap(a, b, window)` | do the two `[started_at, last_evidence_at]` spans fall within `window` of each other |
| `find_related_incidents(incident, incidents, window)` | IDs of graph-adjacent, same-environment, concurrent incidents |
| `correlate_incidents(incidents, window)` | all `(dependent, dependency)` links in a set, sorted, deduped |

### 3.2 When links are formed

Inside `AnomalyProcessor._process_once`, in the **same transaction** as the
incident write, `_link_related_incidents(incident, uow)` runs after a CREATE,
APPEND or SUPERSEDE (never after a DUPLICATE):

1. `neighbours = related_services(incident.service)` — skip if empty.
2. `candidates = uow.active_incidents_in_services(neighbours, incident.environment)`
   — one indexed query, bounded by one active incident per service.
3. For each candidate that `incidents_overlap`s the current incident within the
   window: insert an `incident_relations` row **directed dependent → dependency**
   (`orders-service` → `payments-service`), with `relation_type = "dependency"`
   and a human-readable `reason`.

Direction is derived from the graph, so the relation graph is **acyclic**
whenever the dependency graph is. Re-inserting an existing edge is a no-op
(composite primary key + a pre-check); a cross-transaction race raises
`DuplicateActiveIncidentError` and rides the processor's existing retry loop.

Complexity: `O(d)` per anomaly (`d` = adjacent services), no scan of incident
history.

## 4. Database schema

Phase 3 tables (`incidents`, `incident_evidence`, `incident_state_history`) are
unchanged. Phase 8 adds one table via migration `0002_incident_relations`:

```
incident_relations
  incident_id           FK incidents.id  ─┐ composite PK
  related_incident_id   FK incidents.id  ─┘
  relation_type         varchar(32)   -- "dependency" | "cross_service"
  reason                text
  created_at            timestamptz
  CHECK (incident_id <> related_incident_id)      -- ck_incident_relations_no_self
  INDEX ix_incident_relations_incident_id
  INDEX ix_incident_relations_related_incident_id
```

`ON DELETE CASCADE` on both FKs: deleting an incident drops its links. The row is
stored `dependent → dependency`; reads (`get_related_incidents`) union both
directions so either incident sees the other.

Migration lineage: `0001_initial_incident_schema` → `0002_incident_relations`.
`alembic upgrade head` (run as the `incident-migrate` one-shot in Compose) applies
it; `render_as_batch=True` keeps it SQLite-compatible for the fast repo tests.

## 5. Incident lifecycle (recap — Phase 3, unchanged)

`OPEN → ACKNOWLEDGED → INVESTIGATING → MITIGATING → RESOLVED`, no reopen, every
transition audited in `incident_state_history`. `system` may force any active
incident to `RESOLVED` (used by SUPERSEDE / auto-resolve). Cross-service links
do **not** change an incident's status — they are advisory context only.

## 6. Incident API

`GET /incidents/{id}` now includes a `related_incidents` array:

```jsonc
{
  "id": "inc_ab12…",
  "service": "orders-service",
  "severity": "HIGH",
  // …all existing Phase 3 detail fields, unchanged…
  "related_incidents": [
    {
      "id": "inc_cd34…",
      "service": "payments-service",
      "environment": "development",
      "status": "OPEN",
      "severity": "CRITICAL",
      "title": "CRITICAL - error_rate in payments-service (development)",
      "started_at": "2026-09-04T12:00:10Z",
      "last_evidence_at": "2026-09-04T12:03:40Z"
    }
  ]
}
```

Empty list when there are no links. The existing list / evidence / history /
transition endpoints are unchanged. Repository additions:
`link_incidents(incident_id, related_incident_id, relation_type, *, reason="")`
and `get_related_incidents(incident_id)` on both the in-memory and SQL
implementations.

## 7. Testing

| File | What it pins | Count |
| --- | --- | --- |
| `tests/incident_correlator/test_topology.py` | pure graph helpers, `incidents_overlap`, `find_related_incidents`, `correlate_incidents`, window + environment scoping, `ServiceDependency` / `TopologyConfig` defaults | 9 |
| `tests/incident_correlator/test_cross_service_integration.py` | end-to-end through the real `AnomalyProcessor` + Incident API: orders↔payments linked, unrelated services not linked, window constraint, SQLite persistence + idempotent re-link, `GET /incidents/{id}` carries `related_incidents` | 5 |

Both run in the default suite (in-memory repo + `sqlite_repo` + FastAPI
`TestClient` — no broker, no PostgreSQL), the same pattern as
`test_processor.py` / `test_sql_repository.py` / `test_api.py`. The existing
Postgres `-m integration` e2e (`test_integration_e2e.py`) is unchanged.

Full suite after Phase 8: **1070 passed, 18 deselected** (was 1056; +14).
`tests/incident_correlator/` alone: **73 passed**. Ruff + `ruff format --check` +
`mypy --strict` (343 source files) all green.

## 8. Known limitations

- **Static graph only.** The topology is hand-declared in
  `SERVICE_DEPENDENCY_GRAPH`. No discovery from traces, service meshes, or
  deploy manifests — that would make linking non-reproducible, which the
  deterministic-correlation principle (ADR-015) rules out.
- **One default edge.** Only `orders-service → {payments,inventory}` ships,
  because `orders-service` is the only instrumented caller today; `payments` /
  `inventory` are named dependencies, not running services yet.
- **Advisory only.** A link does not merge incidents, change severity, or change
  status. Collapsing a dependency chain into a single parent incident is future
  work.
- **`relation_type` is always `"dependency"`** from the processor. The
  `cross_service` value exists for a future generic (non-graph-edge) linker.
- **No new Kafka event.** Link formation is not published on `incident.events`;
  it is visible via the API and in the DB. A `incident.linked` lifecycle event
  can be added if a consumer needs it.
- **Not in the Postgres CI job.** Cross-service tests use in-memory + SQLite;
  the `-m integration` path is exercised by the existing e2e only.

## 9. Commands

```bash
# unit + integration (default suite — no infra)
python -m pytest tests/incident_correlator/test_topology.py -v
python -m pytest tests/incident_correlator/test_cross_service_integration.py -v
python -m pytest tests/incident_correlator/ -q

# migration
cd services/incident-correlator && alembic upgrade head && cd ../..

# API (against a running stack)
curl -s http://localhost:8002/incidents/<id> | python -m json.tool
#   -> .related_incidents[]
```

Recap: [docs/phase8-summary.md](../phase8-summary.md).
