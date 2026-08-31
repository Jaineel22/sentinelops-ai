# Development Setup

Every command here works today (through Phase 1). Windows (PowerShell and Git
Bash) and Unix instructions are given.

## Prerequisites

| Tool | Version used | Notes |
| --- | --- | --- |
| Python | 3.12+ (dev machine: 3.14.5) | `requires-python = ">=3.12"` |
| Git | any recent | — |
| Docker Desktop | required for Phase 1 | Kafka + services run in Compose |
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
python -m pip install -e ".[dev]"
```

`-e` (editable) means source changes take effect without reinstalling. `[dev]`
adds pytest, httpx, Ruff, and mypy.

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

Integration test (needs a broker):

```bash
docker compose up -d kafka
KAFKA_BOOTSTRAP_SERVERS=localhost:29092 pytest -m integration    # or: make test-integration
```

## 8. Lint, format, type-check

```bash
ruff check .            # lint
ruff format .           # apply formatting
ruff format --check .   # verify formatting (used in CI)
mypy                    # strict type-check (config in pyproject.toml)
```

## 9. Make shortcuts (Git Bash)

Run `make help` for the full list. Common ones:

| Command | Does |
| --- | --- |
| `make install` | install deps into `.venv` (run `make venv` first if needed) |
| `make run` / `make run-orders` | run the platform API (:8000) / orders-service (:8001) |
| `make test` / `make test-integration` | unit tests / integration tests (needs Kafka) |
| `make lint` / `make format` / `make typecheck` | Ruff / Ruff / mypy |
| `make check` | lint + typecheck + test (the full gate) |
| `make compose-up` / `make compose-down` / `make compose-logs` | Phase 1 environment |
| `make traffic SCENARIO=latency` | run the traffic generator |

On PowerShell, use the explicit commands instead of `make`.

## 10. Docker

```bash
docker build -t sentinelops-ai:api .                                   # platform API
docker build -f apps/orders-service/Dockerfile -t sentinelops-ai:orders-service .
docker compose up --build                                              # full Phase 1 env
```

## 11. Git workflow

- `main` is protected; work on branches: `git switch -c phase-<n>/<short-topic>`.
- Keep commits small and scoped; run `make check` before pushing.
- Open a PR into `main`; CI (`.github/workflows/ci.yml`) must pass.
- ADRs for significant decisions go in `docs/decisions/` in the same PR.
