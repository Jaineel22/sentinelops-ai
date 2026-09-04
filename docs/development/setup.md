# Development Setup

Every command here works today (through Phase 4). Windows (PowerShell and Git
Bash) and Unix instructions are given.

## Prerequisites

| Tool | Version used | Notes |
| --- | --- | --- |
| Python | 3.12+ (dev machine: 3.14.5) | `requires-python = ">=3.12"` |
| Git | any recent | — |
| Docker Desktop | required for Phase 1+ | Kafka, PostgreSQL, and services run in Compose |
| `make` | optional | bundled with Git for Windows; used for shortcuts |

## 1. Clone

```bash
git clone https://github.com/Jaineel22/sentinelops-ai.git
cd sentinelops-ai
```

## 2. Virtual environment

**Git Bash / macOS / Linux**

```bash
python -m venv .venv
source .venv/Scripts/activate   # Git Bash on Windows
# source .venv/bin/activate     # macOS / Linux
```

**PowerShell**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

## 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install -e ".[dev,ml,incident,detector,rca]"
```

`-e` (editable) means source changes take effect without reinstalling. `[dev]`
adds pytest, httpx, Ruff, and mypy; `[ml]` adds pandas, NumPy, scikit-learn,
SciPy, matplotlib, and joblib (Phase 2); `[incident]` adds SQLAlchemy, asyncpg,
and Alembic and `[detector]` adds httpx (Phase 3); `[rca]` adds `langgraph` and
the `anthropic` SDK (Phase 4). Drop extras you don't need.

## 4. Environment configuration

```bash
cp .env.example .env      # PowerShell: Copy-Item .env.example .env
```

`.env` is git-ignored. All variables use the `APP_` prefix and are read through
`sentinelops_api.config.Settings`. Defaults are safe for local use, so `.env` is
optional in Phase 0.

## 5. Run the API

```bash
uvicorn sentinelops_api.main:app --reload --app-dir apps/api
```

- Health: <http://localhost:8000/health> → `{"status":"ok"}`
- Root:   <http://localhost:8000/>
- OpenAPI docs: <http://localhost:8000/docs>

## 6. Phase 1: the event pipeline (Kafka + orders-service)

Start everything in Docker:

```bash
docker compose up --build -d          # api:8000, kafka:29092, orders-service:8001, orders-consumer
docker compose ps
curl http://localhost:8001/health     # {"status":"ok"}
curl http://localhost:8001/ready      # {"status":"ready","kafka":"connected"}
```

Create an order and watch the consumer pick it up:

```bash
curl -X POST http://localhost:8001/orders \
  -H 'content-type: application/json' \
  -d '{"customer_id":"customer-1","amount":1499.00,"currency":"INR"}'

docker compose logs orders-consumer | tail -n 5     # "order event received"
curl -s http://localhost:8001/metrics | grep '^orders_'
```

Run `orders-service` on the host instead (needs only Kafka in Compose):

```bash
docker compose up -d kafka
make run-orders            # uses KAFKA_BOOTSTRAP_SERVERS=localhost:29092, port 8001
```

Generate controlled telemetry scenarios:

```bash
python scripts/generate_traffic.py --scenario normal   --rate 5 --duration 60
python scripts/generate_traffic.py --scenario latency  --rate 5 --duration 45
python scripts/generate_traffic.py --scenario sequence --duration 30   # normal→latency→errors→surge→recovery
```

See [telemetry-scenarios.md](telemetry-scenarios.md) for what each scenario
should do to metrics, traces, and logs.

Tear down: `docker compose down` (add `-v` to drop volumes).

## 7. Tests

```bash
pytest                     # unit tests (no broker needed) — or: make test
```

Integration tests (need a broker; the Phase 3/4 ones also need PostgreSQL):

```bash
docker compose up -d kafka postgres
make db-migrate                    # incident lineage — alembic upgrade head
make db-migrate-rca               # rca lineage (alembic_version_rca)
make test-integration             # sets KAFKA_BOOTSTRAP_SERVERS + DB_URL + DB_TEST_URL
```

## 8. Phase 2: ML anomaly detection

The `ml/` subsystem is offline. The committed datasets under
`ml/data/processed/sentinelops/` mean you can run everything without regenerating
data:

```bash
make ml-test                       # ML unit tests only
make nab-download                  # Track B benchmark data (network; git-ignored)
make ml-experiments                # all 6 experiments -> artifacts/reports/ + summary.md
make ml-experiment NAME=exp2_isolation_forest_sentinelops

