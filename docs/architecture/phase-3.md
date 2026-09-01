# Phase 3 — Incident Correlation + Persistence

Phase 2 produced a detector that scores one telemetry window and says *"this is
anomalous"*. Phase 3 answers the next two questions:

1. **Which anomalies are the same problem?** — deterministic correlation into
   incidents.
2. **Where does incident state live, and how is it queried and audited?** —
   PostgreSQL, an Incident API, and an append-only state history.

No LLM, no root-cause analysis, no remediation — those are Phase 4+. This phase
ends with a clean, queryable incident record and an `incident.*` event stream
for Phase 4 to consume.

Design decisions: [ADR-014](../decisions/adr-014-postgresql-for-incident-state.md)
· [ADR-015](../decisions/adr-015-deterministic-anomaly-correlation.md)
· [ADR-016](../decisions/adr-016-idempotent-kafka-consumer.md)
· [ADR-017](../decisions/adr-017-incident-state-machine.md)
· [ADR-018](../decisions/adr-018-kafka-partitioning-strategy.md).

---

## 1. Components

| Component | Path | Role |
| --- | --- | --- |
| `sentinelops_common` | `libs/sentinelops_common/` | Shared library: the Kafka event envelope, JSON logging + OTel setup, a JSON producer, and an idempotent consumer. Extracted from `orders_service` so Phase 3 services reuse it. |
| `anomaly-detector` | `services/anomaly-detector/` | The Phase 2 → Phase 3 handoff. Polls `orders-service`'s `/metrics`, turns each pair of scrapes into a telemetry window (reusing `ml.data.prepare`), scores it with `ml.inference.DetectorService`, and publishes `anomaly.detected` for anomalous windows. Owns **no** ML logic of its own. |
| `incident-correlator` | `services/incident-correlator/` | The core of the phase. Consumes `anomaly.detected`, correlates deterministically into incidents, persists them in PostgreSQL, serves the Incident API, and emits `incident.*` lifecycle events. |
| `postgres` | compose / CI | Incident state store. Schema owned by Alembic migrations. |

```
orders-service /metrics
        │  (HTTP scrape, every 10s)
        ▼
 anomaly-detector ──(ml.inference.DetectorService.score_window)──► anomaly.detected ─► Kafka
                                                                                        │
                                                                     (keyed by service) │
                                                                                        ▼
                                                                            incident-correlator
                                                                    ┌───────────────┼───────────────┐
                                                                    ▼               ▼               ▼
                                                              correlate       PostgreSQL       incident.* ─► Kafka
                                                             (deterministic)   (source of truth)  (Phase 4 wake-up)
                                                                                    ▲
                                                                            Incident API (:8002)
```

---

## 2. The `anomaly.detected` contract

Defined in `sentinelops_common.contracts.AnomalyDetectedV1`, carried in the
standard event envelope (`event_type = "anomaly.detected"`, `event_version = 1`).

| Field | Type | Notes |
| --- | --- | --- |
| `detector` | str | e.g. `"isolation_forest"` |
| `detector_version` | str | `ml` package version the model trained with |
| `service` | str | the service the telemetry describes |
| `environment` | str | `development` \| `staging` \| `production` |
| `window_start`, `window_end` | RFC 3339 str | the scored telemetry window |
| `anomaly_score`, `threshold` | float | from the detector |
| `is_anomaly` | bool | the detector's verdict |
| `signals` | `{str: float}` | the 11 per-window operational signals (`ml.data.schema.SIGNAL_COLUMNS`) |
| `abnormal_signals` | `[str]` | **coarse deterministic triage** — which signals are outside a fixed normal band. Not the detection decision; a cheap annotation so the correlator can name affected signals. |

Kafka key: `service` (ADR-018). Headers: `event-type`, `event-id`,
`event-version`, plus `traceparent` for trace propagation.

The correlator **rejects** (→ DLQ, no retry) an envelope whose `event_type` or
`event_version` it does not speak, whose payload fails validation, or whose
`is_anomaly` is `false`.

---

## 3. Normalisation: event → `AnomalySignal`

`incident_correlator.events.anomaly_signal_from_envelope` validates the envelope
and payload and produces an immutable `AnomalySignal` — the internal value
object the rest of the pipeline works with. Timestamps are parsed to
UTC-aware `datetime`. `correlation_key` is derived once here:
`f"{service}:{environment}"`.

---

## 4. Correlation (deterministic — ADR-015)

`incident_correlator.correlation.decide(anomaly, active_incident, config)` is a
**pure function** returning one of:

| Verdict | When | Effect |
| --- | --- | --- |
| `CREATE` | no active incident for the key | open a new `OPEN` incident |
| `APPEND` | active incident, anomaly within `CORRELATION_WINDOW_SECONDS` of its last evidence | add evidence, recompute severity, maybe change title/severity |
| `SUPERSEDE` | active incident, but the gap exceeds the window | auto-resolve the stale incident (`resolution = "auto:stale"`), then `CREATE` |

