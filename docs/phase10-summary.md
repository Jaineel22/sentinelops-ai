# Phase 10 — Frontend MVP (summary)

## Overview

Phases 0–9 built the full backend loop (telemetry → anomaly → incident →
cross-service correlation → RCA → human-approved remediation) but left it
headless. Phase 10 adds **`apps/frontend/`** — a small **Next.js 15 / React 19 /
Tailwind** operator dashboard that renders the existing internal APIs and drives
the writes an operator already has: acknowledge / resolve an incident, start an
investigation, and **approve / reject / execute a remediation**. There are **no
backend changes and no new Python** — the TypeScript types mirror the backend
Pydantic models field-for-field, and the four services (incident-correlator
`:8002`, anomaly-detector `:8003`, rca-agent `:8004`, remediation-controller
`:8005`), which have no CORS and no `/api/v1` gateway, are reached through
**Next.js server-side proxy rewrites** under one same-origin `/api/*` prefix.
The approval flow records an explicit `approver_identity` + `approver_role` +
`reason` and posts them to the existing endpoint. A follow-up hardening pass
(**Phase 10.1**, below) added a real JWT login + RBAC gate to the dashboard
itself; the four backend services above are still internal and unauthenticated
by design (ADR-003 note) — unchanged.

## Phase 10.1 — Auth, RBAC, CI & auto-refresh (hardening)

- **JWT login** — `apps/api` gained `/api/v1/auth/{login,me,register}`
  (`sentinelops_api/auth.py` + `routes/auth.py`; PyJWT, PBKDF2-HMAC password
  hashing, an in-memory demo user store). Three roles, `viewer < approver <
  admin`: `admin`/`admin123`, `approver`/`approver123`, `viewer`/`viewer123`.
- **Frontend login + RBAC** — `app/login/page.tsx`, `app/lib/auth.ts`,
  `app/components/AuthGuard.tsx` (blocks every route until a token validates
  against `/auth/me`). Approve/reject/execute and acknowledge/resolve render
  only for `hasRole("approver")`; `Nav.tsx` shows the signed-in user + role.
- **Honest scope**: the incident/RCA/remediation/detector services still don't
  check the token — only `apps/api`'s new routes and the dashboard UI are
  protected. A direct `curl` to `remediation-controller` bypasses the login
  exactly as it could in Phase 10. See
  [phase-10.md §9.1](architecture/phase-10.md) for the full boundary statement.
- **CI** — a new `frontend` job in `.github/workflows/ci.yml`
  (`npm ci` → lint → typecheck → build), independent of the Python jobs.
- **Auto-refresh** — dashboard every 10 s, incident detail every 15 s,
  remediation panel every 15 s (toggle, default on) — all with interval cleanup
  on unmount.
- **Real numbers**: `tests/test_auth.py` — 16 new tests, all passing (login,
  `/me`, expired/tampered tokens, RBAC hierarchy on `/register`). Full suite
  and quality gates below include these.

## Key features

- **Dashboard** (`/`) — incident counts (total / active / critical active) and
  the live detector anomaly rate + model version, from
  `GET /incidents?limit=200` and the anomaly-detector `/model-info` +
  `/ready/stats`.
- **Incidents** (`/incidents`) — a filterable table (`service` / `status` /
  `severity`), debounced, hitting the real query params.
- **Incident detail** (`/incidents/[id]`) — evidence (expandable per anomaly
  window with its signals + correlation reason), lifecycle history,
  cross-service **related incidents** (Phase 8), acknowledge / resolve buttons,
  and the severity-reason breakdown.
