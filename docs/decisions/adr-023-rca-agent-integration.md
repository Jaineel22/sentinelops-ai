# ADR-023: RCA-agent integration — event idempotency, async API, no Phase 3 write-back

- Status: Accepted
- Date: 2026-09-02

## Context

Sub-phases 4A–4D built the RCA investigation engine and its LLM boundary, but it
was reachable only from unit tests and a scenario script. Sub-phase 4E connects
it to the running system: the Phase 3 `incident.opened` Kafka event, an HTTP API,
and Docker Compose. Three questions had to be settled:

1. Kafka is at-least-once. How is "one investigation per incident" enforced when
   `incident.opened` can be redelivered?
2. An investigation can take up to 120 s (live LLM). Does `POST /investigations`
   block for that long, or run asynchronously — and if async, with what infra?
3. Does the RCA result flow back into Phase 3 (a new incident field / endpoint)?

## Decision

### 1. Event idempotency: skip if any investigation exists for the incident

Phase 3 emits `incident.opened` **exactly once per incident** (on CREATE, and on
SUPERSEDE which mints a new incident id). Therefore any redelivery is a
duplicate. `IncidentEventConsumer` checks
`InvestigationService.get_existing_investigation(incident_id)` (the most recent
investigation, **any** status) and returns without acting if one exists —
recording a `rca.kafka.events.duplicate` metric. This is stricter than the
service's own guard (which only blocks *concurrent active* investigations) and is
the right rule for an event that fires once.

A concurrent duplicate that races past the check is still caught by the Sub-phase
4A partial unique index (`investigations(incident_id) WHERE status` non-terminal):
`InvestigationService.begin` catches the `IntegrityError` and returns the
in-flight investigation. In practice the shared `IdempotentConsumer` processes a
partition serially and `incident.events` is keyed by `correlation_key`, so events
for one incident are already ordered — the race is theoretical, the index is the
backstop.

`incident.updated` / `incident.resolved` share the topic; the consumer acks and
ignores them (not a DLQ condition). Malformed / unsupported `incident.opened`
goes to `incident.events.dlq`; a transient failure *starting* the investigation
is a bounded `RetryableError` (a `FAILED` investigation is a valid terminal
outcome, not a delivery failure, and is not retried).

The consumer contains **no LLM logic** — it translates the envelope and calls
`InvestigationService`.

### 2. Async API via an in-process background task — no external queue

`InvestigationService.investigate()` is split into `begin()` (one fast `INSERT`)
and `run_to_completion()` (the bounded graph + the final transaction);
`investigate()` composes them, so every existing caller and test is unchanged.

`POST /investigations` calls `begin()` synchronously, returns `202` with the
PENDING investigation, and runs `run_to_completion()` on a tracked
`asyncio` task. The transaction boundary the engine already guarantees (no DB
transaction held across model calls) is preserved. On shutdown the app drains
in-flight tasks for `investigation_timeout + 5 s`, then cancels.

**No Celery / Redis / second broker.** One investigation is hard-bounded (≤ 120 s
wall clock, ≤ 12 tool calls, ≤ 40 evidence items — Sub-phase 4C limits); a
handful of background tasks in the service process is sufficient for a
one-incident-at-a-time reliability tool and keeps the system maintainable
(project constraint). `run_investigations_in_background=False` is available for
deployments (and tests) that want a blocking, bounded POST.

The API is **idempotent per incident**: if an investigation already exists
(running *or* finished) `POST` returns it with `200` rather than starting another.

### 3. No write-back to Phase 3

The RCA result is persisted in the rca-agent's own tables and served from its own
API (`GET /investigations/{id}`, `GET /incidents/{id}/investigation`). It is
**not** written back to the incident record. Phase 3's API and schema are
unchanged (ADR-019: the rca-agent depends on Phase 3, never the reverse). A
future phase that wants RCA visible on the incident can add a read-time join or a
Phase 3 endpoint — that is not 4E.

## Alternatives considered

- **Dedupe on the Kafka `event_id` (like the anomaly consumer).** Rejected —
  `event_id` dedupe guards against *this exact message* twice; we need "this
  incident investigated once", which spans any redelivery and the concurrent
  race. The existing-investigation check covers both.
- **Synchronous `POST /investigations`.** Rejected as the default — a 120 s HTTP
  request is fragile; kept as an opt-in flag.
- **A task queue (Celery / RQ / Arq).** Rejected — infra weight unjustified for a
  bounded, low-volume workload (project constraint: correctness over technology
  count).
- **Write the `RCAReport` back onto the incident.** Rejected for 4E — it would
  change Phase 3's contract; out of scope.

## Consequences

- The full chain runs from `docker compose up --build` with `RCA_MODE=mock` and
  **no API key**: orders-service → anomaly-detector → incident-correlator →
  `incident.opened` → rca-agent → `RCAReport` in Postgres → Investigation API.
- Adding a `rca-agent` deployment is one service + one migrate one-shot in
  compose, mirroring the Phase 3 pair.
- The Phase 4/5 boundary is unchanged: `RecommendedAction` stays a
  human-approval-required recommendation; there is no executor, and 4E adds none.
