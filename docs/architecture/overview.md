# Architecture Overview

This document describes the **intended** architecture of SentinelOps AI and
tracks what is actually built. It is updated at the end of every phase.

## Status legend

- **IMPLEMENTED** — exists in the repository and is tested.
- **PLANNED** — target design; no code yet.

## Current state (through Phase 1)

**IMPLEMENTED**

- **Phase 0:** platform API skeleton (`apps/api/sentinelops_api`) with
  `GET /health` and `GET /`; typed env-var config; test/lint/type-check/Docker/CI
  scaffolding.
- **Phase 1:**
  - `orders-service` (`apps/orders-service`) — a demo app under observation:
    `POST /orders`, `GET /orders/{id}`, `/health`, `/ready`, `/metrics`,
    dev-only `/admin/simulation`.
  - Kafka event backbone: single-node KRaft broker in Docker Compose; topic
    `orders.events`; versioned `order.created` event envelope
    ([events.md](events.md)).
  - OpenTelemetry instrumentation of `orders-service`: HTTP + business spans,
    Prometheus-scraped metrics, structured JSON logs with trace correlation.
  - A demo consumer proving producer → Kafka → consumer and trace continuation.
  - Development-only failure injection + a traffic generator for reproducible
    telemetry scenarios ([telemetry-scenarios.md](../development/telemetry-scenarios.md)).

See [phase-1.md](phase-1.md). Everything below not listed above is **PLANNED**.

## Target architecture

### 1. Instrumented services — PARTIALLY IMPLEMENTED (Phase 1)

Small services emit telemetry (metrics, logs, traces) via **OpenTelemetry**.
`orders-service` is the first — a demo app under observation, with built-in
fault injection to generate production-like scenarios. More services, and an
OpenTelemetry Collector / **Grafana Alloy** collection pipeline (not Promtail),
are planned for Phase 7.

### 2. Event backbone — PARTIALLY IMPLEMENTED (Phase 1)

**Apache Kafka** is the backbone. A single-node KRaft broker carries
`orders.events` (Phase 1) and, since Phase 3, `anomaly.events` and
`incident.events` (plus `anomaly.events.dlq`). Agent findings, approval
decisions, and remediation/verification outcomes are planned for their
respective phases. Services are decoupled and independently deployable.
Rationale:
[ADR-001](../decisions/adr-001-event-driven-architecture.md),
[ADR-006](../decisions/adr-006-kafka-local-deployment-and-client.md),
[ADR-008](../decisions/adr-008-events-vs-telemetry.md).

### 3. ML anomaly detection — PARTIALLY IMPLEMENTED (Phase 2)

The **offline pipeline** exists in `ml/`: a leak-safe dataset built from
`orders-service` telemetry, a 23-feature engineering layer shared by training
and inference, a robust z-score baseline and an **Isolation Forest** (primary,
scikit-learn — [ADR-012](../decisions/adr-012-isolation-forest-primary-detector.md)),
chronological + held-out-fault evaluation with real metrics (precision, recall,
F1, PR-AUC, FPR, detection delay — never fabricated), and a **separate** NAB
benchmark track ([ADR-004](../decisions/adr-004-datasets-vs-live-telemetry.md),
[ADR-013](../decisions/adr-013-nab-benchmark-track.md)). Reports live in
`artifacts/reports/`.

`ml.inference.DetectorService` gives a clean call: `score_window(signals)
→ AnomalyResult`. Phase 3's `anomaly-detector` service wraps it in a live
scrape/score/publish loop. XGBoost and PyTorch remain deferred. See
[phase-2.md](phase-2.md).

### 4. Incident correlation — IMPLEMENTED (Phase 3)

The `incident-correlator` service consumes `anomaly.detected` and groups related
anomalies for a service into a single **incident** using **deterministic,
explainable** rules — a correlation key (`service:environment`) plus a
configurable time window ([ADR-015](../decisions/adr-015-deterministic-anomaly-correlation.md)).
No LLM. Severity is a deterministic rule engine (INFO…CRITICAL), every firing
rule recorded. Incidents, their evidence, and an append-only state-transition
history are persisted in **PostgreSQL** (SQLAlchemy + Alembic —
[ADR-014](../decisions/adr-014-postgresql-for-incident-state.md)); a partial
unique index enforces one active incident per key. The Kafka consumer is
idempotent with at-least-once semantics
([ADR-016](../decisions/adr-016-idempotent-kafka-consumer.md)). An internal
Incident API (`:8002`) serves queries and manual lifecycle transitions, and
`incident.*` lifecycle events are published for Phase 4. **Redis** was
considered and not needed (ADR-014). See [phase-3.md](phase-3.md).

