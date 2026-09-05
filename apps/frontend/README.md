# SentinelOps frontend (Phase 10 / 10.1)

A small **Next.js 15 / React 19 / Tailwind** operator dashboard over the existing
SentinelOps APIs. Read-mostly: incident triage, the RCA report, and the **human
remediation-approval flow**, with auto-refresh and a JWT login gate.

**Sign in required** — `apps/api` issues the token (`/api/v1/auth/login`); demo
accounts: `admin`/`admin123`, `approver`/`approver123`, `viewer`/`viewer123`
(`viewer < approver < admin`). Approve/reject/execute and acknowledge/resolve
require `approver` or `admin`. Scope note: this login gates **the dashboard
UI** — the incident/RCA/remediation/detector services it reads from are still
internal and unauthenticated by design (ADR-003 note); approve/reject/execute
still send an explicit actor in the request body, exactly as `curl` would.

## What it shows

| Route | Source |
| --- | --- |
| `/login` | `apps/api` `/api/v1/auth/login` + `/me` |
| `/` dashboard | incident-correlator `/incidents` + anomaly-detector `/model-info`, `/ready/stats` — auto-refreshes every 10s |
| `/incidents` | `/incidents?service=&status=&severity=` |
| `/incidents/[id]` | `/incidents/{id}` (evidence, history, related) + rca-agent `/incidents/{id}/investigation` + remediation-controller `/remediations?incident_id=` — auto-refreshes every 15s |
| `/models` | anomaly-detector `/model-info` + `/ready/stats` |

MLflow experiment metrics / registry / PSI drift are CLI + MLflow-server
surfaces, not HTTP APIs — the Models page links to `make phase6-summary` and the
MLflow UI (`:5000`) instead of inventing an endpoint.

## Backend wiring

The four content services run on separate ports with no CORS and no `/api/v1`
gateway. `next.config.mjs` rewrites proxy them **server-side** under one
same-origin `/api/*` prefix, so the browser makes no cross-origin request:

```
/api/incident/*     → INCIDENT_API_URL      (default http://localhost:8002)
/api/rca/*          → RCA_API_URL           (default http://localhost:8004)
/api/remediation/*  → REMEDIATION_API_URL   (default http://localhost:8005)
/api/detector/*     → DETECTOR_API_URL      (default http://localhost:8003)
/api/auth/*         → AUTH_API_URL + /api/v1/auth/*  (default http://localhost:8000)
```

Copy `.env.example` → `.env.local` to override for your setup. `apps/api`
(`make run`, or `docker compose up api`) must be running for login to work —
the other four pages fail gracefully (empty/loading states) if their service
is down, but you cannot get past `/login` without `apps/api`.

## Develop

```bash
cd apps/frontend
npm install
npm run dev          # http://localhost:3100
npm run lint
npm run typecheck    # tsc --noEmit
npm run build
```

From the repo root: `make frontend-install`, `make frontend-dev`,
`make frontend-build`, `make frontend-lint`. CI (`.github/workflows/ci.yml`,
job `frontend`) runs lint + typecheck + build on every push/PR.

## Docker

`docker compose up frontend` builds `apps/frontend/Dockerfile` (Next standalone)
and serves on `http://localhost:3100`, pointing at the internal service names
(including `api` for auth).

## Auth details

See [docs/architecture/phase-10.md §9](../../docs/architecture/phase-10.md) for
the full design (JWT claims, RBAC hierarchy, the `/register` admin-only route,
and the explicit statement of what is — and isn't — protected).
