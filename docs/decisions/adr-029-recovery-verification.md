# ADR-029: Recovery verification (Phase 5F)

- Status: Accepted
- Date: 2026-09-02

## Context

Phases 5A–5E take a remediation from an RCA recommendation to `EXECUTED` — mapped
deterministically, gated by an LLM-free policy engine, approved by an authorized
human, executed through a typed allow-listed executor, and recorded in an
append-only audit trail. But **`EXECUTED` only means the executor returned
success.** The `LocalSimulationExecutor` can happily "restart" a service whose
underlying problem a restart does not fix. Without a separate check, the platform
would claim victory it has not earned.

Phase 5F adds recovery verification: `EXECUTED → VERIFYING → RECOVERED |
RECOVERY_FAILED`, driven by an actual observation of the target's health.

Questions:

1. What does "verify recovery" mean, and what does it observe?
2. Deterministic — how, given a bounded wait for a service to become healthy?
3. Where does it sit in the lifecycle / transaction model?
4. How is it made safe to retry and concurrency-safe?
5. What must it *not* be able to do?

## Decision

### A `RecoveryVerifier` that only observes, against verifier-owned thresholds

`remediation_controller.recovery.RecoveryVerifier` is a `Protocol` with one
method:

```python
async def verify(self, *, target: ServiceTarget,
                 config: RecoveryVerificationConfig,
                 started_at: datetime) -> RecoveryOutcome
```

It runs a **bounded poll loop** against a
`remediation_controller.recovery.HealthProbe` and evaluates each observation
against its *own* thresholds (`max_error_rate`, `max_latency_p95_ms`,
`require_ready`, `min_replicas`) — it **never trusts the probe's self-reported
`status`** for the verdict, the same discipline as the policy engine ignoring
`proposal.risk_level` (ADR-025). Four checks: `service_running`, `error_rate`,
`latency_p95`, `readiness`. `RECOVERED` iff **every** check passes on some poll
within the window; `RECOVERY_FAILED` iff the window is exhausted. The verifier
has no `execute`, no command parameter, no infrastructure client, and no LLM.

### The health signal: a deterministic simulated recovery trajectory

The only Phase 5F probe is `SimulatedHealthProbe`, which reads the executor's
in-process `SimulationState`. `ServiceSimState` gained a health trajectory:
after a real (non-dry-run) execution the service is marked
`recovery_started_at = now` and converges to healthy exactly `recovery_delay`
later — **unless** it was injected with `chronic_fault` (the remediation did not
address the root cause) or a `recover_after` longer than the verification window
(slow recovery). A degraded service reports an elevated `error_rate` that decays
as it converges. This is a *simulation* of a health signal — honest about that —
and it makes RECOVERED and RECOVERY_FAILED both reachable and deterministic. A
real-infrastructure probe (HTTP `/health`, a metrics scrape) is a deliberate
future item behind the same `HealthProbe` Protocol.

### Determinism and termination: a virtual clock, a hard attempt bound

- `max_attempts = timeout_seconds // poll_interval_seconds + 1` — a fixed upper
  bound; the loop cannot run forever.
- health is evaluated at a **virtual clock** that starts at `started_at` and
  advances by `poll_interval_seconds` each iteration, so the result is a pure
  function of `(probe state, started_at, config)`.
- **the loop does not sleep.** In the local simulation there is no real
  dependency to wait on — the convergence is a function of virtual elapsed time,
  not wall time, so a real `sleep` would be theatre. An injectable `sleep`
  (default: no-op) lets a future real-infrastructure verifier pace itself
  without changing the verdict.
- a probe that raises is caught and recorded as a failed `probe_error` check —
  an untrusted target cannot break the loop or reach an execution path.

### Lifecycle: pre-existing 5A states, execution-style transaction boundaries

`EXECUTED → VERIFYING → RECOVERED | RECOVERY_FAILED` — all three are Phase 5A
states; 5F adds none. Two import-time assertions were added to the state machine:
`VERIFYING` is reachable **only** from `EXECUTED`, and the terminal recovery
verdicts **only** from `VERIFYING` — so there is no `APPROVED → RECOVERED` or
`EXECUTING → RECOVERED` shortcut.

`repo.begin_verification` opens one transaction holding `SELECT … FOR UPDATE` on
the remediation row, asserts `status == EXECUTED`, transitions to `VERIFYING`,
and inserts the `STARTED` `remediation_verifications` row (`UNIQUE(remediation_id)`).
The verifier then runs **between** transactions (mirrors ADR-027's executor —
keeps the lock short; a crash mid-verification leaves a visible `VERIFYING`, a
valid state, not a lie). `repo.finish_verification` re-locks, asserts `VERIFYING`,
writes the terminal status + evidence. Each transaction also appends its audit
event (`VERIFICATION_STARTED`; `VERIFICATION_SUCCEEDED` / `VERIFICATION_FAILED`)
in the same transaction as the state change — a committed verification transition
can never be missing its audit record (ADR-028).

