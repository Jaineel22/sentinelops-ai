# ADR-025: Deterministic remediation policy engine (Phase 5B)

- Status: Accepted
- Date: 2026-09-02

## Context

Phase 5A (ADR-024) produces a `RemediationProposal` — typed intent derived from a
Phase 4 RCA recommendation. Before that proposal can be shown to a human for
approval, the platform must independently decide whether it is *eligible*.
ADR-003 fixed the pipeline: AI recommendation → **policy validation** → human
approval → allow-listed execution.

Questions for 5B:

1. Deterministic rules, or a model-assisted risk judgement?
2. What does the policy layer take as input, and what does it output?
3. Cooldown / duplicate detection needs prior-remediation state — but 5C owns
   persistence. How is that dependency expressed now?
4. Can policy validation ever move a proposal toward execution?

## Decision

### Fully deterministic; no LLM, no I/O

`remediation_controller.policy` imports no model SDK, opens no socket, and reads
no free-text field on the proposal (`reason`, `expected_effect`,
`source_recommendation`). The RCA agent's own `risk_level` on the proposal is
**ignored** — risk and blast radius come from the catalogue `ActionDefinition`.
Identical `(proposal, context, config)` always yields an identical
`PolicyDecision` (its `evaluated_at` is taken from `context.now`, not the wall
clock). This is the deterministic counterweight to a probabilistic recommender:
the two must not share a failure mode.

### `PolicyEngine.evaluate(proposal, context) -> PolicyDecision`

A stateless engine constructed with an immutable `PolicyConfig`. It runs a
**fixed, ordered set of 9 rules** and always runs **all** of them, aggregating
every `PolicyViolation` so multiple failures are reported together and
deterministically (`reason_codes` sorted by value).

Rules: state · action eligibility · target eligibility · environment · severity ·
parameters · risk/blast-radius · expiry · cooldown/duplicate.

`PolicyDecision` is a frozen, serializable record: `outcome` (`ALLOW` / `DENY`),
`reason_codes`, `violations` (code + rule + short operational `detail` — never an
LLM sentence, never chain-of-thought), `policy_version`, `evaluated_rules`,
`evaluated_at`.

### `PolicyContext` carries what the proposal must not be trusted for

The proposal does not carry incident severity or remediation history. The caller
supplies them via `PolicyContext`: `now`, `incident_severity` (the value the
caller verified against the Incident API — `None` means "unverified" and **fails
closed**), and `history` (a `RemediationHistoryPort`).

### `RemediationHistoryPort` — an injectable Protocol, not a database

Cooldown and duplicate detection need prior-remediation state that only 5C's
PostgreSQL schema will hold. 5B defines the read-only `Protocol`
(`active_remediation_exists`, `last_completed_at`) and ships **only**
`NullRemediationHistory` — a null object that reports "nothing known", so the
cooldown rule simply never fires until a real adapter exists. This is not a fake
persistence implementation; it is the honest default. 5C provides the
PostgreSQL-backed adapter behind the same Protocol.

### Policy validation can never create execution authority

A passing (`ALLOW`) decision always includes the `APPROVAL_REQUIRED` reason code.
The only lifecycle helper, `apply_policy_decision`, uses the **Phase 5A** state
machine and can land a proposal only on `PENDING_APPROVAL` (via
`POLICY_EVALUATION`) or `BLOCKED`. It asserts the result is never `APPROVED`,
`EXECUTING`, `EXECUTED`, `VERIFYING`, or `RECOVERED`, and refuses to act on a
proposal that is not in a policy-input state.

### `policy_version = "1"`

A single string constant, recorded on every decision, bumped on any rule or
threshold change. No version-negotiation machinery (spec: do not over-engineer).

## Alternatives considered

- **Model-assisted risk scoring.** Rejected — reintroduces the probabilistic
  failure mode policy exists to contain; not reproducible or auditable.
- **Trust `proposal.risk_level`.** Rejected — for an RCA-derived proposal it is
  catalogue-sourced already, but a future `MANUAL` proposal could set it freely;
  policy must derive risk itself.
- **Short-circuit on the first violation.** Rejected — the spec (and good UX for
  the approver) wants every reason a proposal was blocked, deterministically.
- **Build a minimal in-memory history store now.** Rejected — that is fake
  persistence; a `Protocol` + null object is the correct seam for 5C.
- **Let policy move a proposal to `APPROVED`.** Rejected — that would collapse the
  policy gate and the human gate into one. Policy only ever reaches
  `PENDING_APPROVAL`.
- **Allow all environments.** Rejected — 5B restricts to `development` (the only
  environment with an instrumented service and no real executor); widening it is
  a deliberate future policy-config change.

## Consequences

- The policy engine is a pure function of `(proposal, context, config)` — trivial
  to unit-test exhaustively and to reason about.
- 5C must implement a `RemediationHistoryPort` adapter and decide where
  `PolicyDecision`s and the `apply_policy_decision` transition are persisted +
  audited (5E).
- Enabling staging/production, per-role authorization, or a new rule is a
  `PolicyConfig` / `rules.py` change plus a `policy_version` bump — visible and
  reviewable.
- The Phase 4/5 boundary is reinforced: an AI recommendation now passes through a
  deterministic, LLM-free gate before a human ever sees it, and that gate can
  never itself authorize execution.