# use a trained model through the Phase 3 boundary
python -m ml.inference \
  artifacts/models/exp2_isolation_forest_sentinelops__isolation_forest.joblib \
  ml/data/processed/sentinelops/run_a/windows.csv
```

Regenerate Track A telemetry (needs Docker + a host `orders-service`, ~20 min per
run):

```bash
docker compose up -d kafka
make run-orders &                                   # host orders-service on :8001
python -m ml.collection.collector --run-id run_a --plan main
python -m ml.collection.collector --run-id run_b --plan holdout
make data-prepare                                   # -> ml/data/processed/sentinelops/
```

### Phase 6A: MLflow experiment tracking (optional)

Install the client (`mlflow-skinny`) with the `mlops` extra — it does **not**
change your pandas/numpy versions:

```bash
pip install -e ".[dev,ml,incident,detector,rca,remediation,mlops]"
```

With `MLFLOW_TRACKING_URI` unset, `make ml-experiments` behaves exactly as in
Phase 2. Set it to record runs:

```bash
# Offline, no server (how the tests run) — a local sqlite store:
export MLFLOW_TRACKING_URI="sqlite:///mlruns/mlflow.db"
python -m ml.experiments run exp3_comparison_sentinelops   # + one MLflow run per model

# Or the shared local server (UI at http://localhost:5000):
docker compose up -d --build mlflow        # also starts postgres + the one-shot mlflow-init
export MLFLOW_TRACKING_URI="http://localhost:5000"
python -m ml.experiments run exp3_comparison_sentinelops
```

`metrics.json` / `summary.md` under `artifacts/reports/` stay authoritative and
unchanged either way.

### Phase 6B: model registry + alias promotion

Needs a DB-backed store (sqlite or the `http://localhost:5000` server) — a bare
`file:` store cannot hold model versions or aliases.

```bash
# after an experiment run has logged a run to MLflow, grab its run id from the UI
python -m ml.mlops register --model-path artifacts/models/<exp>__isolation_forest.joblib --run-id <run_id>
python -m ml.mlops promote  --candidate-version 1 --reason "initial champion"
python -m ml.mlops list-models
python -m ml.mlops get-champion
```

`promote` runs the deterministic gate (`ml/mlops/promotion.py`, ADR-033) — a
candidate that misses the F1 / recall / PR-AUC floors or regresses on F1 vs the
champion is **rejected** (exit 1) and the `champion` alias is left untouched.

### Phase 6C: registry-backed inference

```bash
# host / compose default: MLFLOW_TRACKING_URI empty -> anomaly-detector loads the
# local bundle, exactly as Phases 3-5.

# opt in (from the host):   MLFLOW_TRACKING_URI=http://localhost:5000 make run-detector
# opt in (under compose):   set MLFLOW_TRACKING_URI=http://mlflow:5000 in your .env, then
MLFLOW_TRACKING_URI=http://mlflow:5000 docker compose up -d --build anomaly-detector
curl -s http://localhost:8003/ready | python -m json.tool      # model_source / model_version
curl -s http://localhost:8003/model-info | python -m json.tool
```

`MLFLOW_REQUIRED=false` (default) falls back to the local bundle (logged,
`model_source: local-fallback`) if the registry is down; `=true` makes `/ready`
report 503 instead. See [docs/architecture/phase-6.md](../architecture/phase-6.md).

### Phase 6D: drift detection

```bash
# freeze a reference distribution from training data...
python -m ml.monitoring baseline --model-version 1 \
  --data ml/data/processed/sentinelops/run_a/windows.csv \
  --output artifacts/models/run_a__baseline.joblib

# ...then check a later window against it (exit 1 on significant drift)
python -m ml.monitoring check \
  --baseline artifacts/models/run_a__baseline.joblib \
  --data ml/data/processed/sentinelops/run_b/windows.csv \
  --output artifacts/reports/drift_run_b.json
```

`run_experiment` also freezes a baseline per experiment (saved next to the models
and, when `MLFLOW_TRACKING_URI` is set, logged with each run so a registered
model version carries it). PSI bands: `<0.1` none, `0.1-0.25` moderate, `>=0.25`
significant. Methodology + limitations:
[ADR-034](../decisions/adr-034-drift-detection-methodology.md).

