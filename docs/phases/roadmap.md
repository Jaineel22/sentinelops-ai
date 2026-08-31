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

## Phase 1 — Event backbone + first instrumented service — planned

Kafka via Docker Compose; one service that emits production-like telemetry
events to a topic; a consumer that persists/prints them. Defines topic and
event-schema conventions.

## Phase 2 — ML anomaly detection + offline evaluation — planned

Feature pipeline for live-matching telemetry; a trained detector; a **separate**
benchmark track (HDFS/BGL/NAB). Real metrics: precision, recall, F1, PR-AUC,
false-positive rate, detection latency. See
[ADR-004](../decisions/adr-004-datasets-vs-live-telemetry.md).

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
