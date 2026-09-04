# Project Phases

High-level roadmap. Each phase is delivered end to end, tested, and documented
before the next starts. Later phases may re-scope earlier ones. See
[ADR-005](../decisions/adr-005-incremental-delivery.md).

## Phase 0 — Repository & Development Foundation — **done**

Repo structure, README, architecture overview, ADRs, `pyproject.toml`, minimal
FastAPI app with `/health`, pytest, Ruff, mypy, Dockerfile, Compose, CI,
`.env.example`, `.gitignore`.

**Exit criteria:** app starts, `/health` returns `{"status":"ok"}`, tests pass,
lint/type-check/format pass, Docker image builds and runs, no secrets committed.

## Phase 1 — Event backbone + first instrumented service — **done**

Single-node KRaft Kafka via Docker Compose; `orders-service` (demo app) emits
versioned `order.created` events to `orders.events`, instrumented with
OpenTelemetry (traces + Prometheus metrics + structured JSON logs); a demo
consumer proves the path and continues the trace; development-only failure
injection + a traffic generator produce controlled telemetry scenarios.
Details: [../architecture/phase-1.md](../architecture/phase-1.md).

**Exit criteria:** `docker compose up` starts Kafka + services; `POST /orders`
publishes a well-formed event to `orders.events`; unit + integration tests pass;
latency/error injection visibly changes metrics/traces/logs; no Phase 2+
functionality present.

## Phase 2 — ML anomaly detection + offline evaluation — **done**

`ml/` subsystem. Track A: a leak-safe dataset built by scraping `orders-service`
`/metrics` under scripted scenarios; a 23-feature engineering layer shared by
training and inference; chronological + held-out-fault splits; a robust z-score
baseline and an Isolation Forest (primary), with a supervised RF comparator;
window-wise + event-wise evaluation (precision, recall, F1, PR-AUC, FPR,
detection delay). Track B: the same methodology on the public NAB benchmark
(downloaded, not committed). `ml.inference.DetectorService` is the Phase 3
boundary. Details: [../architecture/phase-2.md](../architecture/phase-2.md);
[ADR-011](../decisions/adr-011-ml-dataset-via-metrics-scraping.md),
[ADR-012](../decisions/adr-012-isolation-forest-primary-detector.md),
[ADR-013](../decisions/adr-013-nab-benchmark-track.md).

**Exit criteria:** `make ml-experiments` reproduces all six experiments and
`artifacts/reports/summary.md` from committed data; ML + Phase 0/1 tests pass;
no test-set leakage; the held-out-fault and NAB experiments run; no Phase 3+
functionality present.

## Phase 3 — Incident correlation + persistence — done

`anomaly-detector` wraps the Phase 2 model in a live scrape/score/publish loop
(`orders-service` `/metrics` → `anomaly.detected`). `incident-correlator`
consumes those events and correlates them into **incidents** with
**deterministic, explainable** rules (correlation key `service:environment` +
configurable window — no LLM), assigns severity by a rule engine, and persists
incidents + evidence + append-only state history in **PostgreSQL** (SQLAlchemy +
Alembic). One active incident per key is a partial unique index; the Kafka
consumer is idempotent (at-least-once, DLQ for poison messages). An internal
Incident API serves queries + manual lifecycle transitions; `incident.*` events
are published for Phase 4. Shared plumbing moved to `libs/sentinelops_common/`.
`docs/architecture/phase-3.md`, `docs/architecture/incident-model.md`,
[ADR-014](../decisions/adr-014-postgresql-for-incident-state.md)–[ADR-018](../decisions/adr-018-kafka-partitioning-strategy.md).

**Exit criteria:** `docker compose up` brings up Kafka + Postgres + the two
services + migrations; a telemetry sequence with injected faults produces one
correlated incident queryable via the API; unit + `-m integration` (Kafka +
Postgres) + all Phase 0/1/2 tests pass; no LLM in correlation or severity; no
Phase 4 functionality present.

## Phase 4 — AI RCA agent with controlled tools — done

