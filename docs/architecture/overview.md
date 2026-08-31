# Architecture Overview

This document describes the **intended** architecture of SentinelOps AI and
tracks what is actually built. It is updated at the end of every phase.

## Status legend

- **IMPLEMENTED** — exists in the repository and is tested.
- **PLANNED** — target design; no code yet.

## Current state (Phase 0)

**IMPLEMENTED**

- A single FastAPI application (`apps/api/sentinelops_api`) with `GET /health`
  and `GET /`.
- Typed configuration via environment variables (`APP_` prefix).
- Test, lint, type-check, Docker, and CI scaffolding.

Nothing else below is implemented yet.

## Target architecture

### 1. Instrumented services — PLANNED

Multiple small services emit telemetry (metrics, logs, traces) via
**OpenTelemetry**. Collection uses an OpenTelemetry Collector / **Grafana Alloy**
pipeline (not Promtail). The services are the system under observation; some
synthetic/fault-injecting services exist to generate production-like incidents
for evaluation.

### 2. Event backbone — PLANNED

**Apache Kafka** is the backbone. Telemetry summaries, anomaly events, incident
lifecycle events, agent findings, approval decisions, and remediation/verification
outcomes all flow as events on well-defined topics. Services are decoupled and
independently deployable. Rationale:
[ADR-001](../decisions/adr-001-event-driven-architecture.md).

### 3. ML anomaly detection — PLANNED

A service consumes telemetry features and scores them with trained models
(scikit-learn / XGBoost; PyTorch only if justified). It emits anomaly events.
The **live** pipeline is trained and evaluated on telemetry whose feature space
matches the live system. Public benchmark datasets (HDFS, BGL, NAB) are used for
**separate** offline experimentation and are never claimed to detect unrelated
live metrics: [ADR-004](../decisions/adr-004-datasets-vs-live-telemetry.md).

Evaluation uses real measurements — precision, recall, F1, PR-AUC where
appropriate, false-positive rate, detection latency. Numbers are never
fabricated.

### 4. Incident correlation — PLANNED

A service groups related anomaly events (by time, service dependency graph,
deployment windows, and shared entities) into a single **incident**. Incidents
are persisted in **PostgreSQL**. **Redis** may be used for correlation windows /
deduplication caches where justified.

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
| Kafka + first instrumented service | 1 |
| ML anomaly detection + offline evaluation | 2 |
| Incident correlation + PostgreSQL | 3 |
| AI RCA agent + evidence tools | 4 |
| Approval + remediation + verification + audit | 5 |
| MLflow + monitoring + drift + retraining | 6 |
| Observability stack | 7 |
| Kubernetes + AWS + Terraform + hardened CI/CD | 8 |

## Repository layout rationale

| Path | Purpose |
| --- | --- |
| `apps/` | Deployable user-facing apps (`api`; `frontend` later). |
| `services/` | Event-driven backend microservices (added from Phase 1). |
| `ml/` | ML lifecycle code: data, features, training, evaluation, inference (Phase 2+). |
| `infrastructure/` | `docker/`, `kubernetes/`, `terraform/` (Phase 7-8). |
| `tests/` | Cross-cutting tests; each app/service also owns focused tests. |
| `docs/` | `architecture/`, `decisions/` (ADRs), `development/`, `phases/`. |

`apps` vs `services` vs `ml` was kept because the three have genuinely different
shapes: `apps` are externally reachable and few; `services` are internal,
event-driven, and many; `ml` is offline/batch pipeline code with a different
dependency set and lifecycle. Empty directories are **not** committed — each
appears when its first real file does.
