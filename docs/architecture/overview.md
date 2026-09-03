# Architecture Overview

This document describes the **intended** architecture of SentinelOps AI and
tracks what is actually built. It is updated at the end of every phase.

## Status legend

- **IMPLEMENTED** — exists in the repository and is tested.
- **PLANNED** — target design; no code yet.

## Current state (through Phase 6)

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
- **Phase 5:** `remediation-controller` — the closed action catalogue + structural
  no-command proposal model (5A), the LLM-free 9-rule policy engine (5B),
  PostgreSQL persistence + the human approval workflow/API (5C), the allow-listed
  `LocalSimulationExecutor` + dry-run (5D), the append-only audit trail with a
  PostgreSQL tamper trigger (5E), the deterministic observe-only recovery
  verifier (5F), and best-effort `remediation.events` Kafka lifecycle events
  published after each committed transition (5G). Human approval is mandatory;
  no real infrastructure is touched.

See the per-phase docs. Section 8 (MLOps lifecycle) is implemented; sections
9–10 below are **PLANNED**.

## Target architecture

### 1. Instrumented services — PARTIALLY IMPLEMENTED (Phase 1)

Small services emit telemetry (metrics, logs, traces) via **OpenTelemetry**.
`orders-service` is the first — a demo app under observation, with built-in
fault injection to generate production-like scenarios. More services, and an
OpenTelemetry Collector / **Grafana Alloy** collection pipeline (not Promtail),
are planned for Phase 7.

### 2. Event backbone — PARTIALLY IMPLEMENTED (Phase 1)

**Apache Kafka** is the backbone. A single-node KRaft broker carries
`orders.events` (Phase 1); since Phase 3, `anomaly.events` and `incident.events`
(plus `anomaly.events.dlq`); and since Phase 5G, `remediation.events` — a
versioned `RemediationLifecycleV1` stream the `remediation-controller` publishes
(keyed by `remediation_id`) after each committed lifecycle transition, and
consumes nothing (ADR-030). Services are decoupled and independently deployable.
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

### 6. Human-approved remediation — IMPLEMENTED (Phase 5, Sub-phases 5A–5G)

```
AI recommendation → policy validation → human approval
  → allow-listed action → execution → audit log → recovery verification
```

No change to a running system happens without a human decision, and only
pre-defined allow-listed actions can ever be executed:
[ADR-003](../decisions/adr-003-human-in-the-loop-remediation.md),
[ADR-024](../decisions/adr-024-remediation-domain-and-action-catalogue.md),
[ADR-025](../decisions/adr-025-deterministic-remediation-policy-engine.md),
[ADR-026](../decisions/adr-026-remediation-persistence-and-approval-workflow.md),
[ADR-027](../decisions/adr-027-allow-listed-executor-and-local-simulation.md),
[ADR-028](../decisions/adr-028-append-only-remediation-audit-trail.md),
[ADR-029](../decisions/adr-029-recovery-verification.md).
Every step is recorded — immutably — for auditability, and recovery is
independently verified.

**Sub-phase 5A (done):** `services/remediation-controller` domain foundation — a
closed `RemediationActionType` enum + immutable `ACTION_CATALOGUE`, a structural
`RemediationProposal` (no command-shaped field, `extra="forbid"`,
`requires_approval: Literal[True]`), a `ServiceTarget` allow-list that fails
closed on unknown services, the remediation lifecycle state machine (`EXECUTING`
reachable only from `APPROVED`), the `RemediationApproval` model, and the
deterministic `proposal_from_rca` mapping.

**Sub-phase 5B (done):** `remediation_controller.policy` — a deterministic,
**LLM-free** 9-rule `PolicyEngine` that independently validates a
`RemediationProposal` (state, action, target, environment, severity, parameters,
risk/blast-radius from the *catalogue* not `proposal.risk_level`, expiry,
cooldown) and returns a structured `PolicyDecision`. Every rule fails closed;
`apply_policy_decision` can only reach `PENDING_APPROVAL` or `BLOCKED`.

**Sub-phase 5C (done):** the remediation-controller becomes a running service
(`:8005`) — its own Alembic lineage (`alembic_version_remediation`, 2 tables) in
the shared `sentinelops` database, a `RemediationService` (`proposal_from_rca` →
`PolicyEngine` → persist as `PENDING_APPROVAL`/`BLOCKED`), and a FastAPI approval
API (`POST /remediations`, `GET`, `POST …/approve`, `POST …/reject`). A
deterministic role→**catalogue-risk** authorization matrix; immutable
`remediation_approvals` rows (`UNIQUE(remediation_id)`, INSERT-only);
concurrency-safe via `SELECT … FOR UPDATE`. The SQL repo backs the 5B
`RemediationHistoryPort`. Request models are `extra="forbid"`; **no
command-shaped field exists on any model, request, response, or table**.

