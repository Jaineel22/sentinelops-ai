"""The fixed evidence-tool registry (ADR-020).

Built once from a hard-coded list. There is **no public ``register`` method** —
neither the LLM, evidence content, nor configuration can add a tool. The
investigation engine (4C) discovers tools through :meth:`ToolRegistry.specs`
and invokes them by :class:`ToolName` only.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

from rca_agent.config import Settings
from rca_agent.tools.base import EvidenceTool
from rca_agent.tools.http_sources import ServiceHealthClient, ServiceMetricsClient
from rca_agent.tools.incident_api import IncidentApiClient
from rca_agent.tools.incident_tools import (
    GetAnomalyEvidenceTool,
    GetIncidentTimelineTool,
    GetIncidentTool,
    GetRelatedIncidentsTool,
)
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.observability_tools import GetServiceHealthTool, GetServiceMetricsTool
from rca_agent.tools.unavailable import (
    GetRecentDeploymentsTool,
    GetRecentLogsTool,
    GetServiceDependenciesTool,
    GetTracesTool,
)

# The complete, closed set of tool classes. Order is stable.
_REGISTERED_ORDER: tuple[ToolName, ...] = tuple(ToolName)


class ToolSpec(BaseModel):
    """What the investigation engine sees when discovering tools."""

    model_config = ConfigDict(frozen=True)

    name: ToolName
    description: str
    availability: ToolAvailability
    read_only: bool
    input_schema: dict[str, Any]


class ToolRegistry:
    def __init__(self, tools: Sequence[EvidenceTool[Any]]) -> None:
        by_name: dict[ToolName, EvidenceTool[Any]] = {}
        for tool in tools:
            if tool.name in by_name:
                raise ValueError(f"duplicate tool registration: {tool.name}")
            if not tool.is_read_only:
                raise ValueError(f"tool {tool.name} is not read-only")
            by_name[tool.name] = tool
        missing = set(ToolName) - set(by_name)
        if missing:
            raise ValueError(f"registry is missing tools: {sorted(missing)}")
        self._by_name: dict[ToolName, EvidenceTool[Any]] = by_name

    def get(self, name: ToolName) -> EvidenceTool[Any]:
        return self._by_name[name]

    def has(self, name: str) -> bool:
        return name in set(ToolName) and ToolName(name) in self._by_name

    def names(self) -> frozenset[ToolName]:
        return frozenset(self._by_name)

    def available(self) -> tuple[EvidenceTool[Any], ...]:
        return tuple(
            self._by_name[n]
            for n in _REGISTERED_ORDER
            if self._by_name[n].availability is ToolAvailability.AVAILABLE
        )

    def unavailable(self) -> tuple[EvidenceTool[Any], ...]:
        return tuple(
            self._by_name[n]
            for n in _REGISTERED_ORDER
            if self._by_name[n].availability is ToolAvailability.UNAVAILABLE
        )

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(
            ToolSpec(
                name=t.name,
                description=t.description,
                availability=t.availability,
                read_only=t.is_read_only,
                input_schema=t.request_model.model_json_schema(),
            )
            for t in (self._by_name[n] for n in _REGISTERED_ORDER)
        )


def build_registry(settings: Settings, *, http_client: httpx.AsyncClient) -> ToolRegistry:
    """Construct the registry with real HTTP-backed clients. The caller owns
    ``http_client``'s lifecycle (the 4C app lifespan; tests inject a mock)."""

    incident_api = IncidentApiClient(
        settings.rca.incident_api_base_url,
        client=http_client,
        timeout=settings.rca.http_timeout_seconds,
    )
    metrics = ServiceMetricsClient(
        settings.rca.service_metrics_urls,
        client=http_client,
        timeout=settings.rca.http_timeout_seconds,
    )
    health = ServiceHealthClient(
        settings.rca.service_health_urls,
        client=http_client,
        timeout=settings.rca.http_timeout_seconds,
    )
    return ToolRegistry(
        [
            GetIncidentTool(incident_api),
            GetIncidentTimelineTool(incident_api),
            GetAnomalyEvidenceTool(incident_api),
            GetRelatedIncidentsTool(incident_api),
            GetServiceMetricsTool(metrics),
            GetServiceHealthTool(health),
            GetRecentLogsTool(),
            GetTracesTool(),
            GetRecentDeploymentsTool(),
            GetServiceDependenciesTool(),
        ]
    )