### 5. AI RCA agent — PLANNED

An explicit state-machine agent (**LangGraph** or equivalent) reacts to
`incident.created`. It investigates by calling a fixed, **allow-listed** set of
read-only evidence tools: metrics query, log query, trace lookup, service
dependency lookup, recent deployments, and historical-incident search. It
produces an **evidence-backed** root-cause analysis and a remediation proposal.
The agent has no unrestricted infrastructure access:
[ADR-002](../decisions/adr-002-ml-and-llm-separation.md).

### 6. Human-approved remediation — PLANNED

```
AI recommendation → policy validation → human approval
  → allow-listed action → execution → audit log → recovery verification
```

No change to a running system happens without a human decision, and only
pre-defined allow-listed actions can ever be executed:
[ADR-003](../decisions/adr-003-human-in-the-loop-remediation.md). Every step is
recorded for auditability.

### 7. Recovery verification — PLANNED

After an action executes, the system re-checks the signals that defined the
incident and records whether recovery occurred.

### 8. MLOps lifecycle — PLANNED

**MLflow** for experiment tracking and model registry, using **model aliases**
(not deprecated stage transitions). Model monitoring and drift detection feed a
retraining workflow. Training/evaluation is reproducible.

### 9. Observability stack — PLANNED

**Prometheus** (metrics), **Loki** (logs), **Tempo** (traces), **Grafana**
(dashboards), all fed through OpenTelemetry.

### 10. Packaging & delivery — PLANNED

- **Docker** / **Docker Compose** for local multi-service development.
- **Kubernetes** for orchestration.
- **AWS** as the target cloud.
- **Terraform** for infrastructure as code.
- **GitHub Actions** for CI/CD (lint, type-check, test now; build/publish/deploy
  later).

## Component → phase map

| Component | Phase |
| --- | --- |
| Repo & dev foundation | 0 (done) |
| Kafka + first instrumented service | 1 (done) |
| ML anomaly detection + offline evaluation | 2 (done, offline) |
| Incident correlation + PostgreSQL | 3 |
| AI RCA agent + evidence tools | 4 |
| Approval + remediation + verification + audit | 5 |
| MLflow + monitoring + drift + retraining | 6 |
| Observability stack | 7 |
| Kubernetes + AWS + Terraform + hardened CI/CD | 8 |

## Repository layout rationale

| Path | Purpose |
| --- | --- |
| `apps/api/` | The SentinelOps platform API (Phase 0). |
| `apps/orders-service/` | Demo app under observation (Phase 1). |
| `services/` | SentinelOps-internal event processors — `anomaly-detector` (live scoring) and `incident-correlator` (correlation + persistence + Incident API), Phase 3. |
| `libs/sentinelops_common/` | Shared library: Kafka event envelope, JSON logging + OpenTelemetry setup, JSON producer, idempotent consumer. |
| `ml/` | ML anomaly-detection subsystem: collection, data, features, models, evaluation, experiments, inference (Phase 2). |
| `artifacts/` | `reports/` (committed experiment results), `models/` (git-ignored). |
| `scripts/` | Developer utilities (e.g. `generate_traffic.py`). |
| `infrastructure/` | `docker/`, `kubernetes/`, `terraform/` (Phase 7-8). |
| `tests/` | Tests, one subpackage per component (`tests/orders_service/`, `tests/ml/`). |
| `docs/` | `architecture/`, `decisions/` (ADRs), `development/`, `phases/`. |

`apps` vs `services` vs `ml` is kept because the three have genuinely different
shapes: `apps` are externally reachable (the platform API, and demo apps under
observation); `services` are SentinelOps-internal, event-driven, and many; `ml`
is offline/batch pipeline code with a different dependency set and lifecycle.
`orders-service` sits in `apps/` because it is a stand-in for a customer's
production application, not a SentinelOps component. Empty directories are
**not** committed — each appears when its first real file does.

Packaging note: all Python currently ships as one distribution
(`sentinelops-ai`) with multiple import packages. Splitting per-service
packaging is a Phase 8 concern.
