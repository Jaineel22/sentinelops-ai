"""The live Anthropic-backed reasoner (Sub-phase 4D, ADR-021 / ADR-022).

One more :class:`~rca_agent.llm.base.LlmClient` implementation — the investigation
graph does not know whether it is talking to this or to
:class:`~rca_agent.llm.mock.MockLlmClient`.

Boundaries this client keeps:

* **It does not build prompts.** :mod:`rca_agent.llm.prompts` renders the fixed
  ADR-021 message architecture from the typed request; this client only
  translates that into the Anthropic wire format.
* **It does not give the model tools.** The single forced-tool-use call per
  operation is a *transport* for a typed proposal — its schema is one of the
  existing ``*Result`` DTOs. The model cannot call a SentinelOps evidence tool,
  run code, or reach the network/filesystem/OS through this client.
* **It does not trust the output.** The model response is parsed into the
  existing Pydantic DTO or rejected as :class:`LlmMalformedOutput`. Deterministic
  code downstream (``validate_plan`` / ``validate_report``) remains authoritative.
* **It bounds the request.** Explicit timeout, explicit ``max_tokens``, explicit
  prompt-size ceiling, small bounded SDK retries. No unbounded network call, no
  retry of malformed output.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from rca_agent.config import Settings
from rca_agent.llm.base import (
    AnalysisResult,
    AnalyzeRequest,
    LlmConfigurationError,
    LlmMalformedOutput,
    LlmProviderError,
    LlmTimeout,
    PlanRequest,
    PlanResult,
    SynthesisResult,
    SynthesizeRequest,
    VerificationResult,
    VerifyRequest,
)
from rca_agent.llm.prompts import LlmOperation, PromptTooLarge, build_messages

# The default when LLM_MODEL is unset. Overridable via config; pinned, not "latest".
DEFAULT_MODEL = "claude-opus-5"

_ResultT = TypeVar("_ResultT", bound=BaseModel)
_MessagesCreate = Callable[..., Awaitable[Any]]


class AnthropicLlmClient:
    """``LlmClient`` backed by the Anthropic Messages API."""

    provider = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = DEFAULT_MODEL,
        base_url: str | None = None,
        max_output_tokens: int = 4096,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        max_prompt_chars: int = 200_000,
        _messages_create: _MessagesCreate | None = None,
    ) -> None:
        self.model: str | None = model
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_prompt_chars = max_prompt_chars
        if _messages_create is not None:
            self._create = _messages_create
        else:
            client = anthropic.AsyncAnthropic(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
            self._create = client.messages.create

    @classmethod
    def from_settings(cls, settings: Settings) -> AnthropicLlmClient:
        llm = settings.llm
        if llm.api_key is None or not llm.api_key.get_secret_value():
            raise LlmConfigurationError(
                "LLM_API_KEY is required for RCA_MODE=live with LLM_PROVIDER=anthropic"
            )
        return cls(
            api_key=llm.api_key.get_secret_value(),
            model=llm.model or DEFAULT_MODEL,
            base_url=llm.base_url,
            max_output_tokens=llm.max_output_tokens,
            timeout_seconds=llm.request_timeout_seconds,
            max_retries=llm.max_retries,
            max_prompt_chars=llm.max_prompt_chars,
        )

    # --- typed operations -------------------------------------------
    async def plan(self, request: PlanRequest) -> PlanResult:
        return await self._invoke(request, PlanResult)

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        return await self._invoke(request, AnalysisResult)

    async def verify(self, request: VerifyRequest) -> VerificationResult:
        return await self._invoke(request, VerificationResult)

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult:
        return await self._invoke(request, SynthesisResult)

    # --- transport ------------------------------------------------
    async def _invoke(
        self,
        request: PlanRequest | AnalyzeRequest | VerifyRequest | SynthesizeRequest,
        result_model: type[_ResultT],
    ) -> _ResultT:
        try:
            op, messages = build_messages(request, max_chars=self._max_prompt_chars)
        except PromptTooLarge as exc:
            raise LlmProviderError(str(exc)) from exc

        system = "\n\n".join(m.content for m in messages if m.role == "system")
        user = "\n\n".join(m.content for m in messages if m.role == "user")
        tool = {
            "name": op.tool_name,
            "description": op.tool_description,
            "input_schema": result_model.model_json_schema(),
        }

        response = await self._call_provider(op, system=system, user=user, tool=tool)
        payload = _extract_forced_tool_input(response, op)
        try:
            return result_model.model_validate(payload)
        except ValidationError as exc:
            raise LlmMalformedOutput(
                f"{op.name}: model output did not match the {result_model.__name__} schema "
                f"({exc.error_count()} error(s))"
            ) from exc

    async def _call_provider(
        self, op: LlmOperation, *, system: str, user: str, tool: dict[str, Any]
    ) -> Any:
        try:
            return await self._create(
                model=self.model,
                max_tokens=self._max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[tool],
                tool_choice={"type": "tool", "name": op.tool_name},
            )
        except anthropic.APITimeoutError as exc:
            raise LlmTimeout(
                f"{op.name}: provider did not respond within {self._timeout_seconds}s"
            ) from exc
        except anthropic.APIConnectionError as exc:
            raise LlmProviderError(f"{op.name}: could not reach the LLM provider") from exc
        except anthropic.RateLimitError as exc:
            raise LlmProviderError(f"{op.name}: provider rate limit exceeded") from exc
        except anthropic.APIStatusError as exc:
            raise LlmProviderError(f"{op.name}: provider returned HTTP {exc.status_code}") from exc
        except anthropic.AnthropicError as exc:
            raise LlmProviderError(f"{op.name}: provider error ({type(exc).__name__})") from exc


def _extract_forced_tool_input(response: Any, op: LlmOperation) -> dict[str, Any]:
    """Pull the single forced ``tool_use`` block's input out of the response, or
    raise :class:`LlmMalformedOutput`. Never trusts free-form text."""

    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "max_tokens":
        raise LlmMalformedOutput(
            f"{op.name}: response hit max_tokens before a complete {op.tool_name} call"
        )
    if stop_reason == "refusal":
        raise LlmProviderError(f"{op.name}: provider declined the request")

    content = getattr(response, "content", None)
    if not isinstance(content, list):
        raise LlmMalformedOutput(f"{op.name}: provider response had no content blocks")

    for block in content:
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != op.tool_name:
            raise LlmMalformedOutput(
                f"{op.name}: model called an unexpected tool {getattr(block, 'name', None)!r}"
            )
        payload = getattr(block, "input", None)
        if not isinstance(payload, dict):
            raise LlmMalformedOutput(f"{op.name}: {op.tool_name} input was not a JSON object")
        return payload

    raise LlmMalformedOutput(
        f"{op.name}: model did not call the required {op.tool_name} tool "
        f"(stop_reason={stop_reason!r})"
    )
