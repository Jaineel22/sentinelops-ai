"""Fakes for the Anthropic SDK boundary (Sub-phase 4D). Not collected.

These stand in for ``AsyncAnthropic().messages.create`` so ``AnthropicLlmClient``
can be tested end to end without a network call or an API key.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx2

_EV_ID = re.compile(r"\[evidence (ev_[a-z0-9_]+)\]")


class FakeToolUseBlock:
    type = "tool_use"

    def __init__(self, name: str, tool_input: object) -> None:
        self.name = name
        self.input = tool_input


class FakeTextBlock:
    type = "text"

    def __init__(self, text: str) -> None:
        self.text = text


class FakeMessage:
    def __init__(self, content: list[object], *, stop_reason: str = "tool_use") -> None:
        self.content = content
        self.stop_reason = stop_reason


def forced_tool_response(tool_name: str, tool_input: object, **kw: Any) -> FakeMessage:
    return FakeMessage([FakeToolUseBlock(tool_name, tool_input)], **kw)


class RecordingCreate:
    """Callable stand-in for ``messages.create``. Records every kwargs payload
    and returns responses from ``responder(**kwargs)``."""

    def __init__(self, responder: Callable[..., object]) -> None:
        self._responder = responder
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: Any) -> object:
        self.calls.append(kwargs)
        result = self._responder(**kwargs)
        if isinstance(result, BaseException):
            raise result
        return result

    @property
    def last(self) -> dict[str, Any]:
        return self.calls[-1]

    @property
    def system(self) -> str:
        return str(self.last["system"])

    @property
    def user(self) -> str:
        return "\n".join(str(m["content"]) for m in self.last["messages"] if m["role"] == "user")


def _request() -> httpx2.Request:
    return httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def timeout_error() -> BaseException:
    import anthropic

    return anthropic.APITimeoutError(request=_request())


def connection_error() -> BaseException:
    import anthropic

    return anthropic.APIConnectionError(request=_request())


def rate_limit_error() -> BaseException:
    import anthropic

    return anthropic.RateLimitError(
        "rate limited", response=httpx2.Response(429, request=_request()), body=None
    )


def status_error(code: int = 503) -> BaseException:
    import anthropic

    return anthropic.APIStatusError(
        f"HTTP {code}", response=httpx2.Response(code, request=_request()), body=None
    )


def evidence_ids_in(user_message: str) -> list[str]:
    """The ev_ ids the deterministic collector rendered into the evidence block —
    what a well-behaved model would ground its answer in."""

    seen: list[str] = []
    for match in _EV_ID.findall(user_message):
        if match not in seen:
            seen.append(match)
    return seen


Responder = Callable[..., object]
MessagesCreate = Callable[..., Awaitable[object]]