`correlation_key = "<service>:<environment>"`. There is **at most one active
incident per key** — enforced by a partial unique index (§6), so the lookup is a
single O(1) indexed read, never a scan of history.

**`CORRELATION_WINDOW_SECONDS` default = 300.** Telemetry cadence is ~10s, so a
sustained problem emits an anomaly roughly every 10s; 300s = 30 missing windows
before the platform calls the problem over. Long enough to bridge a brief
recovery dip or a scrape gap; short enough that a genuinely new problem 10
minutes later gets its own incident. One env var; retune without code.

**Complexity:** O(1) per anomaly — one indexed lookup + O(1) aggregate update +
three row writes.

---

## 5. Severity (deterministic, rule-based — no LLM)

`incident_correlator.severity.evaluate_severity(inputs, config)` runs a fixed
rule set against the incident's **running aggregates** (max error rate, max p95
latency, anomaly count, distinct abnormal signals, duration). The incident's
severity is the **highest level whose rule fires**; **every** firing rule is
recorded in `severity_reasons` (explainability + Phase 4 input).

| Level | Fires when (defaults, all `SEVERITY_*` configurable) |
| --- | --- |
| `LOW` | ≥ 1 anomaly window |
| `MEDIUM` | ≥ 3 anomaly windows, **or** ≥ 2 distinct signals abnormal |
| `HIGH` | error rate ≥ 10%, **or** p95 ≥ 500 ms, **or** duration ≥ 120 s |
| `CRITICAL` | error rate ≥ 30%, **or** (duration ≥ 300 s **and** ≥ 2 distinct signals) |
| `INFO` | no rule fired (not reachable once an incident exists) |

Severity can rise **or fall** as evidence accumulates (a recovering incident
that stops meeting the HIGH latency bar drops back). Each change writes a
history row with `change = "severity-changed"`.

---

## 6. Persistence (PostgreSQL — ADR-014)

Three tables (full schema: [incident-model.md](incident-model.md)):

- **`incidents`** — one row per incident + running aggregates + lifecycle
  timestamps. Partial unique index
  `uq_incidents_active_key UNIQUE (correlation_key) WHERE status <> 'RESOLVED'`
  is the "one active incident per key" invariant.
- **`incident_evidence`** — one row per contributing anomaly. `UNIQUE (event_id)`
  is the **idempotency key**.
- **`incident_state_history`** — append-only; one row per accepted transition.

The processor never touches SQLAlchemy directly — it goes through the
`IncidentRepository` protocol. Two implementations, proven equivalent by a test:
`InMemoryIncidentRepository` (unit tests) and `SqlIncidentRepository` (SQLite for
fast repo tests, PostgreSQL in integration/production).

### Transactional integrity (ADR-016)

Each anomaly is processed in **one** database transaction via a *unit of work*:

```
dedupe (event_id already in incident_evidence?) 
  → SELECT ... FOR UPDATE the active incident
  → apply CREATE / APPEND / SUPERSEDE: write incident + evidence + history rows
  → COMMIT
```

The Kafka offset is committed **only after** this transaction commits. A crash
mid-processing rolls the transaction back and Kafka replays the message
(at-least-once); the `event_id` dedupe makes the replay a no-op.

Concurrency: two workers racing to create the first incident for a key → one
`INSERT` violates the partial unique index → that worker catches it, re-reads,
and appends. Verified by a PostgreSQL integration test.

---

## 7. State machine (ADR-017)

```
OPEN          → ACKNOWLEDGED | INVESTIGATING | RESOLVED
ACKNOWLEDGED  → INVESTIGATING | RESOLVED
INVESTIGATING → MITIGATING | RESOLVED
MITIGATING    → RESOLVED
RESOLVED      → (terminal)
```

- The correlator only ever creates `OPEN`.
- `ACKNOWLEDGED` / `INVESTIGATING` / `MITIGATING` are operator actions via the API.
- The `system` actor may additionally force any active state → `RESOLVED`
  (auto-resolve on supersede).
- **No reopening.** A new anomaly for a resolved key opens a new incident.
- Every accepted transition → one `incident_state_history` row. A rejected
  transition → `409 Conflict` with the allowed targets listed.

---

## 8. Incident API (`:8002`, internal, no auth in Phase 3)

