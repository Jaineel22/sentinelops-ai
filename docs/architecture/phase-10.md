# Phase 10 — Frontend MVP (operator dashboard)

> Status: **complete**, including the **Phase 10.1 hardening pass** (§9): JWT
> login + RBAC, a frontend CI job, and dashboard auto-refresh. `apps/frontend/`
> is a Next.js 15 / React 19 / Tailwind dashboard over the existing internal
> APIs plus one small, additive backend change (`apps/api`'s new auth routes —
> the incident/RCA/remediation/detector services are still untouched). One-line
> recap: [../phase10-summary.md](../phase10-summary.md) ·
> [../../apps/frontend/README.md](../../apps/frontend/README.md).
>
> Numbering: Phase 8 Incident Engine · Phase 9 AI RCA Agent · **Phase 10 Frontend
> MVP (+ 10.1 hardening)** · Phase 11 orchestration / cloud / IaC.

## 1. Overview

Phases 0–9 built the full backend loop but left it headless — every incident,
RCA report and remediation was a `curl` away. Phase 10 adds a small **operator
dashboard**: triage incidents, read the evidence-backed RCA, and drive the
**human remediation-approval flow** from a browser.

It is deliberately **read-mostly**. The only writes are the ones the backend
already exposes for an operator: acknowledge / resolve an incident, start an
investigation, and approve / reject / execute a remediation. There is no new
business logic in the frontend — it renders API responses and posts to existing
endpoints.

## 2. Stack & structure

`apps/frontend/` (Next.js **App Router**, React 19, TypeScript strict, Tailwind
v3). No state library, no data-fetching library — client components with
`fetch` + `useState`/`useEffect` are enough for this surface.

```
apps/frontend/
├── next.config.mjs           server-side proxy rewrites (see §4)
├── app/
│   ├── layout.tsx · globals.css · page.tsx          (dashboard)
│   ├── incidents/page.tsx                            (list + filters)
│   ├── incidents/[id]/page.tsx                       (detail)
│   ├── models/page.tsx                               (provenance + inference stats)
│   ├── lib/  api.ts · types.ts · format.ts
│   └── components/  Nav · Badge · IncidentTable · EvidenceList ·
│                    StateHistory · RelatedIncidents · RcaReport · RemediationPanel
└── Dockerfile                Next standalone, multi-stage
```

`app/lib/types.ts` mirrors the backend Pydantic models field-for-field (incident
`Severity` is `INFO|LOW|MEDIUM|HIGH|CRITICAL`, the RCA `root_cause` is a
`{statement, confidence, evidence_ids, reasoning_summary}` object, the
investigation status enum has 9 values, etc.) — **no invented fields**.

## 3. Views

| Route | Reads | Writes |
| --- | --- | --- |
| `/` dashboard | `GET :8002/incidents?limit=200`, `GET :8003/model-info`, `GET :8003/ready/stats` | — |
| `/incidents` | `GET :8002/incidents?service=&status=&severity=` | — |
| `/incidents/[id]` | `GET :8002/incidents/{id}` (evidence, history, `related_incidents`), `GET :8004/incidents/{id}/investigation`, `GET :8005/remediations?incident_id={id}` | `POST :8002/incidents/{id}/acknowledge` · `/resolve`; `POST :8004/investigations`; `POST :8005/remediations/{id}/approve` · `/reject` · `/execute` |
| `/models` | `GET :8003/model-info`, `GET :8003/ready/stats` | — |

**RCA panel** (`RcaReport.tsx`): fetches the latest investigation for the
incident; a `404` renders a "Start investigation" button (`POST /investigations`,
idempotent per incident). Once a report exists it shows the summary, root cause
(or an honest "Undetermined — insufficient evidence"), findings, hypotheses with
their `SUPPORTED / REFUTED / UNVERIFIED / CONFLICTING` verdict, the recommended
action (always tagged "requires human approval"), and the unavailable evidence
sources.

