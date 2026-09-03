# ADR-027: Allow-listed executor abstraction + LocalSimulationExecutor (Phase 5D)

- Status: Accepted
- Date: 2026-09-02

## Context

Phases 5A–5C produce an `APPROVED` remediation: a typed `RemediationProposal`
(closed action, allow-listed target, catalogue-bounded parameters), gated by a
deterministic LLM-free policy engine (ADR-025) and an explicit, immutable,
authorized human approval (ADR-026). ADR-003 says an approved remediation may
then *execute* — but "the agent can never execute an arbitrary command" and
"only pre-defined allow-listed actions can ever be executed".

Phase 5D adds execution. Questions:

1. What is the shape of the executor boundary, and what does an executor receive?
2. What does the first (and only) executor do — and what does it explicitly *not*
   do?
3. How is execution made idempotent and concurrency-safe?
4. How does dry-run relate to real execution?
5. What is persisted, given the append-only audit trail is Phase 5E?

## Decision

### A tiny executor Protocol that receives a typed proposal — never a string

`remediation_controller.executor.Executor` is a `Protocol` with one method:

```python
def execute(self, proposal: RemediationProposal, *, execution_id: str,
            dry_run: bool, now: datetime) -> ExecutionResult
```

It receives the **already-validated stored proposal**. There is no `command`,
`script`, `shell`, or executor-selector parameter anywhere in the module. The
executor does **not** check approval/authorization and does **not** transition
lifecycle state — the service owns both (`_check_executable` +
`authorize_execution` from ADR-024, and the Phase 5A state machine). An executor
that could decide its own authorization would defeat the boundary.

### `LocalSimulationExecutor` is the only executor — a local simulation, not infrastructure

It mutates a small **in-process** `SimulationState` (per service: `running`,
`replicas`, `deployment_revision`, `restart_count`, `feature_flags`). It never
runs a `subprocess`, opens a socket, or imports a Docker / Kubernetes / cloud /
SSH client — enforced by an AST-based test over every module in the package. The
four simulated actions are exactly the four catalogue actions; an import-time
assertion keeps the executor total against `RemediationActionType`.

The executor registry is `{ExecutorType.LOCAL_SIMULATION: LocalSimulationExecutor}`
wrapped in `MappingProxyType`, asserted total against the enum. `build_executor`
fails closed. **No configuration-driven class loading, no `EXECUTOR_CLASS`
import path, no plugin mechanism** — an API client cannot select an executor.

Real infrastructure execution (a Kubernetes / cloud executor behind the same
Protocol) is a deliberate future item and is intentionally not built or
enumerated.

### The execution lifecycle uses only pre-existing Phase 5A states

`APPROVED → EXECUTING → EXECUTED | EXECUTION_FAILED` — all three are Phase 5A
states; 5D adds none. Every hop goes through `validate_transition`. A genuine
executor failure (`ExecutorError`) produces `EXECUTION_FAILED` + a `FAILED`
`ExecutionResult`; it **never** becomes `EXECUTED`, and the service does not
raise — the request succeeds, the execution is recorded as failed.

### Idempotency + concurrency: `FOR UPDATE` + `UNIQUE(remediation_id)`

`repo.begin_execution` opens one transaction holding `SELECT … FOR UPDATE` on the
remediation row, asserts `status == APPROVED`, transitions to `EXECUTING`, and
inserts the `STARTED` `remediation_executions` row. `UNIQUE(remediation_id)` on
that table is the DB-level guarantee of **at most one real execution per
remediation**. A racer that arrives after the first commit sees `EXECUTING` (not
`APPROVED`) and gets `RemediationExecutionConflictError` → `409`. A racer that
somehow passes the status check hits the `UNIQUE`. `finish_execution` re-locks,
asserts `EXECUTING`, and writes the terminal status + result. A PostgreSQL
integration test fires 5 concurrent executes and asserts exactly one wins.

### Dry-run: same guards, same interface, zero side effects

`execute(id, dry_run=True)` runs the identical `_check_executable` guards (an
unapproved / expired / policy-blocked remediation fails a dry-run exactly as a
real one does) and the identical `Executor.execute` call, then returns an
`ExecutionResult` with `dry_run=True` and a `"[DRY RUN] would …"` effect. It
**persists nothing** (no `remediation_executions` row — the table CHECKs
`dry_run = false`), transitions no state, and mutates no `SimulationState` (the
executor computes the effect against a throwaway `SimulationState.copy()`). The
remediation stays `APPROVED`.

### Persistence is minimal; the immutable audit trail is Phase 5E

One `remediation_executions` row per remediation, INSERTed once (`STARTED`) and
UPDATEd once to its terminal status. That is enough to make the execution
observable and to enforce single-execution — it is **not** the append-only audit
system (5E). The `remediations` row + `policy_decision` JSON + immutable approval
row + this execution row are the interim traceability record. `RemediationRecord`
gains one additive optional field (`execution`); no 5A/5B/5C behaviour changes.

## Alternatives considered

- **A generic `Executor` that takes an action string / command template.**
  Rejected — reinstates the free-form escape hatch ADR-003 exists to prevent.
- **Run the executor inside the `begin_execution` transaction (one hop).**
  Rejected — mirrors Phase 4's "the graph runs *between* transactions": keeps the
  lock short and the sim-state mutation outside the DB transaction. A crash
  mid-execution leaves a visible `EXECUTING` (a valid failure state), not a lie.
- **Persist dry-runs as `dry_run=true` execution rows.** Rejected — a dry-run is a
  pure read-only preview; persisting it is audit-shaped work that belongs to 5E,
  and it complicates the single-execution `UNIQUE`.
- **A real Docker/Kubernetes target for the "simulation".** Rejected explicitly —
  Phase 5D proves the *architecture*, safely. Real infrastructure is out of
  scope.
- **Optimistic concurrency (version column + retry) for execution.** Rejected —
  same reasoning as ADR-026: a short `FOR UPDATE` transaction is simpler and
  matches the Phase 3/5C pattern.
- **Add an `EXECUTION_FAILED`-style new state.** Unnecessary — Phase 5A already
  defines `EXECUTING`, `EXECUTED`, and `EXECUTION_FAILED`.

## Consequences

- `docker compose up --build` still starts `remediation-migrate` (now `0001` +
  `0002`) and `remediation-controller` (`:8005`). No new container, no Kafka, no
  infrastructure dependency.
- 5E builds the append-only audit trail; 5F adds recovery verification
  (`EXECUTED → VERIFYING → RECOVERED | RECOVERY_FAILED`, states that already
  exist); 5G adds Kafka lifecycle events.
- The end-to-end safety story is now demonstrable: an AI recommendation is
  mapped deterministically, gated by an LLM-free policy engine, approved by an
  authorized human, and executed **only** through a typed, allow-listed executor
  that touches nothing real — and a failure is recorded as a failure, never a
  success.
