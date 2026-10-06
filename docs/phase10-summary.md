# Phase 10 — Frontend MVP (summary)

## Overview

Phases 0–9 built the full backend loop (telemetry → anomaly → incident →
cross-service correlation → RCA → human-approved remediation) but left it
headless. Phase 10 adds **`apps/frontend/`** — a small **Next.js 15 / React 19 /
Tailwind** operator dashboard that renders the existing internal APIs and drives
the writes an operator already has: acknowledge / resolve an incident, start an
investigation, and **approve / reject / execute a remediation**. The TypeScript
types mirror the backend
Pydantic models field-for-field, and the four services (incident-correlator
`:8002`, anomaly-detector `:8003`, rca-agent `:8004`, remediation-controller
`:8005`), which have no CORS and no `/api/v1` gateway, are reached through
**Next.js server-side proxy rewrites** under one same-origin `/api/*` prefix.
The approval form collects an approval reason and posts it to the existing
endpoint. The backend now derives the effective approver identity and role from
the signed JWT rather than trusting those legacy request-body fields. A
follow-up hardening pass
(**Phase 10.1**, below) added a real JWT login + RBAC gate to the dashboard
itself; Phase 10.2 extends the same bearer token validation to all four backend
services and keeps only health/metrics public.

## Phase 10.1 — Auth, RBAC, CI & auto-refresh (hardening)

- **JWT login** — `apps/api` gained `/api/v1/auth/{login,me,register}`
  (`sentinelops_api/auth.py` + `routes/auth.py`; PyJWT, PBKDF2-HMAC password
  hashing, an in-memory demo user store). Three roles, `viewer < approver <
  admin`: `admin`/`admin123`, `approver`/`approver123`, `viewer`/`viewer123`.
- **Frontend login + RBAC** — `app/login/page.tsx`, `app/lib/auth.ts`,
  `app/components/AuthGuard.tsx` (blocks every route until a token validates
  against `/auth/me`). Approve/reject/execute and acknowledge/resolve render
  only for `hasRole("approver")`; `Nav.tsx` shows the signed-in user + role.
- **Phase 10.2 scope**: the incident/RCA/remediation/detector services validate
  the forwarded token; direct unauthenticated calls fail closed. See
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
- **Python side** — shared authentication was added to the platform API and
  four data-plane services, with cross-service contract coverage in
  `tests/test_auth_integration.py`. Targeted auth/shared-library tests and
  Ruff/format/mypy checks passed. The historical full suite is not yet green:
  legacy unauthenticated service tests need bearer-token fixture updates.
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

- **The backend services (incident/RCA/remediation/detector) validate the
  shared bearer token**; direct unauthenticated API calls fail closed.
- **Demo-grade credentials** — 3 hardcoded users, in-memory (resets on
  restart). A deployment must provide its own `JWT_SECRET_KEY`.
- **No MLflow / drift in the UI**; **remediations aren't proposed from the UI**
  (the controller creates them from an RCA recommendation).
- **Port is `3100`** (Grafana owns `3000`).

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

## Phase 10.2 — Security hardening and frontend tests

Phase 10.2 adds shared JWT validation to the incident-correlator, RCA agent,
remediation-controller, and anomaly-detector. Protected endpoints fail closed
with `401` for missing/invalid/expired tokens and `403` for insufficient roles.
Remediation approval records the authenticated JWT subject and role rather than
client-supplied identity fields. `JWT_SECRET_KEY` is now required and Compose
fails fast when it is absent.

The frontend attaches the bearer token to data-plane calls and adds Jest +
Testing Library coverage for auth, route guarding, remediation RBAC, and
incident RBAC. The live Anthropic walkthrough is in `docs/demo.md` and uses
`scripts/live_rca_demo.sh`; mock RCA remains the default.

## Phase 11 deployment foundation

Phase 11 documentation and configuration are maintained separately from this
frontend summary:

- [Kubernetes/Kind deployment](deployment/kubernetes.md)
- [EKS deployment](deployment/eks.md)
- [Terraform infrastructure](deployment/terraform.md)
- [Load testing](deployment/load-testing.md)
- [Cost guidance](deployment/costs.md)

These documents distinguish static configuration validation from live execution.
No cloud resources, cluster status, or performance metrics are claimed without
an actual run.