**Sub-phase 5D (done):** `remediation_controller.executor` — an allow-listed
executor boundary + `LocalSimulationExecutor` (mutates a small in-process
`SimulationState`; **no `subprocess` / Docker / Kubernetes / SSH / cloud SDK**).
`POST /remediations/{id}/execute` runs an `APPROVED` remediation through the typed
executor (`APPROVED → EXECUTING → EXECUTED | EXECUTION_FAILED`, all pre-existing
5A states); `{"dry_run": true}` runs the same guards + executor interface but
persists nothing and mutates no state. One real execution per remediation
(`remediation_executions` table, `UNIQUE(remediation_id)`, `FOR UPDATE`). A
genuine executor failure is recorded as `EXECUTION_FAILED` — never `EXECUTED`.
The executor registry is closed and code-defined (no dynamic class loading).

**Sub-phase 5E (done):** `remediation_controller.audit` + the
`remediation_audit_events` table (migration `0003`, same lineage) — one immutable
`RemediationAuditEvent` per committed lifecycle fact (proposal, policy decision,
block, approve, reject, execution requested/started/succeeded/failed), written
**in the same transaction** as the state change. Append-only at four layers: no
write API, no repository mutation path, the app appends only its own legitimate
events, and a PostgreSQL `BEFORE UPDATE OR DELETE` trigger rejects tampering.
Every stored value passes a secret-redaction boundary (credential-shaped keys and
values → `[REDACTED]`). Read-only `GET /remediations/{id}/audit` — chronological,
paginated. A dry-run writes no event; a concurrent-approval loser's audit event
rolls back with its transaction.

**Sub-phase 5F (done):** `remediation_controller.recovery` + the
`remediation_verifications` table (migration `0004`) — *"execution succeeded"* is
not *"the system recovered"*, so a separate **deterministic, observe-only**
`RecoveryVerifier` runs `EXECUTED → VERIFYING → RECOVERED | RECOVERY_FAILED`. A
bounded virtual-clock poll loop over a `HealthProbe` (a deterministic simulation
of the target's post-remediation health), evaluated against the verifier's *own*
thresholds — never the probe's self-report, never an LLM. Execution-style
`FOR UPDATE` transactions; 3 new audit events written in the same transaction;
`UNIQUE(remediation_id)` + idempotent replay. `POST
/remediations/{id}/verify-recovery` (no body fields). The verifier only observes:
no command, no infrastructure client, no re-execution, no approval bypass; health
responses are untrusted data (redacted, never parsed).

**Sub-phase 5G (done):** `remediation_controller.kafka` + the `remediation.events`
topic — after each committed transition the service publishes a versioned
`RemediationLifecycleV1` event (closed set of 11 `event_type`s, a 1:1 mirror of
the audit trail), keyed by `remediation_id`, **best-effort after the transaction
commits** (the same consistency model as `incident.events` / ADR-016; the audit
trail is the durable record, no transactional outbox). The `event_id` is
`uuid5(namespace, audit_id)` so a consumer can dedupe a republish. The payload
carries only safe structured metadata — no command / URL / credential field, by
construction — and every value re-passes the Phase 5E redaction boundary. **The
service consumes no topic; a Kafka message can never become an instruction**
(ADR-030). `/ready` reports a `kafka` field but does not gate readiness on it.
See [phase-5.md](phase-5.md).

### 7. Recovery verification — IMPLEMENTED (Phase 5, Sub-phase 5F)

After an action executes, a deterministic observe-only verifier polls a health
signal against fixed thresholds and records whether the system actually
recovered (`RECOVERED`) or not (`RECOVERY_FAILED`) — see Sub-phase 5F above.

### 8. MLOps lifecycle — IMPLEMENTED (Phase 6)

**What Phase 6 delivers:** the Phase 2 detector becomes a reproducible,
versioned, observable ML lifecycle around the *same* Isolation Forest and the
*same* evaluation methodology. **MLflow** records experiments and holds the model
registry, promoted through **aliases** (`candidate` / `champion` /
`previous-champion`, never deprecated stages) via a deterministic, LLM-free gate.
The anomaly-detector resolves the `champion` alias at startup (fail-safe).
Per-feature **PSI** drift detection runs against a training baseline frozen with
the champion, and a `python -m ml.mlops retrain` workflow reuses the Phase 2
pipeline end to end and runs the same gate — promotion stays opt-in and manual.
One-pager: [phase6-summary.md](../phase6-summary.md).

**Sub-phase 6A (done):** `ml/mlops/` — a typed `MLflowSettings` and a best-effort
`log_run` that mirrors each Phase 2 training run (parameters, hyperparameters,
lineage, real evaluation metrics, artifacts) into MLflow. `ml/experiments/runner.py`
logs to MLflow when `MLFLOW_TRACKING_URI` is set; `metrics.json` / `summary.md`
stay authoritative and unchanged. Docker Compose gains a local MLflow server
(`mlflow` + one-shot `mlflow-init`, Postgres backend store, HTTP-served
artifacts, `http://localhost:5000`) that nothing in Phases 1–5 depends on.
[ADR-031](../decisions/adr-031-mlflow-tracking-and-registry.md).

**Sub-phase 6B (done):** `ml/mlops/registry.py` registers each model bundle as an
MLflow **model version** and manages the `candidate` / `champion` /
`previous-champion` **aliases** (never MLflow stages —
[ADR-032](../decisions/adr-032-model-alias-strategy.md)).
`ml/mlops/promotion.py` is the deterministic gate: `evaluate_candidate` (pure, no
LLM) checks a candidate's F1 / recall / PR-AUC against absolute floors grounded
in the committed Phase 2 numbers and against no-F1-regression vs the champion
([ADR-033](../decisions/adr-033-model-promotion-criteria.md)); a failing
candidate stays `candidate` and the champion is untouched. `python -m ml.mlops`
CLI: `register`, `promote`, `list-models`, `get-champion`.

**Sub-phase 6C (done):** `DetectorService.from_registry(settings)` resolves the
`champion` alias, downloads that version's bundle, and loads it with the existing
`AnomalyDetector.load` (no `mlflow.pyfunc`/`sklearn` flavors). The
`anomaly-detector` service resolves the registry model at startup when
`MLFLOW_TRACKING_URI` is set (cached — no per-request reload); with
`MLFLOW_REQUIRED=true` an unreachable registry makes `/ready` report 503, with
`=false` it falls back to the local bundle, logged, tagged `local-fallback`.
`/ready` and a new `/model-info` report `model_source` / `model_version` /
`model_type`. With `MLFLOW_TRACKING_URI` unset the service behaves exactly as in
Phases 3–5.

**Sub-phase 6D (done):** `ml/monitoring/` — `freeze_baseline` snapshots a
model's **training** feature distribution (per-feature quantile bins + stats,
labels excluded) as a `BaselineDistribution`, stored with the champion model
version. `detect_drift` compares a later window of **production** features
against it with the **Population Stability Index** (per-feature PSI, standard
`<0.1 / 0.1-0.25 / >=0.25` bands, overall = most severe), returning a
`DriftReport`. Prediction drift (anomaly-rate change) is a separate field;
neither is evidence of model *performance* degradation, which needs labels.
Deterministic, no LLM ([ADR-034](../decisions/adr-034-drift-detection-methodology.md)).
CLI: `python -m ml.monitoring {baseline,check}`.

