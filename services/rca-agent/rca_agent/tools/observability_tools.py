"""Evidence tools backed by an instrumented service's own endpoints (ADR-020).

Both are constrained to the configured service allow-list; neither accepts a
URL. ``get_service_metrics`` is a point-in-time scrape — there is no queryable
historical metrics store in this repository (that arrives with Phase 7).
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from rca_agent.domain import EvidenceSourceType
from rca_agent.tools.base import EvidenceTool, build_evidence
from rca_agent.tools.context import ToolContext
from rca_agent.tools.contracts import GetServiceHealthRequest, GetServiceMetricsRequest
from rca_agent.tools.http_sources import ServiceHealthClient, ServiceMetricsClient
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.results import ToolResult, ToolResultStatus


class GetServiceMetricsTool(EvidenceTool[GetServiceMetricsRequest]):
    name: ClassVar[ToolName] = ToolName.GET_SERVICE_METRICS
    description: ClassVar[str] = (
        "Read the current value(s) of named Prometheus metric families from an "
        "allow-listed instrumented service's /metrics endpoint. Point-in-time "
        "only; no historical range is queryable."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetServiceMetricsRequest

    def __init__(self, client: ServiceMetricsClient) -> None:
        self._client = client

    async def _run(self, request: GetServiceMetricsRequest, ctx: ToolContext) -> ToolResult:
        payload = await self._client.scrape(request.service, list(request.metric_names))
        present = [s.metric_name for s in payload.series if s.present]
        ev = build_evidence(
            ctx,
            source_type=EvidenceSourceType.METRIC,
            tool_name=self.name,
            source_reference=f"metrics:{request.service}/metrics",
            summary=(
                f"{request.service} point-in-time metrics; present: "
                f"{', '.join(present) or 'none of the requested names'}"
            ),
            content=payload,
            service=request.service,
            observed_at=payload.scraped_at,
        )
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=[ev],
            summary=f"scraped {len(present)}/{len(request.metric_names)} requested metric(s)",
            query=request.model_dump(mode="json"),
        )


class GetServiceHealthTool(EvidenceTool[GetServiceHealthRequest]):
    name: ClassVar[ToolName] = ToolName.GET_SERVICE_HEALTH
    description: ClassVar[str] = (
        "Check the current /health and /ready status of an allow-listed instrumented service."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetServiceHealthRequest

    def __init__(self, client: ServiceHealthClient) -> None:
        self._client = client

    async def _run(self, request: GetServiceHealthRequest, ctx: ToolContext) -> ToolResult:
        payload = await self._client.check(request.service)
        ev = build_evidence(
            ctx,
            source_type=EvidenceSourceType.SERVICE_HEALTH,
            tool_name=self.name,
            source_reference=f"health:{request.service}/health+/ready",
            summary=(f"{request.service}: health={payload.health}, readiness={payload.readiness}"),
            content=payload,
            service=request.service,
            observed_at=payload.checked_at,
        )
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=[ev],
            summary=f"health={payload.health} readiness={payload.readiness}",
            query=request.model_dump(mode="json"),
        )
