# ADR-030: Remediation lifecycle events on Kafka (Phase 5G)

- Status: Accepted
- Date: 2026-09-03

## Context

Phases 5A–5F built the remediation controller as a self-contained service:
an RCA recommendation is mapped deterministically to a closed-catalogue action
(5A), validated by an LLM-free policy engine (5B), approved by an authorized
human (5C), executed through the allow-listed `LocalSimulationExecutor` (5D),
recorded in an append-only audit trail (5E), and independently
recovery-verified (5F). Every lifecycle transition is already persisted in
PostgreSQL **in the same transaction** that writes the immutable
`remediation_audit_events` row.

What was missing: the rest of SentinelOps is event-driven (ADR-001), and the
remediation lifecycle was invisible on the Kafka backbone. ADR-028 explicitly
deferred a "Kafka mirror of the audit stream" to 5G, noting the audit model
already carries the fields such events would need.

Questions:

1. What is a remediation lifecycle event, and which facts get one?
2. Which topic, key, and partitioning strategy?
3. What is the publication consistency model — and is a transactional outbox
   justified here?
4. How are duplicates and process restarts handled?
5. Does 5G need a Kafka **consumer**?
6. How is Kafka prevented from becoming an execution channel?

## Decision

### Publisher only — no consumer

The remediation-controller **publishes** `remediation.events` and consumes no
topic. Nothing in Phase 5 needs to react to a remediation lifecycle event; the
end-to-end wiring (incident → RCA → remediation) is Phase 5H and connects
components through their existing HTTP interfaces, not through a new consumer.
Adding a consumer now would be speculative and would widen the attack surface
for no benefit. `remediation_controller.kafka` contains no `AIOKafkaConsumer`,
no handler, and no "envelope → action" translation — enforced by an AST test.

### One versioned event per committed lifecycle fact

`sentinelops_common.contracts.RemediationLifecycleV1` (`event_version = 1`) is
the single payload contract. The envelope `event_type` is one of a **closed set
of 11 values**, a 1:1 mirror of the auditable lifecycle facts (Phase 5E/5F)
minus the internal `EXECUTION_REQUESTED` note:

| `event_type` | audit event it mirrors | transition |
| --- | --- | --- |
| `remediation.proposed` | `PROPOSAL_CREATED` | – → `PROPOSED` |
| `remediation.policy_evaluated` | `POLICY_EVALUATED` | `POLICY_EVALUATION` → `PENDING_APPROVAL` \| `BLOCKED` |
| `remediation.blocked` | `REMEDIATION_BLOCKED` | `POLICY_EVALUATION` → `BLOCKED` |
| `remediation.approved` / `remediation.rejected` | `APPROVED` / `REJECTED` | `PENDING_APPROVAL` → `APPROVED` \| `REJECTED` |
| `remediation.execution_started` | `EXECUTION_STARTED` | `APPROVED` → `EXECUTING` |
| `remediation.execution_succeeded` / `remediation.execution_failed` | `EXECUTION_SUCCEEDED` / `EXECUTION_FAILED` | `EXECUTING` → `EXECUTED` \| `EXECUTION_FAILED` |
| `remediation.recovery_verification_started` | `VERIFICATION_STARTED` | `EXECUTED` → `VERIFYING` |
| `remediation.recovered` / `remediation.recovery_failed` | `VERIFICATION_SUCCEEDED` / `VERIFICATION_FAILED` | `VERIFYING` → `RECOVERED` \| `RECOVERY_FAILED` |

The payload carries **safe structured metadata only**: `remediation_id`,
`incident_id`, `investigation_id?`, `change`, `previous_state?` / `new_state?`,
`action_type?`, `target_service?` / `target_environment?`, `trigger?`,
`risk_level?`, `actor_type?` / `actor_id?` (redacted) / `actor_role?`,
`policy_outcome?` / `policy_version?` / `policy_reason_codes[]`,
`execution_id?` / `execution_result?`, `verification_id?` /
`verification_attempts?` / `checks_passed?` / `checks_total?` /
`failure_reason?` (redacted), a short redacted `reason`, the `audit_id` it
mirrors, `correlation_id?`, and `occurred_at`. There is **no field that can
hold a command, script, shell string, URL, or credential** — by construction,
mirroring the domain model. Events are built from the already-redacted audit
events and re-pass `redact_text` (idempotent) as defence in depth.

Versioning follows the platform rule (docs/architecture/events.md): additive
optional fields do not bump `event_version`; a breaking change does.

### Topic, key, partitioning

- **Topic:** `remediation.events` — follows the `<aggregate>.events` convention.
- **Key:** `remediation_id`. Every event for one remediation lands in the same
  partition and stays ordered (ADR-018 discipline).
