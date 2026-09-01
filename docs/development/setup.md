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