- **RCA panel** — the latest investigation for the incident; a `404` offers a
  "Start investigation" button (`POST /investigations`, idempotent). The report
  shows the summary, root cause (or honest "Undetermined — insufficient
  evidence"), findings, hypotheses with their `SUPPORTED / REFUTED / UNVERIFIED /
  CONFLICTING` verdicts, the recommended action tagged "requires human
  approval", and the unavailable evidence sources.
- **Remediation panel** — lists the incident's remediations; for
  `PENDING_APPROVAL` an approval form (identity required, role select, reason)
  → `POST /remediations/{id}/approve|reject`; for `APPROVED` an Execute control
  with a **dry-run** toggle → `POST /remediations/{id}/execute`. Policy outcome,
  the recorded approval, the execution result and the recovery verification are
  all shown.
- **Models** (`/models`) — live model provenance + `source_details` and the
  inference-stats rollup (throughput, anomaly rate, latency min/avg/max, uptime,
  `healthy` + reasons). MLflow metrics / registry / PSI drift have no HTTP
  surface, so the page links to `make phase6-summary` and the MLflow UI rather
  than inventing an endpoint.

## Backend connection

```
browser ──same-origin──> Next server (:3100) ──rewrite──> :8002 incident-correlator
                                              ──rewrite──> :8003 anomaly-detector
                                              ──rewrite──> :8004 rca-agent
                                              ──rewrite──> :8005 remediation-controller
```

`next.config.mjs` `rewrites()`; targets are env vars (localhost by default, the
internal service names in `docker-compose`). No CORS middleware added anywhere.

## Structure

`apps/frontend/` — App Router. `app/lib/{api,types,format}.ts`,
`app/components/{Nav,Badge,IncidentTable,EvidenceList,StateHistory,RelatedIncidents,RcaReport,RemediationPanel}.tsx`,
`app/{page,incidents/page,incidents/[id]/page,models/page}.tsx`. No state or
data-fetching library — client components with `fetch` + hooks.

## Real numbers (actual runs)

- **`npm run build`** — Next production build succeeds (standalone output).
- **`npm run lint`** (`next lint`) — clean.
- **`npm run typecheck`** (`tsc --noEmit`, strict + `noUncheckedIndexedAccess`)
  — clean.
- **Python side** — `apps/api` gained real Python (auth.py, routes/auth.py,
  16 new tests in `tests/test_auth.py`); the other 4 services are still
  untouched. `pytest -q` → **1086 passed, 18 deselected** (was 1070; +16, all
  in `test_auth.py`). `ruff check`/`format --check` and `mypy` (348 files) all
  green.
- **Live end-to-end**: `apps/api` started locally, the built frontend proxied
  `POST /api/auth/login` and `GET /api/auth/me` through to it — real tokens,
  real role, verified over HTTP, not just unit-tested.

## Toolchain

`apps/frontend/Dockerfile` (Next standalone) + a `frontend` service in
`docker-compose.yml` (`:3100`, depends on `api` + the four backends, plus
`AUTH_API_URL`); `make frontend-{install,dev,build,lint}` + `phase10-summary`;
`.gitignore` for `node_modules/` + `.next/`; `apps/frontend` added to the Ruff /
mypy excludes; a `frontend` job in `.github/workflows/ci.yml` (Phase 10.1).

## Known limitations

- **The backend services (incident/RCA/remediation/detector) remain
  unauthenticated** — only `apps/api`'s new routes and the dashboard UI are
  protected (Phase 10.1 §9.1). A direct API caller bypasses the login exactly
  as before.
- **Demo-grade credentials** — 3 hardcoded users, in-memory (resets on
  restart), a default JWT secret meant for local dev only.
- **No MLflow / drift in the UI**; **remediations aren't proposed from the UI**
  (the controller creates them from an RCA recommendation).
- **No frontend unit tests**; port is `3100` (Grafana owns `3000`).

## Commands

```bash
make frontend-install          # npm install
make frontend-dev              # http://localhost:3100 (needs the backend up)
make frontend-lint             # next lint + tsc --noEmit
make frontend-build            # next build
docker compose up frontend     # containerised, :3100
python -m pytest tests/test_auth.py -v      # JWT auth + RBAC tests
curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" -d '{"username":"admin","password":"admin123"}'
# seed data first: python scripts/incident_scenario.py ; python scripts/remediation_e2e_scenario.py
```

Full write-up: [architecture/phase-10.md](architecture/phase-10.md) ·
[apps/frontend/README.md](../apps/frontend/README.md).