**Remediation panel** (`RemediationPanel.tsx`): lists the remediations for the
incident. For `PENDING_APPROVAL` it renders an approval form — **approver
identity (required), role (`OPERATOR` / `INCIDENT_RESPONDER` / `ADMINISTRATOR`),
reason** — wired to `POST /remediations/{id}/approve|reject`. For `APPROVED` it
shows an Execute control with a **dry-run** toggle
(`POST /remediations/{id}/execute {dry_run}`). Policy outcome, the recorded
approval, the execution result, and the recovery verification are all displayed.
Remediations are **created by the remediation-controller** from an RCA
recommendation (`scripts/remediation_e2e_scenario.py`) — the dashboard does not
propose them.

**Models page**: live model version / type / source + `source_details` from
`/model-info`, and the inference rollup (throughput, anomaly rate, latency
min/avg/max, uptime, `healthy` + `health_reasons`) from `/ready/stats`. MLflow
experiment metrics, the registry, and PSI drift have **no HTTP surface** on this
service — the page links to `make phase6-summary`, `python -m ml.mlops
get-champion`, and the MLflow UI (`:5000`) rather than inventing an endpoint.

## 4. Backend connection — server-side proxy, no CORS, no gateway

The four services run on separate ports with **no CORS headers** and there is
**no `/api/v1` gateway** (`apps/api:8000` only serves `/health`). Rather than add
CORS middleware to every FastAPI service, `next.config.mjs` `rewrites()` proxy
them **server-side** under one same-origin prefix:

```
/api/incident/*     → INCIDENT_API_URL      (default http://localhost:8002)
/api/rca/*          → RCA_API_URL           (default http://localhost:8004)
/api/remediation/*  → REMEDIATION_API_URL   (default http://localhost:8005)
/api/detector/*     → DETECTOR_API_URL      (default http://localhost:8003)
```

The browser only ever calls `http://localhost:3100/api/*` (same origin). In
`docker compose` the `frontend` service sets those env vars to the internal
service names. **No backend code changed.**

## 5. Authentication — JWT login gates the dashboard UI (Phase 10.1, §9)

The four backend services (incident/RCA/remediation/detector) are still
internal and unauthenticated by design (ADR-003 note) — **unchanged**.
Phase 10.1 adds a real JWT login screen backed by `apps/api`, so the dashboard
itself now requires sign-in and gates write actions by role. See §9 for the
full design and its honest scope limits (the four backend services still don't
check the token — see §9.1).

## 6. Toolchain integration

- **`apps/frontend/Dockerfile`** — multi-stage Next standalone build; the
  `frontend` service in `docker-compose.yml` serves `:3100` and depends on the
  four backend services.
- **`Makefile`** — `frontend-install`, `frontend-dev` (`:3100`),
  `frontend-build`, `frontend-lint` (`next lint` + `tsc --noEmit`),
  `phase10-summary`.
- **`.gitignore`** — `node_modules/`, `apps/frontend/.next/`, `next-env.d.ts`.
- **`pyproject.toml`** — `apps/frontend` added to the Ruff `extend-exclude` and
  mypy `exclude` (defensive; neither tool scans non-Python files anyway).
- **`.env.example`** (root) — a pointer to `apps/frontend/.env.example`.
- **CI** (Phase 10.1) — `.github/workflows/ci.yml` gained a `frontend` job:
  `npm ci` → `npm run lint` → `npm run typecheck` → `npm run build`, independent
  of the Python jobs.

## 7. Verification (actual)

- `npm install && npm run build` — Next production build succeeds; `next lint`
  clean; `tsc --noEmit` clean.
- The full Python suite (`pytest -q`), Ruff, and mypy were unaffected by the
  Phase 10 MVP itself (the frontend added no Python); Phase 10.1 adds 16 new
  backend tests — see §9.6 for the post-10.1 counts.
- Manual: `docker compose up` then `make frontend-dev`, open
  `http://localhost:3100` — incident list, detail, RCA panel, and the remediation
  approve → execute flow render against the live services (drive data first with
  `python scripts/incident_scenario.py` / `scripts/remediation_e2e_scenario.py`).

