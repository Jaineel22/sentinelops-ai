# Architecture Overview

This document describes the **intended** architecture of SentinelOps AI and
tracks what is actually built. It is updated at the end of every phase.

## Status legend

- **IMPLEMENTED** — exists in the repository and is tested.
- **PLANNED** — target design; no code yet.

## Current state (through Phase 4)

**IMPLEMENTED**

- **Phase 0:** platform API skeleton (`apps/api/sentinelops_api`) with
  `GET /health` and `GET /`; typed env-var config; test/lint/type-check/Docker/CI
  scaffolding.
- **Phase 1:** `orders-service` demo app; Kafka event backbone (`orders.events`,
  versioned `order.created`); OpenTelemetry instrumentation; a trace-continuing
  demo consumer; dev-only failure injection + a traffic generator.
- **Phase 2:** the `ml/` offline anomaly-detection pipeline (leak-safe dataset,
  23-feature layer, Isolation Forest primary, window/event evaluation, NAB track).
- **Phase 3:** `anomaly-detector` (live scrape/score/publish) + `incident-correlator`
  (deterministic correlation + severity, PostgreSQL persistence, idempotent Kafka
  consumer, Incident API, `incident.*` lifecycle events).
- **Phase 4:** `rca-agent` — the `incident.opened` consumer, the bounded LangGraph
  investigation engine, the closed read-only evidence-tool registry, the mock /
  live (`AnthropicLlmClient`) LLM boundary, deterministic `validate_report`, the
  Investigation HTTP API, and Docker Compose integration (`rca-migrate` +
  `rca-agent`). Recommendation-only — no executor.

See the per-phase docs. Sections 6-10 below are **PLANNED**.

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

### 5. AI RCA agent — IMPLEMENTED (Phase 4)

`services/rca-agent` reacts to the Phase 3 `incident.opened` Kafka event and runs
an explicit **LangGraph** investigation state machine
(`plan → collect → analyze → verify → synthesize → validate`) over the incident.
Evidence comes only through a fixed, closed registry of **read-only** tools
(incident / anomaly / timeline / related-incident / point-in-time metrics /
health — with logs, traces, deployments and dependencies registered but
explicitly unavailable, never fabricated —
[ADR-020](../decisions/adr-020-controlled-read-only-evidence-tools.md)). The LLM
only *proposes* plans, hypotheses and a synthesis; deterministic code owns the
tool allow-list, argument validation, resource limits, evidence ids, state
transitions, and a final `validate_report` gate — and there is **no executor**,
so a prompt injection in evidence cannot cause an action
([ADR-021](../decisions/adr-021-llm-boundary-and-injection-defense.md)). Output
is a strongly typed, evidence-grounded `RCAReport` whose recommendation always
requires human approval. A deterministic mock reasoner drives the same graph in
CI; `RCA_MODE=live` swaps in `AnthropicLlmClient` behind the same `LlmClient`
protocol (forced-tool-use structured output, bounded, key held as a `SecretStr` —
[ADR-022](../decisions/adr-022-live-llm-provider.md)). An idempotent Kafka
consumer (one investigation per incident) triggers investigations; a small HTTP
API (`POST /investigations`, `GET /investigations/{id}` and `/steps`,
`GET /incidents/{id}/investigation`) exposes their state, trace, and report;
`docker compose up` runs the whole chain with no API key
([ADR-023](../decisions/adr-023-rca-agent-integration.md)). See
[phase-4.md](phase-4.md) ·
[ADR-002](../decisions/adr-002-ml-and-llm-separation.md) ·
[ADR-019](../decisions/adr-019-rca-agent-service-and-boundary.md).

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
| Incident correlation + PostgreSQL | 3 (done) |
| AI RCA agent + evidence tools | 4 (done) |
| Approval + remediation + verification + audit | 5 |
| MLflow + monitoring + drift + retraining | 6 |
| Observability stack | 7 |
| Kubernetes + AWS + Terraform + hardened CI/CD | 8 |

## Repository layout rationale

| Path | Purpose |
| --- | --- |
| `apps/api/` | The SentinelOps platform API (Phase 0). |
| `apps/orders-service/` | Demo app under observation (Phase 1). |
| `services/` | SentinelOps-internal event processors — `anomaly-detector` (live scoring) and `incident-correlator` (correlation + persistence + Incident API), Phase 3; `rca-agent` (AI investigation + Investigation API), Phase 4. |
| `libs/sentinelops_common/` | Shared library: Kafka event envelope, JSON logging + OpenTelemetry setup, JSON producer, idempotent consumer. |
| `ml/` | ML anomaly-detection subsystem: collection, data, features, models, evaluation, experiments, inference (Phase 2). |
| `artifacts/` | `reports/` (committed experiment results), `models/` (git-ignored). |
| `scripts/` | Developer utilities: `generate_traffic.py`, `incident_scenario.py` (Phase 3 demo), `rca_scenario.py` / `rca_e2e_scenario.py` (Phase 4 demos). |
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
