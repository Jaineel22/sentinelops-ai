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

## Phase 5 — Human-approved remediation — planned

Policy validation → human approval → allow-listed action → execution → audit log
→ recovery verification. See
[ADR-003](../decisions/adr-003-human-in-the-loop-remediation.md).

## Phase 6 — MLOps lifecycle — planned

MLflow experiment tracking + registry (model aliases, not stages); model
monitoring; drift detection; retraining workflow.

## Phase 7 — Observability stack — planned

OpenTelemetry instrumentation across services; Prometheus, Loki, Tempo, Grafana
(Alloy/OTel collection, not Promtail).

## Phase 8 — Orchestration, cloud, IaC, hardened CI/CD — planned

Kubernetes manifests/Helm; AWS as target cloud; Terraform modules; CI/CD
extended to build, scan, publish, and deploy.
