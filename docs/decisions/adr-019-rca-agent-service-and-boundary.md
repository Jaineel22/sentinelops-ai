# ADR-019: Phase 4 RCA agent — separate service, shared database, structural Phase 5 boundary

- Status: Accepted
- Date: 2026-09-01

## Context

Phase 4 adds AI root-cause investigation: an agent reacts to a newly opened
incident, gathers evidence through read-only tools, reasons about cause, and
emits a structured RCA report. This is a different concern from Phase 2
(detection) and Phase 3 (deterministic correlation) — ADR-002 already separates
"ML detects" from "the agent investigates". Three foundational questions had to
be settled before writing any Phase 4 code:

1. Where does the agent live — inside `incident-correlator`, or as its own
   service?
2. How does it read incident data — the incident database, or the Incident API?
3. How is investigation state persisted — a new database, or the existing one?

And one safety question that shapes every contract:

4. How is the Phase 4/5 boundary (RCA only, no remediation execution — ADR-003)
   enforced so that it cannot be crossed even by a successful prompt injection?

## Decision

### A new service, `services/rca-agent`

Not folded into `incident-correlator`. The RCA agent depends on an external LLM
API and is the failure domain most likely to be slow, rate-limited, or down; the
incident correlation path must not inherit that risk. The two services follow
the same shape (FastAPI factory, `sentinelops_common` for Kafka/obs, Alembic
migrations, `InMemory*` fakes for unit tests, SQLite for fast repo tests).

### Reads incidents through the Incident API (HTTP), not the incident database

Phase 3's API was explicitly built "for Phase 4 to consume". The agent's
incident/anomaly/timeline evidence tools call `GET /incidents/{id}`,
`/evidence`, `/history` over HTTP. The agent never imports
`incident_correlator`'s ORM models or touches its tables — the service boundary
stays clean, and a schema change in Phase 3 does not silently break Phase 4.

### Shares the existing PostgreSQL instance and database; own Alembic lineage

The `rca-agent` owns four new tables (`investigations`, `investigation_steps`,
`evidence_records`, `rca_reports`) in the same `sentinelops` database. It runs
its **own** Alembic migration history with a dedicated version table
(`alembic_version_rca`) so the two lineages coexist without colliding. No new
database, no new container — "extend the existing architecture" over "add
infrastructure".

Idempotency for a redelivered `incident.opened` event is a partial unique index
`investigations(incident_id) WHERE status` is non-terminal — the same "one
active X per key" pattern Phase 3 uses for incidents.

### Phase 4 contracts are Pydantic (not dataclasses like Phase 3's domain)

The RCA report is simultaneously the LLM's structured-output target (needs JSON
schema generation), the REST API response, the persisted document, and the
input to the deterministic validation layer. Pydantic serves all four; a
dataclass would not.

### Structural Phase 4/5 boundary

The boundary is enforced by the *shape of the data*, not only by prompt
instructions:

- The only remediation output the agent can produce is a `RecommendedAction`
  whose `action_type` is a **closed enum** of recommendation categories
  (`RESTART_SERVICE`, `ROLL_BACK_DEPLOYMENT`, …). There is no free-text command
  field.
- `RecommendedAction.requires_human_approval` is `Literal[True]` — Pydantic
  rejects any attempt to set it `False`.
- There is **no executor** anywhere in `rca-agent`: no shell, no Docker/kubectl
  client, no write path to any system. Even a fully successful prompt injection
  can, at worst, produce a poorly-worded recommendation string — it cannot cause
  an action, because no code consumes a `RecommendedAction` to do anything.

Phase 5 will own the recommendation → policy → human approval → allow-listed
action → execution → audit pipeline (ADR-003).

## Alternatives considered

- **Fold the agent into `incident-correlator`.** Rejected: couples the LLM
  failure domain to incident correlation and mixes two clearly separate
  responsibilities (ADR-002).
- **Read incidents directly from the incident database.** Rejected: tightly
  couples the two services' schemas; the Incident API exists precisely to avoid
  this.
- **A separate PostgreSQL database or instance for Phase 4.** Rejected:
  unnecessary infrastructure for a solo project; one shared database with
  per-service table ownership and migration lineages is enough.
- **Frozen dataclasses for the domain (Phase 3 style).** Rejected: the report
  must round-trip through JSON schema and be validated at multiple boundaries.
- **Enforce the no-remediation rule with prompt text only.** Rejected as
  insufficient on its own — the structural guarantees (closed enum, no executor)
  hold regardless of model behaviour.

## Consequences

- `rca-agent` requires the Incident API to be reachable during an investigation.
  If it is not, the investigation terminates as `FAILED` with a clear reason; it
  never crashes the service or fabricates incident data.
- Two Alembic histories live in one database. `make` targets and the compose
  one-shot migrate jobs are kept separate and clearly named per service.
- The `Database` engine wrapper is a ~40-line copy of
  `incident_correlator.db.engine` rather than a cross-service import. A future
  refactor could hoist it into `sentinelops_common`.
- Later sub-phases build on this foundation: the controlled read-only tool
  registry (4B, its own ADR), the LangGraph investigation state machine and
  mock/live LLM split (4C), evidence-grounding enforcement in the live path
  (4D), persistence + API + Kafka consumer (4E).
