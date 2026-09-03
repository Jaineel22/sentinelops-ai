# ADR-024: Phase 5 remediation domain — closed action catalogue, structural no-command guarantee

- Status: Accepted
- Date: 2026-09-02

## Context

Phase 5 introduces remediation. ADR-003 already fixed the shape:
AI recommendation → policy validation → human approval → allow-listed action →
execution → audit → recovery verification, and stated that "earlier phases must
not add any action-executing code". Phase 4 (ADR-019) made the Phase 4/5
boundary *structural*: the rca-agent's `RecommendedAction.action_type` is a
closed enum, `requires_human_approval` is `Literal[True]`, and there is no
executor and no command field anywhere in that service.

Sub-phase 5A builds the domain foundation on which the rest of Phase 5 sits. The
questions to settle before any 5B+ code:

1. What is the set of things the controller can execute, and how is it defined?
2. How does an AI-generated recommendation become a Phase 5 object without a
   free-text string ever becoming an executable command?
3. Where does approval get enforced — a database flag, or the type system?
4. What is the remediation lifecycle, and how are illegal transitions rejected?

## Decision

### A separate service, `services/remediation-controller`

Package `remediation_controller`, same shape as the other services (config,
domain, state machine, `InMemory*`/SQLite tests, its own Alembic lineage in
later sub-phases). Not folded into `rca-agent`: the rca-agent must never gain a
write path. 5A is domain-only — no `app.py`, no `__main__.py`, no Dockerfile
yet.

### The executable action set is a closed enum with a closed, code-defined catalogue

`RemediationActionType` has four members: `RESTART_SERVICE`, `SCALE_SERVICE`,
`ROLL_BACK_DEPLOYMENT`, `DISABLE_FEATURE_FLAG`. There is deliberately **no**
`EXECUTE_COMMAND` / `RUN_SHELL` / `KUBECTL` / `ARBITRARY_SCRIPT` / `CUSTOM`
member.

`ACTION_CATALOGUE` is a `dict` literal wrapped in `MappingProxyType`: one
`ActionDefinition` per action type, total and closed (asserted at import). Each
definition carries `requires_approval: Literal[True]`, an `allowed_target_types`
/ `allowed_target_services` / `allowed_severities` set, a `risk_level`, an
`executor_type` (`LOCAL_SIMULATION` — the only one that exists), blast-radius /
timeout / cooldown bounds, and a tuple of `ActionParameter`s. No environment
variable, settings object, or input can add or mutate an entry.

An action has a *type* plus a small set of **bounded, named** parameters. Every
string parameter has an explicit `pattern` or `allowed_values` — there is no
unbounded free-text parameter. `validate_action_parameters` fails closed on an
unknown action, an unknown key, a missing required parameter, a wrong type, an
out-of-range int, or a pattern miss.

### The target is structured and allow-listed; both fail closed

`ServiceTarget` is `{target_type, service_name, environment}` with
`service_name` constrained to a strict slug (`^[a-z][a-z0-9-]{1,62}$`) — a shell
fragment, path, or URL cannot pass. `ALLOWED_TARGET_SERVICES = {"orders-service"}`
(the only instrumented service). `resolve_target` and `is_allowed_target` reject
anything else.

### `RemediationProposal` is the type-system enforcement point

Frozen Pydantic, `extra="forbid"`, `requires_approval: Literal[True]`. There is
no `command` / `script` / `shell` / `kubectl_command` field and a caller cannot
add one. A `model_validator` re-checks catalogue membership, target eligibility,
parameter schema, and expiry at construction — an invalid action, target, or
parameter set cannot produce a proposal. The RCA's free text lands only in the
`reason` prose field, which nothing parses.

### `proposal_from_rca` is the single, deterministic, fail-closed mapping

It consumes `RcaRecommendedActionInput` — a re-declared slice of the Phase 4
`RecommendedAction` (`extra="ignore"`, `action_type` typed as a bounded `str`,
not the Phase 4 enum, so an unknown label misses rather than raising). This
mirrors how Phase 4 re-declares the Incident API payloads instead of importing
`incident_correlator` (ADR-020) — no cross-service runtime dependency, and the
mapping is naturally fail-closed against a new/unknown Phase 4 category.

A recommendation becomes a `RemediationProposal` **only** when its category is in
`_RCA_ACTION_MAP` (`RESTART_SERVICE`, `ROLL_BACK_DEPLOYMENT`), the target is
allow-listed, the severity is eligible, and all required parameters can be
satisfied without a human. Every other case — an unmapped category, a category
needing operator input (`SCALE_SERVICE`), a missing/blocked target, an
ineligible severity, an unknown or adversarial label — returns a terminal
`BlockedProposal`. The function never raises on hostile input.

### The lifecycle state machine is explicit adjacency, fail closed

Same pattern as `incident_correlator.state_machine` /
`rca_agent.state_machine`. `EXECUTING` is reachable only from `APPROVED`;
`APPROVED` only from `PENDING_APPROVAL`. `validate_transition` raises
`InvalidRemediationTransition` for anything not in the adjacency table
(`PROPOSED → EXECUTING`, `REJECTED → EXECUTING`, `EXECUTED → EXECUTING`,
`RECOVERED → APPROVED`, …). An `authorize_execution(proposal, approval)` guard
additionally requires an `APPROVED` proposal plus a matching, affirmative
`RemediationApproval`.

### No executor in 5A

No `subprocess`, `os.system`, Docker/Kubernetes client, SSH, cloud SDK, or
HTTP-to-infrastructure anywhere in the package. Execution is Sub-phase 5D and
will be `LocalSimulationExecutor` first.

## Alternatives considered

- **A generic "action" with a command template.** Rejected — reintroduces the
  free-form escape hatch ADR-003 exists to prevent.
- **`requires_approval: bool = True`.** Rejected — a future caller could pass
  `False`. `Literal[True]` makes it structurally impossible.
- **Config-driven catalogue (YAML / env).** Rejected — an attacker who can
  influence configuration could add an executable action. Code-defined and
  `MappingProxyType`-frozen instead.
- **Import `rca_agent`'s `RecommendedAction` / `RecommendedActionType`
  directly.** Rejected — couples the services and pulls the heavy `rca` extra
  (langgraph, anthropic) into the remediation dependency set; a re-declared
  input contract is the established pattern (ADR-020) and is fail-closed by
  design.
- **Map every Phase 4 category to *some* action.** Rejected — `ADJUST_CONFIGURATION`
  / `FAILOVER_DEPENDENCY` have no safe bounded representation; forcing a mapping
  would be exactly the "silently becomes executable" failure the spec forbids.
- **One database flag for approval.** Rejected as the *only* mechanism — the
  type system and the state machine carry the guarantee; persistence (5C) records
  it.

## Consequences

- The catalogue is closed for Phase 5. Adding an action is a code change + an
  ADR note, reviewed like any other.
- `SCALE_SERVICE` / `DISABLE_FEATURE_FLAG` exist in the catalogue but have no RCA
  auto-mapping; a later sub-phase adds an operator-initiated (`MANUAL`) proposal
  path that supplies their parameters.
- Later sub-phases build on this: the deterministic policy engine (5B), the
  PostgreSQL persistence + approval workflow + API (5C), the
  `LocalSimulationExecutor` + dry-run (5D), the audit trail (5E), recovery
  verification (5F), Kafka + Compose (5G).
- The Phase 4/5 boundary is unchanged and now has a Phase 5 counterpart: an AI
  recommendation can, at worst, produce a `BlockedProposal` or an exact
  allow-listed structured action awaiting human approval — never a command.