**Sub-phase 6E (done):** `ml/mlops/retraining.py` — `retrain_pipeline(config)`
runs the **existing** Phase 2 pipeline (load → split → features → train →
calibrate on validation → evaluate on test), freezes a drift baseline, logs the
run (6A), registers a new model version (6B), and runs it through the
deterministic `evaluate_candidate` gate vs the champion (6B). Deterministic (same
dataset + seed → same metrics). `python -m ml.mlops retrain --dataset run_a
[--seed N] [--promote-if-passing]`; `scripts/drift_triggered_retraining.py`
shows drift → retrain → gate. Promotion is **opt-in** and still gated — no
autonomous deployment, no scheduler. Orchestration only; no Phase 2 code
reimplemented.

**Sub-phase 6F (done):** `scripts/phase6_e2e_demo.py` (`make phase6-demo`) runs
the whole lifecycle deterministically in one command (no server needed);
`tests/ml/test_phase6_e2e.py` asserts it; a non-blocking `mlflow-integration` CI
job exercises the Postgres-backed store; docs (this file, `phase-6.md`,
`phase6-summary.md`, `README.md`, `roadmap.md`, `setup.md`) are complete.

Phase 6 is **complete** — see [phase-6.md § 10](phase-6.md) for the exit-criteria
checklist. Full write-up: [phase-6.md](phase-6.md) ·
[phase6-summary.md](../phase6-summary.md).

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
| Approval + remediation + verification + audit + lifecycle events | 5 (done) |
| MLflow tracking + registry + drift + retraining | 6 (done) |
| Observability stack | 7 |
| Kubernetes + AWS + Terraform + hardened CI/CD | 8 |

## Repository layout rationale

| Path | Purpose |
| --- | --- |
| `apps/api/` | The SentinelOps platform API (Phase 0). |
| `apps/orders-service/` | Demo app under observation (Phase 1). |
| `services/` | SentinelOps-internal event processors — `anomaly-detector` (live scoring) and `incident-correlator` (correlation + persistence + Incident API), Phase 3; `rca-agent` (AI investigation + Investigation API), Phase 4; `remediation-controller` (RCA→proposal mapping, policy, human approval API, `LocalSimulationExecutor`, audit trail, recovery verification, `remediation.events` publisher), Phase 5. |
| `libs/sentinelops_common/` | Shared library: Kafka event envelope, JSON logging + OpenTelemetry setup, JSON producer, idempotent consumer. |
| `ml/` | ML anomaly-detection subsystem: collection, data, features, models, evaluation, experiments, inference (Phase 2). |
| `artifacts/` | `reports/` (committed experiment results), `models/` (git-ignored). |
| `scripts/` | Developer utilities: `generate_traffic.py`, `incident_scenario.py` (Phase 3 demo), `rca_scenario.py` / `rca_e2e_scenario.py` (Phase 4 demos), `remediation_e2e_scenario.py` (Phase 5 full-chain demo). |
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
