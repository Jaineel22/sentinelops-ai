# Development Setup

Phase 0. Every command here works today. Windows (PowerShell and Git Bash) and
Unix instructions are given.

## Prerequisites

| Tool | Version used | Notes |
| --- | --- | --- |
| Python | 3.12+ (dev machine: 3.14.5) | `requires-python = ">=3.12"` |
| Git | any recent | — |
| Docker Desktop | optional | only for `make docker-*` |
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

## 6. Tests

```bash
pytest
```

## 7. Lint, format, type-check

```bash
ruff check .            # lint
ruff format .           # apply formatting
ruff format --check .   # verify formatting (used in CI)
mypy                    # strict type-check (config in pyproject.toml)
```

## 8. Make shortcuts (Git Bash)

| Command | Does |
| --- | --- |
| `make install` | create-less install into `.venv` (run `make venv` first if needed) |
| `make run` | run the API with autoreload |
| `make test` | run pytest |
| `make lint` | `ruff check` |
| `make format` | `ruff format` + `ruff check --fix` |
| `make typecheck` | `mypy` |
| `make check` | lint + typecheck + test (the full gate) |
| `make docker-build` / `make docker-run` | build / run the image |

On PowerShell, use the explicit commands above instead of `make`.

## 9. Docker

```bash
docker build -t sentinelops-ai:phase0 .
docker run --rm -p 8000:8000 sentinelops-ai:phase0
# or
docker compose up --build
```

Then check <http://localhost:8000/health>.

## 10. Git workflow

- `main` is protected; work on branches: `git switch -c phase-<n>/<short-topic>`.
- Keep commits small and scoped; run `make check` before pushing.
- Open a PR into `main`; CI (`.github/workflows/ci.yml`) must pass.
- ADRs for significant decisions go in `docs/decisions/` in the same PR.