`services/rca-agent`: a bounded **LangGraph** investigation state machine that
turns a Phase 3 incident into an evidence-grounded, machine-validated
`RCAReport`. Sub-phases: **4A** foundation (domain, state machine, RCA schema,
DB + migration, config) · **4B** the fixed closed registry of read-only evidence
tools (incident/anomaly/timeline/related/metrics/health available; logs, traces,
deployments, dependencies registered-but-unavailable, never fabricated) · **4C**
the engine (`plan → collect → analyze → verify → synthesize → validate`), a
deterministic mock reasoner, the `LlmClient` boundary, prompt-injection
quarantine, `validate_report` gate, and persistence · **4D** the live LLM
provider (`AnthropicLlmClient` behind the same protocol — forced-tool-use
structured output into the existing DTOs, bounded timeout / prompt size /
retries, `LLM_API_KEY` as a `SecretStr`; `RCA_MODE=mock` stays the CI default and
never a silent fallback) · **4E** integration — an idempotent `incident.opened`
Kafka consumer (one investigation per incident), the Investigation HTTP API
(`POST /investigations`, `GET /investigations/{id}[/steps]`,
`GET /incidents/{id}/investigation`) with background execution, Docker Compose
(`rca-migrate` + `rca-agent`, mock mode = no API key), a deterministic full-chain
scenario, and an outcome-class RCA-quality harness · **4G** final Docker / CI /
README / docs pass. The LLM only proposes; deterministic code owns every safety
boundary; there is no executor. `docs/architecture/phase-4.md`,
[ADR-019](../decisions/adr-019-rca-agent-service-and-boundary.md)–[ADR-023](../decisions/adr-023-rca-agent-integration.md).

**Exit criteria:** `docker compose up` brings up Kafka + Postgres + all four
services + both migration one-shots; an injected-fault telemetry sequence
produces an `incident.opened` that the rca-agent turns into a persisted,
evidence-grounded `RCAReport` retrievable via the Investigation API; unit +
`-m integration` (real Kafka + Postgres) + all Phase 0–3 tests pass;
`RCA_MODE=mock` (no API key) is the CI default; the recommended action always
requires human approval and no executor exists (ADR-003); no Phase 5
functionality present.

## Phase 5 — Human-approved remediation — done

AI recommendation → deterministic mapping onto a **closed action catalogue** →
policy validation → human approval → allow-listed execution → audit log →
recovery verification. The core principle is **AI recommendation ≠ execution
authority**: the rca-agent never gains a write path; a separate
`remediation-controller` service turns a recommendation into typed intent a human
must approve. See [ADR-003](../decisions/adr-003-human-in-the-loop-remediation.md),
[ADR-024](../decisions/adr-024-remediation-domain-and-action-catalogue.md),
[../architecture/phase-5.md](../architecture/phase-5.md).

