# ADR-026: Remediation persistence + human approval workflow (Phase 5C)

- Status: Accepted
- Date: 2026-09-02

## Context

Phase 5A (ADR-024) built the remediation domain; Phase 5B (ADR-025) built the
deterministic policy engine. Both are pure — no persistence, no API. Phase 5C
makes `services/remediation-controller` a running service that can persist a
remediation proposal, run policy, and let a human APPROVE or REJECT it through
an HTTP API. Execution is still Phase 5D — **APPROVED ≠ EXECUTED**.

Questions:

1. Where do remediations live, and how are migrations managed?
2. What is persisted for policy traceability?
3. How does the (async) repository satisfy the (sync) Phase 5B
   `RemediationHistoryPort`?
4. What is the approval authorization model, given there is no IdP?
5. How is concurrent approval made safe?
6. What does the API accept — and how is "no command field" preserved end to end?

## Decision

### Shares the `sentinelops` database; own Alembic lineage

Two tables (`remediations`, `remediation_approvals`) in the shared PostgreSQL
database, migrated by the remediation-controller's own Alembic history with a
dedicated version table `alembic_version_remediation` — exactly the ADR-019
pattern the rca-agent uses (`alembic_version_rca`). No new database, no new
container beyond the service + its migrate one-shot. SQLAlchemy async + a
~40-line `Database` wrapper copied from the other services; `InMemory*` +
SQLite-file repositories for fast tests, PostgreSQL under `-m integration`.

### The persisted record = proposal + policy decision (+ approval)

`RemediationRecord` is `RemediationProposal` (the pure 5A intent object,
unchanged) + the full `PolicyDecision` + an optional immutable
`RemediationApproval`. The `remediations` row stores every proposal field plus
`policy_outcome` / `policy_version` / `policy_reason_codes` / the complete
`policy_decision` JSON / `policy_evaluated_at` — enough to reconstruct *why* a
remediation is `PENDING_APPROVAL` or `BLOCKED` without re-running policy. There
is **no column that can hold a command / script / shell string** — the 5A model
has no such field, so neither does the table.

### `RemediationHistorySnapshot` bridges async repo → sync policy

ADR-025 kept `RemediationHistoryPort` and `PolicyEngine.evaluate` synchronous.
Rather than make the whole policy layer async, the service pre-loads a frozen
`RemediationHistorySnapshot` (one async query, scoped to one
`(incident, action, target)`) that *implements* the sync port, and hands that to
`PolicyContext`. 5B is untouched.

### Demo authorization: deterministic role → catalogue-risk matrix

`can_approve(role, action_type)` checks the action's **catalogue** `risk_level`
(never `proposal.risk_level`, never free text — same principle as the policy
engine) against a code-defined, `MappingProxyType`-frozen matrix: `OPERATOR` ≤
LOW, `INCIDENT_RESPONDER` ≤ MEDIUM, `ADMINISTRATOR` ≤ HIGH. Any role may REJECT.
Approver identity is supplied by the API request and only structurally validated
(non-empty). **This is explicitly a demo identity model, not authentication** —
the interface (identity + role on every decision) is shaped for a real IdP later.

### Concurrency: pessimistic row lock + a unique constraint

`record_decision` opens one transaction, takes `SELECT … FOR UPDATE` on the
remediation row, re-checks "already decided" *inside* the lock, writes the status
transition and the approval row, and commits. Two racing approves: one wins, the
other sees the existing decision and gets `409`. `UNIQUE(remediation_id)` on
`remediation_approvals` is the database-level backstop. The approval row is
INSERT-only — there is no update path, so a decision is immutable.

### The API is RCA-recommendation-shaped and `extra="forbid"`

`POST /remediations` takes `{incident_id, investigation_id?, incident_severity?,
target_environment, recommended_action: {action_type, target_service,
description, rationale, evidence_ids}}`. `action_type` is a bounded *label*, not
the executable enum. The handler runs `proposal_from_rca` → `PolicyEngine` →
`apply_policy_decision` → persist. A recommendation that cannot map to a
catalogue action/target is a `422` and nothing is persisted; a policy denial is
a persisted `BLOCKED` remediation (traceability). Every request model is
`extra="forbid"` so `{"command": …}` / `{"script": …}` / an arbitrary executor
parameter is a `422`. Responses are explicit flat models — SQLAlchemy rows are
never exposed. `decide()` transitions only to `APPROVED` / `REJECTED` via the 5A
state machine; there is no code path from the API to `EXECUTING`.

### `incident_severity` is caller-supplied in 5C

The API request carries the verified incident severity (5H will fetch it from
the Incident API). Absent severity fails closed in the policy engine (DENY →
persisted `BLOCKED`). Documented as a 5C limitation alongside the demo identity
model.

## Alternatives considered

- **A separate database / instance for remediation.** Rejected — unnecessary
  infrastructure; per-service table ownership + migration lineage is enough
  (ADR-019).
- **Make `RemediationHistoryPort` async.** Rejected — cascades into making every
  policy rule and `PolicyEngine.evaluate` async, for a lookup the caller can do
  once up front. The snapshot is the smaller change and keeps 5B pure.
- **Optimistic concurrency (version column + retry).** Rejected — the workload is
  low-volume and a human-in-the-loop; a `FOR UPDATE` lock held for one short
  transaction is simpler and matches the Phase 3 pattern.
- **Trust `proposal.risk_level` for authorization.** Rejected — same reason as
  ADR-025: an upstream mapping (or a future `MANUAL` proposal) could set it
  freely.
- **A full authentication layer now.** Rejected — out of scope; the demo identity
  model is clearly labelled and the interface is ready for an IdP.
- **Persist the RCA-blocked case as a `BLOCKED` row.** Rejected — a recommendation
  that never became a valid proposal is a client error (`422`), not a remediation
  worth a row. Only proposals that reached the policy engine are persisted.

## Consequences

- `docker compose up --build` now also starts `remediation-migrate` (one-shot)
  and `remediation-controller` (`:8005`). No Kafka, no executor.
- 5D builds its `LocalSimulationExecutor` on top of an `APPROVED` remediation +
  `authorize_execution`; 5E adds a dedicated append-only audit table (5C's
  `policy_decision` JSON + immutable approval row are the interim record); 5G
  adds Kafka lifecycle events.
- The Phase 4/5 boundary holds and is now demonstrable end to end: an AI
  recommendation is mapped deterministically, gated by an LLM-free policy engine,
  persisted, and can only change a running system after an explicit, authorized,
  immutable human approval — and even then, 5C stores the decision and stops.