| Method & path | Purpose |
| --- | --- |
| `GET /health` | liveness |
| `GET /ready` | DB reachable + consumer healthy (503 otherwise) |
| `GET /metrics` | Prometheus exposition |
| `GET /incidents` | list; filters: `status`, `service`, `severity`, `since`, `limit`, `offset` |
| `GET /incidents/{id}` | full detail incl. aggregates, evidence, history |
| `GET /incidents/{id}/evidence` | the contributing anomalies |
| `GET /incidents/{id}/history` | the ordered state-transition audit log |
| `POST /incidents/{id}/acknowledge` | `→ ACKNOWLEDGED` |
| `POST /incidents/{id}/resolve` | `→ RESOLVED` (optional `reason`, `actor`) |
| `POST /incidents/{id}/transition` | explicit `{to, reason, actor}` transition |

Responses are structured JSON (Pydantic models). Codes: `404` unknown incident,
`409` illegal transition, `422` bad filter/body. Internal exceptions are **not**
leaked — FastAPI returns a generic 500; the stack trace goes to the structured
log only.

---

## 9. Observability (reuses ADR-007 conventions)

- **Tracing:** the consumer extracts the `traceparent` header and continues the
  trace, so an anomaly's span in `anomaly-detector` and its processing span in
  `incident-correlator` share a `trace_id` (verified in the compose run).
- **Metrics** (low-cardinality labels only — never `incident_id` / `event_id`):
  `incident.anomalies.processed` (by `outcome`), `incident.anomalies.rejected`,
  `incident.anomalies.duplicate`, `incident.created`, `incident.updated`,
  `incident.resolved` (by `resolution`), `incident.correlation.failures`,
  `incident.processing.duration` (histogram), `incident.active` (up/down).
  Detector: `detector.scrapes`, `detector.windows.scored`,
  `detector.anomalies.published`, `detector.publish.failures`,
  `detector.score.duration`.
- **Logging:** one JSON object per line, `trace_id` / `span_id` auto-injected;
  high-cardinality context (`incident_id`, `event_id`, `correlation_key`) goes
  in `extra=`, never on a metric. No secrets logged.

---

## 10. Running it

```bash
# Everything (Phases 1 + 3):
docker compose up --build

# Just Phase 3:
docker compose up --build kafka postgres orders-service \
    incident-migrate anomaly-detector incident-correlator

# Ports: orders-service :8001 · incident-correlator :8002 · anomaly-detector :8003
```

`incident-migrate` runs `alembic upgrade head` once and exits;
`incident-correlator` waits for it (`service_completed_successfully`).

Local (no Docker) — needs a host Kafka + Postgres:

```bash
make db-migrate           # alembic upgrade head
make run-correlator       # :8002
make run-detector         # :8003
make incident-scenario    # deterministic in-process demo, no infra
```

### Deterministic demo

`scripts/incident_scenario.py` wires the real domain code + API against an
in-memory repository and feeds a fixed anomaly sequence: a healthy window is
ignored; a latency anomaly opens ONE incident; a related error-rate anomaly is
folded into the SAME incident and escalates it to `CRITICAL`; a replayed event
is idempotent; an unrelated service gets its own incident. No Kafka, no DB.

---

## 11. Testing

| Layer | What |
| --- | --- |
| Unit | state machine, severity rules, correlation `decide`, event normalisation, processor scenarios (same service → one incident with N evidence; different service → separate; duplicate → no-op; stale → supersede; severity escalates), both repositories. |
| Repo parity | one test drives the in-memory and SQLite repositories through the same processor and asserts identical outcomes. |
| API | FastAPI `TestClient` over the in-memory repo: every endpoint, every filter, 404/409/422, the acknowledge→resolve history. |
| `-m integration` | PostgreSQL: concurrent first-anomaly race → one incident. Kafka + PostgreSQL end-to-end: publish related + unrelated + duplicate `anomaly.detected` → 2 incidents, correct evidence counts, replay ignored. |
| Detector | triage bands; `anomaly.detected` envelope round-trips into an `AnomalySignal`; `DetectorRunner.tick` scrape→score→publish with fakes. |

All Phase 0/1/2 tests continue to pass unchanged.

---

## 12. Limitations (deliberate, documented)

- **Correlation granularity is the service.** No cross-service / topology-aware
  correlation, no trace-linkage grouping. The correlation key is the single knob.
- **Detector triage bands are coarse and fixed.** The IF model is the detection
  authority; `abnormal_signals` is a hint and can be empty for a
  model-flagged window.
- **`incident.*` events are best-effort** (fire-after-commit). The database is
  authoritative; a lost lifecycle event never corrupts state.
- **No auth on the API** — it is internal in Phase 3 (compose network / laptop).
- **Single partition per topic in dev.** Keying (ADR-018) is what makes scaling
  partitions/consumers a no-code change.
- **DLQ has no UI** — inspect with `kafka-console-consumer`.
- **No performance benchmark is claimed.** The compose run processed tens of
  anomalies into one incident with sub-second per-event latency in the logs;
  that is an observation, not a measured throughput figure.