- **Partitions / RF:** 1 locally (single-node KRaft). Created on startup via the
  shared `ensure_topics` helper when `KAFKA_AUTO_CREATE_TOPICS=true`; a real
  deployment provisions it out of band.

### Consistency model: application-level, best-effort, after commit

This is the **same model as `incident.events`** (ADR-016): the DB transaction —
which writes the state change *and* its immutable audit row together — commits
first; the lifecycle events are then mirrored onto Kafka best-effort. A publish
failure is counted (`remediation.events.publish_failures`), logged (ids only,
never payloads), and **never rolls back or fails the API call**.

Guarantee: a committed transition is always durably and immutably recorded in
`remediation_audit_events`. Limitation: a crash or broker outage between commit
and publish drops that event from Kafka. A consumer must therefore treat
`remediation.events` as an at-least-once best-effort notification and reconcile
against the Remediation API / audit trail — never as the source of truth.

**A transactional outbox was considered and deliberately not adopted.** It is a
heavier pattern, inconsistent with the rest of the platform's event streams,
and the blueprint warns against over-engineering 5G. The append-only audit
table already *is* a durable, ordered, gap-free log of exactly these facts — a
future phase can add a relay that scans unpublished `seq` values and publishes
them, with no change to the event model, if the best-effort guarantee ever
proves insufficient.

### Duplicates and restarts

The envelope `event_id` is `uuid5(namespace, audit_id)` — deterministic. The
same committed audit fact always produces the same `event_id`, so a consumer
keying on `event_id` (the documented idempotency key) deduplicates a republish
after a restart. Because the database and state machine remain authoritative and
every guard (`FOR UPDATE`, `UNIQUE(remediation_id)`, the single edge into
`EXECUTING`) is unchanged, a duplicate event has no unsafe effect even if a
consumer does process it twice. A dry-run persists nothing and therefore
publishes nothing. A replayed recovery verification returns the stored result
and emits no new event.

### Security boundary (unchanged, reinforced)

Kafka is an **observability / lifecycle** channel. The authoritative flow is
still: RCA recommendation → deterministic mapping → policy validation → human
approval → allow-listed action → `LocalSimulationExecutor` → audit → recovery
verification → lifecycle event. A `remediation.events` message is never read
back by this service and cannot become an instruction. No shell, subprocess,
`kubectl`, Docker, AWS, SSH, arbitrary URL, arbitrary executor, or arbitrary
action type is reachable from any Kafka path. `LocalSimulationExecutor` remains
the only executor and human approval remains mandatory (ADR-003).

### Observability

Three new instruments on the existing meter (no new subsystem):
`remediation.events.published` (by `event_type`),
`remediation.events.publish_failures` (by `event_type`),
`remediation.events.publish_latency` (histogram, s). `/ready` reports a
`kafka` field (`ok` / `degraded` / `disabled`) but does **not** gate readiness
on it — the approval workflow does not depend on Kafka.

### Docker Compose

`remediation-controller` gains `KAFKA_BOOTSTRAP_SERVERS`,
`KAFKA_REMEDIATION_TOPIC`, and `depends_on: kafka: service_healthy`. No new
container. `KAFKA_ENABLED=false` degrades cleanly to audit-trail-only.

## Alternatives considered

- **A Kafka consumer that turns an RCA/incident event into a remediation
  proposal.** Rejected for 5G — speculative, widens the trust boundary, and 5H
  connects the chain through HTTP interfaces. Can be added later behind the same
  safety guards if an event-driven trigger is wanted.
- **Transactional outbox / CDC.** Rejected as over-engineering for 5G; the audit
  table is already the durable log a relay would need (see above).
- **Publish inside the DB transaction (dual write).** Rejected — a Kafka send is
  not transactional with PostgreSQL; this is the exact anti-pattern ADR-028
  called out.
- **One generic `remediation.lifecycle` event with a `type` field in the
  payload only.** Rejected — distinct `event_type` values let a consumer
  subscribe/filter at the envelope level, matching `incident.opened` /
  `incident.updated` / `incident.resolved`.
- **Emit `EXECUTION_REQUESTED` too.** Rejected — it is an internal pre-check
  note with no state change; publishing it would be "every function call", which
  the blueprint warns against.

## Consequences

- `docker compose up --build` now has the remediation-controller publishing
  `remediation.events` after each committed transition; it still consumes
  nothing and touches no real infrastructure.
- Phase 5H can build a deterministic end-to-end demonstration and, if desired, a
  consumer, on top of a stable event contract.
- The end-to-end safety story is now observable on the event backbone without
  weakening any Phase 5A–5F guarantee.
- 5H (end-to-end integration) and 5I (final hardening + docs) remain.
