# apps/api — SentinelOps AI API

FastAPI application. **Phase 0 scope:** health/info endpoints only — no auth,
messaging, persistence, ML, or agent logic.

| Path | Purpose |
| --- | --- |
| `sentinelops_api/main.py` | App factory + `GET /health`, `GET /` |
| `sentinelops_api/config.py` | Typed env-var settings (`APP_` prefix) |

Run from the repo root:

```bash
uvicorn sentinelops_api.main:app --reload --app-dir apps/api
```

Tests live in the repo-root [`tests/`](../../tests/).