Sub-phases: **5A** remediation domain — closed `RemediationActionType` enum +
immutable `ACTION_CATALOGUE`, structural `RemediationProposal` (no command field,
`extra="forbid"`, `requires_approval: Literal[True]`), `ServiceTarget` allow-list
(fail closed), lifecycle state machine (`EXECUTING` reachable only from
`APPROVED`), `RemediationApproval`, and the deterministic `proposal_from_rca`
mapping (unmapped / unknown / adversarial recommendation → terminal
`BlockedProposal`). **No executor, DB, API or Kafka.** — **done** ·
**5B** deterministic policy validation — a 9-rule, LLM-free `PolicyEngine` over
`RemediationProposal` (state · action · target · environment · severity ·
parameters · risk/blast-radius · expiry · cooldown) returning a structured
`PolicyDecision`; `apply_policy_decision` can only reach `PENDING_APPROVAL` or
`BLOCKED`; cooldown state via an injectable `RemediationHistoryPort` (5C backs it
with PostgreSQL). **No executor, DB, API or Kafka.** — **done** ·
**5C** PostgreSQL persistence + human approval workflow — own Alembic lineage
(`alembic_version_remediation`, `remediations` + immutable `remediation_approvals`
tables), a `RemediationService` (`proposal_from_rca` → `PolicyEngine` → persist
`PENDING_APPROVAL` | `BLOCKED`), a FastAPI approval API (`POST /remediations`,
`GET`, `POST …/approve|reject`), a deterministic role→catalogue-risk
authorization matrix, and concurrency safety (`SELECT … FOR UPDATE` +
`UNIQUE(remediation_id)`). The SQL repo backs the 5B `RemediationHistoryPort`.
**No executor, no Kafka — `APPROVED ≠ EXECUTED`.** — **done** ·
**5D** allow-listed **executor abstraction** + `LocalSimulationExecutor` +
dry-run — `Executor.execute(proposal, …)` receives a typed proposal (never a
command); `POST /remediations/{id}/execute` runs an `APPROVED` remediation
(`APPROVED → EXECUTING → EXECUTED | EXECUTION_FAILED`, all pre-existing 5A
states); `{"dry_run": true}` = same guards + executor interface, zero side
effects; `remediation_executions` table with `UNIQUE(remediation_id)` +
`FOR UPDATE` = one real execution per remediation; closed code-defined executor
registry (no dynamic class loading). **No `subprocess` / Docker / Kubernetes /
SSH / cloud SDK — a local simulation only.** — **done** ·
**5E** append-only audit trail — `remediation_audit_events` table (migration
`0003`, same lineage), one immutable `RemediationAuditEvent` per committed
lifecycle fact (proposal · policy · blocked · approved · rejected · execution
requested·started·succeeded·failed), written **in the same transaction** as the
transition; four-layer append-only enforcement (no write API, no repo mutation
path, app-appends-only, PostgreSQL `BEFORE UPDATE OR DELETE` trigger); a
secret-redaction boundary on every stored value; read-only
`GET /remediations/{id}/audit` (chronological, paginated). **No recovery states,
no Kafka.** — **done** ·
**5F** recovery verification — a deterministic, **observe-only** `RecoveryVerifier`
runs `EXECUTED → VERIFYING → RECOVERED | RECOVERY_FAILED` via a bounded
virtual-clock poll loop over a `HealthProbe` (a deterministic simulation of the
target's post-remediation health), evaluated against the verifier's *own*
thresholds — never the probe's self-report, never an LLM; `remediation_verifications`
table (migration `0004`, `UNIQUE(remediation_id)`), execution-style `FOR UPDATE`
transactions, 3 new audit events written in the same transaction;
`POST /remediations/{id}/verify-recovery` (no body fields); idempotent replay,
concurrency-safe. **The verifier only observes — no command, no infrastructure,
no re-execution, no approval bypass. No Kafka.** — **done** ·
**5G** Kafka lifecycle events + Docker Compose event wiring — `remediation_controller.kafka`
publishes a versioned `RemediationLifecycleV1` event onto `remediation.events`
(closed set of 11 `event_type`s, a 1:1 mirror of the audit trail) after each
committed transition, keyed by `remediation_id`, **best-effort after the DB
commit** (same model as `incident.events`; the audit trail is the durable
record — no transactional outbox); deterministic `event_id` (`uuid5` of the
audit-row id) for consumer dedupe; the service **consumes no topic** — Kafka is
never an execution channel. Docker Compose wires it to Kafka + PostgreSQL. **No
schema change (the 5E audit model already carried the fields).** — **done** ·
**5H** end-to-end integration — `scripts/remediation_e2e_scenario.py` +
`tests/remediation_controller/test_e2e_flow.py` wire the real components
(`incident.opened` → rca-agent `IncidentEventConsumer` → `InvestigationService` →
`RCAReport` → a human, informed by the RCA, POSTs an allow-listed action →
`PolicyEngine` → explicit human approval → `LocalSimulationExecutor` → audit →
recovery verification → `remediation.events`), covering the happy path, both
recovery outcomes, rejection, and duplicate/idempotent requests. There is no
AI → auto-approval → execution path. — **done** ·
**5I** final hardening + docs — security regression tests (shell / kubectl /
docker / URL / credential / prompt-injection cannot cross the event or execution
boundary), an AST test that `remediation_controller.kafka` imports no
infrastructure and has no consumer, idempotency/concurrency coverage across the
full chain, and this documentation pass. — **done**

**5A exit criteria (met):** unknown actions and unknown targets fail closed;
every catalogue action is `requires_approval: Literal[True]`; the state machine
rejects `PROPOSED/REJECTED/EXECUTED → EXECUTING` and `RECOVERED → APPROVED`;
`proposal_from_rca` is deterministic and never turns free-form / adversarial RCA
text into an executable action; no executor / DB / API / Kafka code exists.

**5B exit criteria (met):** the policy engine imports no LLM and reads no
free-text field; risk is derived from the catalogue, never `proposal.risk_level`;
a terminal / past-approval proposal can never re-enter policy; every rule fails
closed; multiple failures are reported deterministically; a policy `ALLOW` never
advances past `PENDING_APPROVAL`; `ruff` + `mypy` + `pytest` green with all Phase
0–5A tests unchanged.

**5C exit criteria (met):** proposals + policy decisions persist in PostgreSQL
(own Alembic lineage); `PENDING_APPROVAL` proposals are retrievable via the API;
a human APPROVE/REJECT works; approver identity is mandatory (empty rejected);
the role→risk matrix is enforced (`403`); expired / policy-blocked / already-
decided / wrong-state approvals are rejected (`409`); duplicate + concurrent
approval yield exactly one immutable decision (PostgreSQL integration test);
`APPROVE` reaches only `APPROVED`, `REJECT` only `REJECTED`, no path to
`EXECUTING`; no executor exists; no command/script field on any model, request,
response, or table; adversarial payloads are `422` or inert; Phase 0–5B tests
unchanged; `ruff` + `ruff format` + `mypy` + `pytest` green.

**5E exit criteria (met):** every committed lifecycle transition (proposal,
policy decision, block, approve, reject, execution request/start/success/failure)
writes an immutable `remediation_audit_events` row **in the same transaction** as
the state change; audit records cannot be modified or deleted through any
application API (no route, no repository method) and PostgreSQL rejects
`UPDATE`/`DELETE` via a trigger; earlier entries are unchanged as later ones are
appended; the trail is retrievable chronologically and paginated via
`GET /remediations/{id}/audit`; a concurrent-approval race yields exactly one
decision event; a dry-run writes none; shell / kubectl / docker / credential /
prompt-injection payloads create no execution path and are redacted, not stored;
migration `0003` applies on PostgreSQL and the append-only trigger fires
(integration test); Phase 0–5D tests unchanged; `ruff` + `ruff format` + `mypy` +
`pytest` green.

**5F exit criteria (met):** `EXECUTED → VERIFYING → RECOVERED | RECOVERY_FAILED`
only — invalid transitions rejected, no `APPROVED`/`EXECUTING → RECOVERED`
shortcut (state-machine asserts + tests); the verifier is deterministic and
LLM-free, runs a bounded poll loop (`max_attempts = timeout // interval + 1`,
virtual clock), and evaluates its *own* thresholds, ignoring the probe's
self-reported status; a healthy service → `RECOVERED` with all checks passed +
audit events; a chronically unhealthy service → `RECOVERY_FAILED` with a redacted
`failure_reason` + audit events; polling scenarios (healthy first / after N polls
/ never / timeout) covered; a repeated verification replays the stored result and
creates no duplicate transition or audit event; a repeat while `VERIFYING`
conflicts; concurrent verifications yield exactly one fresh result (PostgreSQL
integration test); health/telemetry text containing shell / kubectl / docker /
URLs / prompt-injection is inert data — redacted, never parsed, never an
execution path (security regression test); the `verify-recovery` body has no
fields (`extra="forbid"`); no `recovery/` module imports infrastructure
(AST test); migration `0004` applies on PostgreSQL, one Alembic head; no Kafka;
Phase 0–5E tests unchanged; `ruff` + `ruff format` + `mypy` + `pytest` green.

**5G / 5H / 5I exit criteria (met):** every committed lifecycle transition
publishes exactly one versioned `remediation.events` event (11 closed
`event_type`s, `RemediationLifecycleV1` v1) keyed by `remediation_id`, **after**
the transaction commits; a publish failure is counted + logged and never rolls
back the transition or fails the API (the audit trail remains authoritative);
`event_id` is deterministic so a republish is deduplicable; a dry-run and a
replayed verification publish nothing; the payload has no command/URL/credential
field and every value passes the redaction boundary (security regression tests);
`remediation_controller.kafka` imports no infrastructure and contains no consumer
(AST test); the full chain `incident → RCA → proposal → policy → approval →
simulated execution → audit → recovery verification → lifecycle events` runs
deterministically in-process (`test_e2e_flow.py`, `make remediation-e2e-scenario`)
and over real Kafka + PostgreSQL (`-m integration`); duplicate approve / execute
/ verify are rejected or replayed with no unsafe second effect; human approval
remains mandatory and `LocalSimulationExecutor` remains the only executor;
Phase 0–5F tests unchanged; `ruff` + `ruff format` + `mypy` + `pytest` green.

**5D exit criteria (met):** an `APPROVED` remediation reaches `EXECUTING` and
then `EXECUTED` only after a successful simulation; execution goes exclusively
through the `Executor` abstraction; `LocalSimulationExecutor` is the only
implementation and touches no real infrastructure (AST-enforced); the request
body carries only `dry_run` (`extra="forbid"`); unapproved / rejected / blocked /
expired remediations cannot execute (`409`); duplicate + concurrent execution
yield exactly one execution (PostgreSQL integration test); a failed execution
lands in `EXECUTION_FAILED`, never `EXECUTED`; dry-run mutates no state and
persists nothing but still requires approval; no 5E audit trail, 5F recovery
verification, or 5G Kafka wiring; Phase 0–5C tests unchanged; `ruff` +
`ruff format` + `mypy` + `pytest` green.

## Phase 6 — MLOps lifecycle — done (2026-09-03)

Turns the Phase 2 detector into a reproducible, versioned, observable ML
lifecycle around the **same** Isolation Forest + the **same** evaluation
methodology (which stays authoritative). All sub-phases done:
**6A** MLflow experiment tracking (each run's params / real metrics / artifacts /
git-SHA + library lineage; local Compose server) · **6B** model registry +
`candidate`/`champion`/`previous-champion` **aliases** (not stages) promoted only
through a deterministic gate (`evaluate_candidate`, no LLM) · **6C** the
anomaly-detector resolves the `champion` alias at startup with an explicit,
logged local fallback · **6D** per-feature **PSI** drift detection against a
training baseline frozen with the champion; drift ≠ degradation · **6E**
`python -m ml.mlops retrain` reuses the Phase 2 pipeline, logs + registers, and
runs the 6B gate — opt-in promotion, no autonomous deployment / scheduler · **6F**
`scripts/phase6_e2e_demo.py` (`make phase6-demo`), a non-blocking Postgres-backed
CI job, and docs. `docs/architecture/phase-6.md`,
[docs/phase6-summary.md](../phase6-summary.md),
[ADR-031](../decisions/adr-031-mlflow-tracking-and-registry.md),
[ADR-032](../decisions/adr-032-model-alias-strategy.md),
[ADR-033](../decisions/adr-033-model-promotion-criteria.md),
[ADR-034](../decisions/adr-034-drift-detection-methodology.md).

**Exit criteria (met):** MLflow integrated; runs record real params + metrics +
artifacts + dataset/feature/code lineage; models registered with aliases;
deterministic promotion gate that a failed candidate cannot bypass; a baseline
distribution associable with a model version; data/feature drift detection with
tested drift + no-drift scenarios, kept distinct from label-dependent
performance degradation; a controlled retraining workflow that produces a tracked
run and passes evaluation before promotion; existing inference + Phase 0–5 tests
still pass; Ruff + format + mypy green; Compose valid; a reproducible end-to-end
demo; no secrets; no fabricated metrics; no Phase 7/8 functionality.

## Phase 7 — Real-Time ML Inference Observability — done (2026-09-04)

Instruments the **anomaly-detector's inference path** end to end and stands up
**Prometheus + Grafana**. All sub-phases done:
**7A** Prometheus inference metrics — a `DetectorMetrics` inference view
(requests, latency histogram with real buckets, anomalies, score distribution,
live model provenance) recorded per scored window, exported at `GET /metrics`
(OTel → Prometheus, ADR-007) · **7B** detection-latency timeline — a per-cycle
`DetectionTimeline` (scrape → window-close → inference → publish) yielding a
window-age / scrape-to-publish / end-to-end breakdown as histograms **and** on
the `anomaly.detected` payload (debug metadata, never correlation logic) · **7C**
enhanced `/ready` — a thread-safe `DetectorState` rollup (counts, EMA latency,
min/max, uptime) + a soft `healthy` / `health_reasons` degradation signal
(`HEALTH_` thresholds; never changes the HTTP status) + `GET /ready/stats` +
service-level aggregate metrics · **7D** Prometheus (`:9090`, scrapes the
detector every 5 s) + Grafana (`:3000`, auto-provisioned data source + a 12-panel
"Anomaly Detector — Inference & Performance" dashboard) in Docker Compose · **7E**
`scripts/phase7_verify.py` (`make phase7-verify`), static dashboard/config tests,
and this documentation pass. `docs/architecture/phase-7.md`,
[docs/phase7-summary.md](../phase7-summary.md).

**Exit criteria (met):** the inference path exposes a purpose-built Prometheus
metric surface (throughput, latency percentiles from real buckets, anomaly rate,
end-to-end detection latency, model version/type); `/ready` carries an
inference-statistics rollup + `uptime_seconds` + a `healthy` flag without
breaking the existing contract; the detection-latency breakdown rides on the
event payload; a Prometheus scrapes the detector and a provisioned Grafana
dashboard renders every panel from real data (verified with `docker compose up`);
no detection logic changed; no fabricated metrics; existing inference + Phase 0–6
tests still pass (1056, 18 deselected); Ruff + format + mypy green; Compose
config valid. Cross-service OTel rollout and Loki / Tempo / an OTel Collector are
**not** in scope — deferred.

## Phase 8 — Incident Engine: cross-service correlation — done (2026-09-04)

Completes the incident engine. Phase 3 built deterministic anomaly→incident
correlation, the PostgreSQL schema, the severity engine, the lifecycle state
machine, and the Incident API. Phase 8 adds the layer ADR-015 deferred:
**cross-service correlation** via a static service-dependency graph
(`incident_correlator/topology.py`, `SERVICE_DEPENDENCY_GRAPH`). When a service
and one of its declared dependencies both have an active incident within
`CROSS_SERVICE_CORRELATION_WINDOW_SECONDS` (default 600 s), the processor records
a directed `dependent -> dependency` link in the same DB transaction as the
incident write (`incident_relations` table, migration `0002`). Links surface on
`GET /incidents/{id}` as `related_incidents[]` and feed the Phase 4 RCA agent.
Deterministic and explainable — a static graph + a fixed window, no topology
discovery. Details: [../architecture/phase-8.md](../architecture/phase-8.md) ·
[../phase8-summary.md](../phase8-summary.md).

**Exit criteria (met):** a static dependency graph with pure, tested helpers;
cross-service links formed transactionally in `AnomalyProcessor` after
CREATE/APPEND/SUPERSEDE (never DUPLICATE); the `incident_relations` table added
via an Alembic migration with an up/down-tested lineage `0001 -> 0002`;
`IncidentRelationType` in the domain; `GET /incidents/{id}` returns linked
incidents; `link_incidents` / `get_related_incidents` on both repositories;
directional (acyclic), deduped, race-safe linking; same-service Phase 3
correlation and every existing test unchanged; Ruff + format + mypy green;
no fabricated metrics.

## Phase 9 — Orchestration, cloud, IaC, hardened CI/CD — planned

Kubernetes manifests/Helm; AWS as target cloud; Terraform modules; CI/CD
extended to build, scan, publish, and deploy.
