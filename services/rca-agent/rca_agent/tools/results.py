"""Structured, machine-readable tool outcomes (ADR-020).

A tool never raises to its caller and never returns a stack trace or arbitrary
text. Every call yields a :class:`ToolResult` whose ``status`` the investigation
engine can branch on and whose ``error.message`` is safe to hand to the LLM
(no secrets, URLs, paths, or connection strings).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from rca_agent.schemas import Evidence
from rca_agent.tools.names import ToolName


class ToolResultStatus(StrEnum):
    OK = "OK"
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_SERVICE = "UNSUPPORTED_SERVICE"
    SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"  # the evidence source is not deployed
    NOT_FOUND = "NOT_FOUND"
    UPSTREAM_TIMEOUT = "UPSTREAM_TIMEOUT"
    UPSTREAM_UNAVAILABLE = "UPSTREAM_UNAVAILABLE"
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"


_RETRIABLE = frozenset(
    {
        ToolResultStatus.UPSTREAM_TIMEOUT,
        ToolResultStatus.UPSTREAM_UNAVAILABLE,
    }
)


class ToolError(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ToolResultStatus
    message: str = Field(max_length=500)  # sanitized; safe to show the model
    retriable: bool = False


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_name: ToolName
    status: ToolResultStatus
    evidence: list[Evidence] = Field(default_factory=list)
    error: ToolError | None = None
    summary: str = Field(default="", max_length=500)  # deterministic 1-liner for the trace
    query: dict[str, Any] = Field(default_factory=dict)  # echo of the *validated* params

    @property
    def ok(self) -> bool:
        return self.status is ToolResultStatus.OK

    @classmethod
    def failure(
        cls,
        tool_name: ToolName,
        code: ToolResultStatus,
        message: str,
        *,
        query: dict[str, Any] | None = None,
    ) -> ToolResult:
        return cls(
            tool_name=tool_name,
            status=code,
            error=ToolError(code=code, message=message, retriable=code in _RETRIABLE),
            summary=f"{tool_name}: {code}",
            query=query or {},
        )


class ToolExecutionError(Exception):
    """Raised inside a tool's ``_run``; converted to a :class:`ToolResult` by the
    ABC wrapper. ``message`` must already be safe to surface (no secrets)."""

    def __init__(self, code: ToolResultStatus, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")