## 8. Known limitations

- **No MLflow / drift in the UI** — CLI + MLflow-server only; the Models page
  links out.
- **Remediations are not proposed from the UI** — the dashboard only acts on
  remediations the controller already created from an RCA recommendation.
- **No frontend unit tests** — `next build` + `next lint` + `tsc` (now in CI,
  §6/§9) are the gate; no component/integration test suite.
- Next dev/prod port is **3100** (Grafana already owns `3000`).
- See §9.6 for the auth-specific limitations (demo credential store, no
  refresh tokens, the backend services still don't check the token, etc.).

## 9. Phase 10.1 — Auth, RBAC, CI & auto-refresh (hardening)

A follow-up hardening pass over the Phase 10 MVP. **No new features beyond
what's listed here** — the views, proxy wiring, and RBAC-free backend design
from §1–§8 are otherwise unchanged.

### 9.1 Scope — what actually got protected

`apps/api` (the platform API skeleton, previously just `/health` + `/`) gained
JWT auth (`sentinelops_api.auth` + `sentinelops_api.routes.auth`, mounted at
`/api/v1/auth/*`) **and nothing else changed on the backend** — the incident /
RCA / remediation / detector services are still unauthenticated internal
services (unchanged from §5's original Phase 10 note). So concretely:

- The dashboard **requires sign-in** to view any page (`AuthGuard`).
- Write actions the UI exposes (acknowledge/resolve, approve/reject/execute)
  are **gated in the UI** by the signed-in user's role.
- The **underlying write endpoints on remediation-controller /
  incident-correlator still accept any caller** — exactly as in Phase 10. A
  `curl` caller who never goes through the dashboard can still call
  `POST /remediations/{id}/approve` directly. Protecting those endpoints would
  mean adding auth to services deliberately kept internal-only by design; that
  was explicitly out of scope for this pass and would need its own ADR.

This is an honest, deliberate boundary, not an oversight — stated so nobody
mistakes the dashboard's login screen for the whole platform being secured.

### 9.2 Backend — `apps/api` JWT auth

`sentinelops_api/auth.py`: `Role` (`viewer < approver < admin`, StrEnum), an
in-memory `_UserStore` seeded with three demo users, PBKDF2-HMAC password
hashing (200k iterations, per-user deterministic salt — adequate for a demo
credential set, not a production KDF policy), `authenticate_user`,
`create_access_token` / `decode_access_token` (PyJWT, HS256), and
request-scoped helpers (`get_current_user`, `get_current_active_user`,
`require_role`) that read `Request` directly — the same pattern the rest of the
platform uses (`incident_correlator.api._repo`/`_load`), not FastAPI's
`Depends(...)`-in-signature idiom (this project's Ruff config flags `B008` on
that idiom).

`sentinelops_api/routes/auth.py` (`APIRouter(prefix="/api/v1/auth")`):

| Route | Auth | Behaviour |
| --- | --- | --- |
| `POST /login` | none | `{username, password}` → `{access_token, token_type, expires_in}`. Same `401` for a bad password or an unknown user (no username enumeration). |
| `GET /me` | any valid token | `{username, role, disabled}`. |
| `POST /register` | **admin only** | Adds a user to the in-memory store (process-lifetime — no persistence layer). `409` on a duplicate username. |

Demo users (`config.py`'s `AuthSettings`, `JWT_*` env vars for the secret /
algorithm / expiry):

| Username | Password | Role |
| --- | --- | --- |
| `admin` | `admin123` | `admin` |
| `approver` | `approver123` | `approver` |
| `viewer` | `viewer123` | `viewer` |

A token also embeds the role at issue time; `decode_access_token` re-checks it
against the *live* store on every request, so a user whose role changed (or
account removed) after a token was issued is rejected rather than trusted —
fail closed, not just "trust the JWT claims forever."

### 9.3 Frontend — login, RBAC gating, session check

- **`app/login/page.tsx`** — username/password form; on success stores the
  token and redirects to `?next=` (or `/`).
- **`app/lib/auth.ts`** — `login`, `logout`, `fetchMe` (hits `/api/auth/me` to
  confirm the token is still valid server-side, not just present),
  `currentUser()` (decodes the JWT payload client-side for a snappy
  username/role display — never trusted as the security boundary, since every
  write still round-trips through a real endpoint), and `hasRole(minimum)`.
- **`app/components/AuthGuard.tsx`** (wraps `{children}` in `layout.tsx`) —
  redirects to `/login?next=<path>` when there's no token, *and* when
  `/api/auth/me` rejects a present-but-stale/expired token. `/login` itself
  renders unguarded.
- **RBAC gating** — `RemediationPanel.tsx`'s approve/reject form and execute
  control render only for `hasRole("approver")`; otherwise a
  "requires the approver role" note. `incidents/[id]/page.tsx`'s
  Acknowledge/Resolve buttons are disabled the same way, and `Resolve` now
  passes the signed-in username as `actor` instead of the fixed string
  `"dashboard"`. `Nav.tsx` shows the username + a role badge + sign-out.
- **Proxy** — `next.config.mjs` gained one more rewrite:
  `/api/auth/* → AUTH_API_URL/api/v1/auth/*` (default `http://localhost:8000`;
  `http://api:8000` in compose). `app/lib/api.ts` (the incident/rca/remediation/
  detector client) is **unchanged** — it never attaches the token, because
  those services don't check it (see §9.1).

### 9.4 CI & auto-refresh

- **CI** — `.github/workflows/ci.yml` gained a `frontend` job (independent of
  the Python jobs): `npm ci` → `next lint` → `tsc --noEmit` → `next build`.
- **Auto-refresh** — the dashboard (`/`) polls every **10 s**, the incident
  detail page every **15 s**, and the remediation panel every **15 s** with a
  visible on/off checkbox (default on); all three clean up their
  `setInterval` on unmount. First load still shows a loading state; background
  refreshes update silently (no flash).

### 9.5 Verification (actual runs)

- `tests/test_auth.py` — **16 new tests**, all passing (parametrized login for
  all 3 demo users, wrong/unknown/empty credentials, `/me` with no token /
  garbage token / a token whose role no longer matches the live store / an
  expired token, `/register` RBAC for viewer/approver/admin, duplicate
  username, no-token register, and the full admin-outranks-approver-outranks-
  viewer hierarchy over HTTP).
- **Full suite**: `pytest -q` → **1086 passed, 18 deselected** (was 1070 before
  10.1; +16, all in `test_auth.py`). `ruff check` / `ruff format --check` /
  `mypy` all green (348 source files).
- **Live end-to-end**: started `apps/api` and the built frontend, then curled
  through the Next proxy — `POST /api/auth/login` and `GET /api/auth/me` both
  round-tripped real tokens and the correct role, confirming the proxy +
  backend + frontend types agree (not just unit-tested in isolation).
- `npm run build` / `next lint` / `tsc --noEmit` (strict) all clean after the
  login page, `AuthGuard`, RBAC gating, and auto-refresh additions.
- `docker compose config --quiet` — valid with the new `AUTH_API_URL` env var
  and the `frontend → api` dependency.

### 9.6 Known limitations (Phase 10.1-specific)

- **The four backend services remain unauthenticated** — see §9.1. This is the
  single most important scope note for anyone reading this doc.
- **Demo-grade credentials**: three hardcoded users, an in-memory registry that
  resets on restart, a default JWT secret meant only for local dev
  (`JWT_SECRET_KEY` overrides it).
- **No refresh tokens / no logout-everywhere** — a token is valid for its full
  `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` (30 min default) regardless of client-side
  "sign out," which only deletes the local copy.
- **`currentUser()` is a client-side JWT decode**, used only for UI display and
  gating — every actual write still requires the token to pass `/api/auth/me`-
  equivalent server-side validation on `apps/api`; the RBAC gate on the
  dashboard buttons is a UX affordance, not the enforcement boundary (see 9.1).