### Phase 6E: reproducible retraining

Needs a reachable MLflow (`MLFLOW_TRACKING_URI`).

```bash
export MLFLOW_TRACKING_URI="sqlite:///mlruns/mlflow.db"   # or http://localhost:5000

# retrain (reuses the Phase 2 pipeline), track, register, run the 6B gate
python -m ml.mlops retrain --dataset run_a --seed 42                 # exit 0 = gate passed
python -m ml.mlops retrain --dataset run_a --seed 42 --promote-if-passing

make mlops-retrain-demo      # scripts/retraining_demo.py
make mlops-drift-retrain     # drift detected -> retrain -> gate  (scripts/drift_triggered_retraining.py)
```

Deterministic: same `--dataset` + `--seed` → identical metrics. Promotion is
opt-in and still runs `evaluate_candidate` — no autonomous deployment.

### Phase 6F: end-to-end demo

```bash
make phase6-demo        # scripts/phase6_e2e_demo.py — no server needed
make phase6-summary     # one-screen recap of Phase 6
```

`phase6-demo` runs the whole lifecycle in one deterministic command against a
throwaway sqlite store (printed at the end so you can open it with `mlflow ui`):
train champion on `run_a` → register + promote → load it from the registry →
drift-check on `run_b` → retrain on `run_b` → gate → promote. Set
`MLFLOW_TRACKING_URI` to run against a shared store instead. See
[docs/phase6-summary.md](../phase6-summary.md).

## 9. Phase 3: incident correlation

```bash
# Everything (Phases 1 + 3 + 4):
docker compose up --build
#   orders-service :8001 · incident-correlator :8002 · anomaly-detector :8003
#   rca-agent :8004 · postgres :5432

# Watch an incident form from injected faults:
python scripts/generate_traffic.py --scenario sequence --duration 40 --rate 6
curl -s http://localhost:8002/incidents | python -m json.tool
curl -s "http://localhost:8002/incidents/<id>/evidence" | python -m json.tool
curl -s "http://localhost:8002/incidents/<id>/history"  | python -m json.tool
curl -X POST "http://localhost:8002/incidents/<id>/acknowledge"

# Deterministic in-process demo — no Kafka, no DB:
make incident-scenario
```

Run the services on the host instead:

```bash
docker compose up -d kafka postgres
make db-migrate
make run-correlator &      # :8002
make run-detector &        # :8003  (scrapes host orders-service :8001)
```

Schema changes: edit
`services/incident-correlator/incident_correlator/db/models.py`, then
`make db-revision MSG="..."`, review the generated file, `make db-migrate`.

## 9b. Phase 4: AI RCA agent

`rca-agent` consumes `incident.opened` and investigates. `RCA_MODE=mock` (the
default) needs **no LLM API key**.

```bash
# Deterministic full-chain demo — no Kafka, no DB, no key:
make rca-e2e-scenario            # incident.opened envelope -> consumer -> RCA -> API

# In the running stack (docker compose up), after an incident forms:
curl -s "http://localhost:8004/incidents/<incident-id>/investigation" | python -m json.tool
curl -X POST http://localhost:8004/investigations \
     -H 'content-type: application/json' -d '{"incident_id":"<incident-id>"}'
curl -s "http://localhost:8004/investigations/<rca-id>"       | python -m json.tool
curl -s "http://localhost:8004/investigations/<rca-id>/steps" | python -m json.tool  # just the trace

# On the host instead:
make db-migrate-rca
make run-rca &                   # :8004  (RCA_MODE=mock)

# Live LLM (opt-in; key stays in your shell, never committed):
RCA_MODE=live LLM_PROVIDER=anthropic LLM_API_KEY=sk-ant-... docker compose up --build rca-agent
```

Schema changes: edit `services/rca-agent/rca_agent/db/models.py`, then
`cd services/rca-agent && alembic revision --autogenerate -m "..."` (its own
`alembic_version_rca` lineage), review, `make db-migrate-rca`.

## 9c. Phase 7: inference metrics + Grafana dashboard

