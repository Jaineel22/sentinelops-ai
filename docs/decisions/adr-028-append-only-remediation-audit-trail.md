# ADR-028: Append-only remediation audit trail (Phase 5E)

- Status: Accepted
- Date: 2026-09-02

## Context

Phases 5A–5D produce a remediation that is mapped deterministically from an RCA
recommendation, gated by an LLM-free policy engine, approved by an authorized
human, and executed through a typed allow-listed executor. Until now the
"traceability record" was the mutable `remediations` row plus the `policy_decision`
JSON, the immutable `remediation_approvals` row, and the single
`remediation_executions` row (ADR-026, ADR-027 both explicitly deferred a proper
audit trail to 5E).

That is not defensible to an auditor. `remediations.status` is overwritten on
every transition, so the row cannot answer "*when* did this move to `APPROVED`,
and from what?" or "was execution ever requested and then rolled back?". Phase 5E
adds a dedicated, immutable, chronological record of the remediation lifecycle.

Questions:

1. What is an audit event, and what does it record?
2. Append-only — enforced how, at which layers?
3. How is an audit event kept consistent with the state change it describes?
4. How is the trail prevented from becoming a secret store?
5. What is the read interface?

## Decision

### One immutable event per committed lifecycle fact

`remediation_controller.audit.RemediationAuditEvent` (frozen, `extra="forbid"`)
records: the remediation / incident / investigation ids; a closed
`AuditEventType`; the actor (`SYSTEM` for the deterministic mapping / policy
engine / executor, `HUMAN` for an approver, with the role the authorization
check used); the `previous_state` → `new_state` transition; the action + target;
the policy `outcome` / `version` / reason codes; the execution id / mode /
result; a short redacted `reason`; a caller `correlation_id`; and a bounded
scalar-only `metadata` map. `occurred_at` is a human-facing timestamp; the
database `BIGINT` identity `seq` is the authoritative total order.

The nine event types are 1:1 with meaningful **committed** facts —
`PROPOSAL_CREATED`, `POLICY_EVALUATED`, `REMEDIATION_BLOCKED`, `APPROVED`,
`REJECTED`, `EXECUTION_REQUESTED`, `EXECUTION_STARTED`, `EXECUTION_SUCCEEDED`,
`EXECUTION_FAILED`. No synthetic event is minted to pad the trail. A rejected API
call that changes no state (an already-decided approval, an execution conflict, a
404) is an application-log / metric concern, not an audit fact. A **dry-run**
writes nothing (ADR-027: it is a read-only preview). Recovery-verification events
are Phase 5F and are deliberately absent; the model already carries the fields
5F needs, so no schema change will be required.

### Append-only, enforced at four layers

1. **No mutating API.** There is no `POST` / `PUT` / `PATCH` / `DELETE` route for
   audit events. The only endpoint is `GET /remediations/{id}/audit`.
2. **No repository mutation path.** `RemediationRepository` has
   `list_audit_events` (read) and an `audit_events` parameter on the four
   state-changing methods (append). There is no update / delete / delete-by-id.
3. **The application appends its own legitimate lifecycle events** — the service
   builds them from validated domain objects (`audit.builders`, the only event
   factory) and hands them to the repository.
4. **PostgreSQL backstop.** Migration `0003` installs a
   `BEFORE UPDATE OR DELETE` trigger on `remediation_audit_events` that raises.
   Even a direct `UPDATE`/`DELETE` by the application's own database role is
   rejected. (SQLite, used only for fast tests, has no equivalent; layers 1–3
   still hold there.)

### Transactional consistency: the audit event commits with the state change

The audit event is inserted in the **same transaction** as the transition it
records. `repo.create` writes the `remediations` row and the
`PROPOSAL_CREATED` + `POLICY_EVALUATED` (+ `REMEDIATION_BLOCKED`) events together;
`repo.record_decision` writes the immutable approval row and its `APPROVED` /
`REJECTED` event together, under the same `SELECT … FOR UPDATE` row lock that
already makes concurrent approval safe; `repo.begin_execution` writes the
`EXECUTING` transition, the `STARTED` execution row, and the
`EXECUTION_REQUESTED` + `EXECUTION_STARTED` events together; `repo.finish_execution`
writes the terminal transition, the execution result, and the
`EXECUTION_SUCCEEDED` / `EXECUTION_FAILED` event together.

