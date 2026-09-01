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

## Phase 3 — Incident correlation + persistence — planned

Service that correlates anomaly events into incidents; PostgreSQL schema and
migrations; Redis for correlation windows if justified.

## Phase 4 — AI RCA agent with controlled tools — planned

LangGraph (or equivalent) agent reacting to `incident.created`; fixed
allow-listed read-only evidence tools; evidence-backed RCA output. See
[ADR-002](../decisions/adr-002-ml-and-llm-separation.md).

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