`docker compose up --build` now also starts **Prometheus** (`:9090`) and
**Grafana** (`:3000`). Prometheus scrapes the anomaly-detector's `/metrics`
every 5 s; Grafana auto-provisions its data source and the
**"Anomaly Detector - Inference & Performance"** dashboard.

```bash
docker compose up -d --build kafka orders-service anomaly-detector prometheus grafana

# drive some inference traffic so the panels fill in
python scripts/generate_traffic.py --scenario sequence --duration 60 --rate 6

curl -s http://localhost:8003/ready | python -m json.tool          # inference_stats rollup (7C)
curl -s http://localhost:8003/ready/stats | python -m json.tool    # just the stats
curl -s http://localhost:9090/api/v1/targets | python -m json.tool # anomaly-detector = up
```

**Grafana:** <http://localhost:3000> — user `admin`, password `admin` (dev only).
Dashboards → Browse → *Anomaly Detector - Inference & Performance*.

| Panel | Shows |
| --- | --- |
| Inference Requests / sec · Anomalies Detected / sec | throughput (`rate(...[1m])`) |
| Inference Latency (p50/p95/p99) | model scoring latency percentiles |
| End-to-End Detection Latency (p95) | window-close → anomaly-published (7B) |
| Anomaly Score Distribution | histogram heatmap of raw scores |
| Current Model Version / Type | `detector_model_info` labels (7A) |
| Total Inferences / Anomalies · Anomaly Rate % | range aggregates (green <10 %, red >25 %) |
| Service Uptime · Latest Inference Latency | `process_start_time_seconds`, mean 5 m latency |

Verify the whole surface (in-process, needs no Docker) or against the running
service:

```bash
make phase7-verify                                                  # in-process
python scripts/phase7_verify.py --url http://localhost:8003 \
    --grafana-url http://localhost:3000                             # live
```

The metric surface is documented in
[docs/architecture/phase-7.md](../architecture/phase-7.md) ·
[docs/phase7-summary.md](../phase7-summary.md).

## 10. Lint, format, type-check

```bash
ruff check .            # lint
ruff format .           # apply formatting
ruff format --check .   # verify formatting (used in CI)
mypy                    # strict type-check (config in pyproject.toml)
```

## 11. Make shortcuts (Git Bash)

Run `make help` for the full list. Common ones:

| Command | Does |
| --- | --- |
| `make install` | install deps into `.venv` (run `make venv` first if needed) |
| `make run` / `make run-orders` | run the platform API (:8000) / orders-service (:8001) |
| `make test` / `make test-integration` / `make ml-test` | unit tests / integration tests / ML tests |
| `make lint` / `make format` / `make typecheck` | Ruff / Ruff / mypy |
| `make check` | lint + typecheck + test (the full gate) |
| `make compose-up` / `make compose-down` / `make compose-logs` | Compose environment |
| `make traffic SCENARIO=latency` | run the traffic generator |
| `make db-migrate` / `make run-correlator` / `make run-detector` / `make incident-scenario` | Phase 3 |
| `make db-migrate-rca` / `make run-rca` / `make rca-scenario` / `make rca-e2e-scenario` | Phase 4 |
| `make ml-experiments` / `make ml-experiment NAME=...` | run Phase 2 experiments |
| `make nab-download` / `make data-prepare` | Track B data / rebuild processed datasets |

On PowerShell, use the explicit commands instead of `make`.

## 12. Docker

```bash
docker build -t sentinelops-ai:api .                                   # platform API
docker build -f apps/orders-service/Dockerfile -t sentinelops-ai:orders-service .
docker build -f ml/Dockerfile -t sentinelops-ai:ml .                   # ML experiment runner
docker build -f services/incident-correlator/Dockerfile -t sentinelops-ai:incident-correlator .
docker build -f services/anomaly-detector/Dockerfile -t sentinelops-ai:anomaly-detector .
docker compose up --build                                              # full env (Phases 1 + 3)

# run the ML experiments in the container, writing to the host artifacts/ dir
docker run --rm -v "$PWD/artifacts:/app/artifacts" sentinelops-ai:ml run all
```

## 13. Git workflow

- `main` is protected; work on branches: `git switch -c phase-<n>/<short-topic>`.
- Keep commits small and scoped; run `make check` before pushing.
- Open a PR into `main`; CI (`.github/workflows/ci.yml`) must pass.
- ADRs for significant decisions go in `docs/decisions/` in the same PR.
