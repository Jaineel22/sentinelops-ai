"""Registered-but-unavailable evidence tools (ADR-020).

The architecture blueprint calls for logs, traces, deployment history, and a
service-dependency graph. None of those backends exist in this repository yet
(Loki / Tempo / a deployment metadata source arrive with Phase 7+). Rather than
omit them — which would hide the intended design — or fabricate them — which
would let the agent believe it retrieved evidence it never did — they are
registered with a real name, description, and input schema and **always** return
``SOURCE_UNAVAILABLE`` with no evidence.

Input is still validated (so the interface is honest and testable), but ``_run``
is never reached: :meth:`EvidenceTool.run` short-circuits on ``UNAVAILABLE``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from rca_agent.tools.base import EvidenceTool
from rca_agent.tools.context import ToolContext
from rca_agent.tools.contracts import (
    GetRecentDeploymentsRequest,
    GetRecentLogsRequest,
    GetServiceDependenciesRequest,
    GetTracesRequest,
)
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.results import ToolResult, ToolResultStatus


class _UnavailableTool(EvidenceTool[BaseModel]):
    availability: ClassVar[ToolAvailability] = ToolAvailability.UNAVAILABLE

    async def _run(self, request: BaseModel, ctx: ToolContext) -> ToolResult:  # pragma: no cover
        # Unreachable: run() short-circuits before _run for UNAVAILABLE tools.
        return ToolResult.failure(
            self.name,
            ToolResultStatus.SOURCE_UNAVAILABLE,
            f"{self.name} has no backing data source in this deployment",
        )


class GetRecentLogsTool(_UnavailableTool):
    name: ClassVar[ToolName] = ToolName.GET_RECENT_LOGS
    description: ClassVar[str] = (
        "Recent application log lines for a service. UNAVAILABLE: this "
        "deployment has no log-aggregation backend (Loki) yet."
    )
    request_model: ClassVar[type[BaseModel]] = GetRecentLogsRequest


class GetTracesTool(_UnavailableTool):
    name: ClassVar[ToolName] = ToolName.GET_TRACES
    description: ClassVar[str] = (
        "Distributed traces for a request or operation. UNAVAILABLE: this "
        "deployment has no trace backend (Tempo) yet."
    )
    request_model: ClassVar[type[BaseModel]] = GetTracesRequest


class GetRecentDeploymentsTool(_UnavailableTool):
    name: ClassVar[ToolName] = ToolName.GET_RECENT_DEPLOYMENTS
    description: ClassVar[str] = (
        "Recent deployments / configuration changes for a service. UNAVAILABLE: "
        "this deployment has no deployment-metadata source yet."
    )
    request_model: ClassVar[type[BaseModel]] = GetRecentDeploymentsRequest


class GetServiceDependenciesTool(_UnavailableTool):
    name: ClassVar[ToolName] = ToolName.GET_SERVICE_DEPENDENCIES
    description: ClassVar[str] = (
        "Upstream / downstream dependencies of a service. UNAVAILABLE: this "
        "deployment has no service dependency graph yet."
    )
    request_model: ClassVar[type[BaseModel]] = GetServiceDependenciesRequest
