"""Live Groq-backed reasoner using the Groq chat-completions API."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import groq
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

DEFAULT_MODEL = "llama-3.3-70b-versatile"
_ResultT = TypeVar("_ResultT", bound=BaseModel)
_CompletionCreate = Callable[..., Awaitable[Any]]


class GroqLlmClient:
    """Implements the typed LLM boundary with one forced function call per operation."""

    provider = "groq"

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
        _completion_create: _CompletionCreate | None = None,
    ) -> None:
        self.model: str | None = model
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_prompt_chars = max_prompt_chars
        if _completion_create is not None:
            self._create = _completion_create
        else:
            client = groq.AsyncGroq(
                api_key=api_key,
                base_url=base_url,
                timeout=timeout_seconds,
                max_retries=max_retries,
            )
            self._create = client.chat.completions.create

    @classmethod
    def from_settings(cls, settings: Settings) -> GroqLlmClient:
        llm = settings.llm
        if llm.api_key is None or not llm.api_key.get_secret_value():
            raise LlmConfigurationError(
                "LLM_API_KEY is required for RCA_MODE=live with LLM_PROVIDER=groq"
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

    async def plan(self, request: PlanRequest) -> PlanResult:
        return await self._invoke(request, PlanResult)

    async def analyze(self, request: AnalyzeRequest) -> AnalysisResult:
        return await self._invoke(request, AnalysisResult)

    async def verify(self, request: VerifyRequest) -> VerificationResult:
        return await self._invoke(request, VerificationResult)

    async def synthesize(self, request: SynthesizeRequest) -> SynthesisResult:
        return await self._invoke(request, SynthesisResult)

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
        function = {
            "name": op.tool_name,
            "description": op.tool_description,
            "parameters": result_model.model_json_schema(),
        }
        try:
            response = await self._create(
                model=self.model,
                max_tokens=self._max_output_tokens,
                timeout=self._timeout_seconds,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                tools=[{"type": "function", "function": function}],
                tool_choice={"type": "function", "function": {"name": op.tool_name}},
            )
        except groq.APITimeoutError as exc:
            raise LlmTimeout(
                f"{op.name}: provider did not respond within {self._timeout_seconds}s"
            ) from exc
        except groq.APIConnectionError as exc:
            raise LlmProviderError(f"{op.name}: could not reach the LLM provider") from exc
        except groq.RateLimitError as exc:
            raise LlmProviderError(f"{op.name}: provider rate limit exceeded") from exc
        except groq.APIStatusError as exc:
            raise LlmProviderError(f"{op.name}: provider returned HTTP {exc.status_code}") from exc
        except groq.GroqError as exc:
            raise LlmProviderError(f"{op.name}: provider error ({type(exc).__name__})") from exc

        payload = _extract_forced_tool_input(response, op)
        try:
            return result_model.model_validate(payload)
        except ValidationError as exc:
            raise LlmMalformedOutput(
                f"{op.name}: model output did not match the {result_model.__name__} schema "
                f"({exc.error_count()} error(s))"
            ) from exc


def _extract_forced_tool_input(response: Any, op: LlmOperation) -> dict[str, Any]:
    choices = getattr(response, "choices", None)
    if not choices:
        raise LlmMalformedOutput(f"{op.name}: provider response had no choices")
    choice = choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        raise LlmMalformedOutput(
            f"{op.name}: response hit max_tokens before a complete {op.tool_name} call"
        )
    message = getattr(choice, "message", None)
    if getattr(message, "refusal", None):
        raise LlmProviderError(f"{op.name}: provider declined the request")
    tool_calls = getattr(message, "tool_calls", None) or []
    for call in tool_calls:
        function = getattr(call, "function", None)
        name = getattr(function, "name", None)
        if name != op.tool_name:
            raise LlmMalformedOutput(f"{op.name}: model called an unexpected tool {name!r}")
        try:
            payload = json.loads(getattr(function, "arguments", ""))
        except (TypeError, json.JSONDecodeError) as exc:
            raise LlmMalformedOutput(f"{op.name}: tool arguments were not valid JSON") from exc
        if not isinstance(payload, dict):
            raise LlmMalformedOutput(f"{op.name}: {op.tool_name} input was not a JSON object")
        return payload
    raise LlmMalformedOutput(
        f"{op.name}: model did not call the required {op.tool_name} tool "
        f"(finish_reason={getattr(choice, 'finish_reason', None)!r})"
    )