Consequences: a committed transition can never be missing its audit record, and
the loser of a concurrent-approval race rolls back its *entire* transaction —
including its audit event — so the trail never shows two mutually exclusive
decisions. The unreliable "commit the state change, then best-effort insert the
audit row" pattern is explicitly not used. Execution still runs *between*
transactions (ADR-027) — a crash mid-execution leaves `EXECUTING` plus its
`EXECUTION_REQUESTED` / `EXECUTION_STARTED` events, a truthful "in progress"
record, never a lie.

### Secret / sensitive-data redaction boundary

`remediation_controller.audit.redaction` is applied to every free-text and
structured value before an event is built. Two deterministic filters:
key-based (a metadata key that looks like a credential → value replaced with
`[REDACTED]`) and value-based (a substring matching a known credential shape — a
PEM private-key block, an AWS access-key id, a GitHub / Slack token, a JWT, a
`Bearer` header, a Google API key — replaced wherever it appears). Values are
length-capped; metadata is scalar-only and key-count-capped. The remediation
domain has no field that can hold a command or an arbitrary secret (closed
catalogue, pattern-bounded parameters), so this is defence in depth — but it is a
hard, tested boundary. An adversarial approver `reason` or a prompt-injected RCA
`rationale` that quotes a token lands in the trail with the token redacted.

### Read API

`GET /remediations/{id}/audit` — chronological (oldest first, ordered by `seq`),
`limit` (1–500, default 100) + `offset` pagination, `404` for an unknown
remediation, authorization identical to the other remediation reads (the 5C demo
model). Flat `AuditEventView` response; the SQLAlchemy row is never exposed and
carries no command-shaped field. A caller `x-request-id` header is echoed onto
every event of that request as `correlation_id` (informational only — never
trusted for a decision).

### Schema

`remediation_audit_events` (migration `0003`, same `alembic_version_remediation`
lineage): `seq` `BIGINT` identity PK, `audit_id` (`aud_…`, `UNIQUE`),
`remediation_id` FK→`remediations` CASCADE, `incident_id`, `investigation_id?`,
`event_type` / `actor_type` (CHECK), `actor_id` (CHECK non-empty), `actor_role?`,
`previous_state?` / `new_state?`, `action_type?` / `target_service?` /
`target_environment?`, `policy_outcome?` / `policy_version?` /
`policy_reason_codes` JSON, `execution_id?` / `execution_mode?` (CHECK) /
`execution_result?`, `reason`, `correlation_id?`, `event_metadata` JSON,
`occurred_at`, `recorded_at`. Indexes: `(remediation_id, seq)`,
`(incident_id, seq)`, `execution_id`, `occurred_at`. **No command-shaped column.**

## Alternatives considered

- **Reuse `incident_state_history`'s shape (`from_status`, `to_status`, `actor`,
  `reason`).** Too thin — it cannot record the policy decision, the execution
  id/result, or the redaction boundary. The richer event is worth the columns.
- **Emit audit events onto Kafka.** That is 5G. A durable, queryable, in-database
  trail is the 5E deliverable; a Kafka mirror can be added later without changing
  the model.
- **Best-effort audit insert after committing the state change.** Rejected
  explicitly — a committed transition with a missing audit row is exactly the
  failure an audit trail must not have.
- **Application-only append-only (no database trigger).** Rejected as the sole
  guarantee — the trigger makes the property true even against a direct SQL
  client or a future bug, at almost no cost.
- **Store the full remediation parameters verbatim in each event.** Rejected —
  the catalogue parameters are already on the `remediations` row; the event keeps
  a redacted copy on `PROPOSAL_CREATED` only, through the sanitisation boundary.
- **A generic `metadata` JSON blob instead of typed columns.** Rejected — typed,
  CHECK-constrained columns for state / actor / policy / execution keep the trail
  queryable and defensible; `event_metadata` is a small bounded extra, not the
  primary record.

## Consequences

- `docker compose up --build` still starts `remediation-migrate` (now `0001` +
  `0002` + `0003`) and `remediation-controller` (`:8005`). No new container, no
  Kafka, no infrastructure dependency.
- `RemediationService` gains an optional `metrics` dependency and emits
  `remediation.audit.events_written` (by event type) and
  `remediation.audit.write_failures`. No new observability subsystem.
- 5F adds recovery verification (`EXECUTED → VERIFYING → RECOVERED |
  RECOVERY_FAILED`) and will append `VERIFICATION_*` events to this same trail —
  no schema change expected. 5G adds Kafka lifecycle events.
- The end-to-end safety story is now independently auditable: for any
  remediation, an operator can reconstruct — chronologically and immutably —
  which incident triggered it, what was proposed, what policy decided and why,
  who approved or rejected it and when, whether execution was requested and
  started, which executor ran it, and whether it succeeded or failed.
