# ADR-003: Human approval required for remediation

- Status: Accepted
- Date: 2026-08-31

## Context

The agent will recommend actions that change a running production system
(restart, scale, roll back, disable a feature flag). LLM-driven reasoning is
probabilistic and can be confidently wrong or manipulated by malicious telemetry
(prompt injection via logs). Unattended remediation could turn a small incident
into an outage and would be unacceptable in most real organisations.

## Decision

Remediation is **human-in-the-loop** and **allow-listed**:

```
AI recommendation
  → policy validation (is this action permitted for this service/severity?)
  → human approval (explicit decision, recorded with identity + reason)
  → allow-listed action (only pre-defined, parameterised actions can run)
  → execution
  → audit log
  → recovery verification
```

The agent can never execute an arbitrary command. The set of executable actions
is a fixed catalogue, each with its own policy and blast-radius limits.

## Alternatives considered

- **Fully autonomous remediation.** Fastest MTTR in theory; unacceptable risk
  and no accountability. Rejected.
- **Autonomous for "low-risk" actions only.** Still requires a trustworthy
  risk classifier and invites scope creep; the safety story is much harder to
  defend. Deferred indefinitely.
- **Approval but with free-form actions.** Removes the allow-list guarantee;
  a rushed approver could authorise something dangerous. Rejected.

## Consequences

- MTTR includes human response time; the value proposition is faster, better
  *analysis*, not zero-touch fixes.
- We must build: a policy layer, an approval UI/workflow, an action catalogue,
  and an audit store.
- Strong, defensible security and accountability posture.
- Implemented in Phase 5; earlier phases must not add any action-executing code.