### Idempotency + concurrency

`UNIQUE(remediation_id)` on `remediation_verifications` is the DB guarantee of
**at most one verification per remediation**. A repeat request once the
remediation is `RECOVERED` / `RECOVERY_FAILED` **replays** the stored result
unchanged (`replayed=True`, `200`) — never a second verification, never a
duplicate audit event. A repeat while `VERIFYING` conflicts (`409`). A racer that
passes the pre-lock check but loses hits the `UNIQUE` and its whole transaction —
including its audit event — rolls back. A PostgreSQL integration test fires 5
concurrent verifications and asserts exactly one produces a fresh result.

### Persistence

`remediation_verifications` (migration `0004`): `id` (`ver_…` PK),
`remediation_id` FK CASCADE `UNIQUE`, `execution_id`, `status` (CHECK
`STARTED|RECOVERED|RECOVERY_FAILED`), `verifier_type` (CHECK `DETERMINISTIC_LOCAL`
— closed), `verifier_version`, `attempts` (CHECK `>= 0`), `checks` JSON (the
structured evidence — redacted, inert), `failure_reason`, `timeout_seconds` /
`poll_interval_seconds` (config snapshot for reproducibility), `ver_metadata`
JSON, `started_at` / `completed_at`. Migration `0004` also adds a nullable
`verification_id` column to `remediation_audit_events` (additive, safe) and
widens the `event_type` CHECK to the three new values. **No command-shaped
column.** Single Alembic head (`0004`).

### Configuration

`RecoveryVerificationConfig` — the **safety thresholds** (`max_error_rate`,
`max_latency_p95_ms`, `require_ready`, `min_replicas`) are code-defined and
immutable at runtime (ADR-025 discipline: a "recovered" verdict must not be
weakenable by an environment variable). Only the two operational timing knobs
(`verification_timeout_seconds`, `verification_poll_interval_seconds`) are exposed
to `AppSettings` — they change *how long / how often* the verifier looks, never
*what passes*.

### Security boundary

The verifier is allowed to **observe**. It is not allowed to execute a command,
call `kubectl` / Docker / AWS / Terraform, SSH anywhere, modify infrastructure,
re-execute the remediation, or bypass approval. All telemetry / health responses
are **untrusted data**: a `HealthSnapshot.detail` of `"run kubectl delete pod…"`
is redacted and recorded for a human reader (in `failure_reason` / `checks`), and
is **never parsed or executed**. The `verify-recovery` request body has **no
fields** (`extra="forbid"` → `422` for any). An AST test asserts no `recovery/`
module imports `subprocess` / Docker / Kubernetes / cloud / SSH / HTTP clients or
calls `eval` / `exec`.

## Alternatives considered

- **Ask an LLM "does the service look healthy?"** Rejected outright — recovery
  verification is the deterministic counterweight; a probabilistic judge here
  would undermine the whole safety story.
- **Assume recovery immediately after `EXECUTED`.** Rejected — that makes 5F
  meaningless. There must be an actual observation.
- **A real HTTP call to `orders-service` `/health`.** Rejected for 5F — it would
  put an `httpx` import (a network client) into the controller, which the AST
  security test forbids, and it is not needed to demonstrate the architecture.
  The `HealthProbe` Protocol is ready for it in a later phase.
- **A background job / async scheduler for the poll loop.** Rejected — "do not
  over-engineer asynchronous scheduling". A bounded synchronous poll inside the
  request, with a virtual clock and a hard attempt cap, is simpler and fully
  deterministic.
- **Env-tunable safety thresholds.** Rejected — a "recovered" verdict must not be
  weakenable by configuration (ADR-025). Only timing is tunable.
- **A new `VERIFICATION_*` set of lifecycle states.** Unnecessary — Phase 5A
  already defines `VERIFYING`, `RECOVERED`, `RECOVERY_FAILED`.

## Consequences

- `docker compose up --build` still starts `remediation-migrate` (now `0001` +
  `0002` + `0003` + `0004`) and `remediation-controller` (`:8005`). No new
  container, no Kafka, no infrastructure dependency.
- `RemediationService` gains `verify_recovery()` and emits
  `remediation.recovery.verifications`, `remediation.recovery.verification_failures`,
  and `remediation.recovery.verification_duration`. No new observability subsystem.
- 5G adds Kafka remediation lifecycle events (including
  `remediation.recovered` / `remediation.recovery_failed`); 5H wires the full
  end-to-end chain.
- The end-to-end safety story is now complete through recovery: an AI
  recommendation is mapped deterministically, gated by an LLM-free policy engine,
  approved by an authorized human, executed through a typed allow-listed
  executor, audited immutably, **and independently verified to have actually
  worked** — with a failed recovery recorded as a failure, never a success.
