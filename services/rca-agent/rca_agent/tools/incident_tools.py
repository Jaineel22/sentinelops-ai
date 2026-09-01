"""Evidence tools backed by the Phase 3 Incident API (read-only, ADR-020)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel

from rca_agent.domain import EvidenceSourceType
from rca_agent.tools.base import EvidenceTool, build_evidence
from rca_agent.tools.context import ToolContext
from rca_agent.tools.contracts import (
    GetAnomalyEvidenceRequest,
    GetIncidentRequest,
    GetIncidentTimelineRequest,
    GetRelatedIncidentsRequest,
)
from rca_agent.tools.incident_api import IncidentApiClient, IncidentApiError
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.results import ToolExecutionError, ToolResult, ToolResultStatus


def _as_tool_error(exc: IncidentApiError) -> ToolExecutionError:
    return ToolExecutionError(exc.code, exc.message)


class GetIncidentTool(EvidenceTool[GetIncidentRequest]):
    name: ClassVar[ToolName] = ToolName.GET_INCIDENT
    description: ClassVar[str] = (
        "Fetch the current state of one incident (status, severity, affected "
        "service, aggregated anomaly signals, lifecycle timestamps)."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetIncidentRequest

    def __init__(self, client: IncidentApiClient) -> None:
        self._api = client

    async def _run(self, request: GetIncidentRequest, ctx: ToolContext) -> ToolResult:
        try:
            incident = await self._api.get_incident(request.incident_id)
        except IncidentApiError as exc:
            raise _as_tool_error(exc) from exc
        ev = build_evidence(
            ctx,
            source_type=EvidenceSourceType.INCIDENT,
            tool_name=self.name,
            source_reference=f"incident-api:GET /incidents/{incident.id}",
            summary=(
                f"Incident {incident.id} on {incident.service} ({incident.environment}): "
                f"{incident.severity} {incident.status}, {incident.anomaly_count} anomaly window(s)"
            ),
            content=incident,
            service=incident.service,
            observed_at=incident.started_at,
        )
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=[ev],
            summary=f"loaded incident {incident.id}",
            query=request.model_dump(mode="json"),
        )


class GetIncidentTimelineTool(EvidenceTool[GetIncidentTimelineRequest]):
    name: ClassVar[ToolName] = ToolName.GET_INCIDENT_TIMELINE
    description: ClassVar[str] = (
        "Fetch the ordered lifecycle-transition history of one incident "
        "(who/what moved it between states, and when)."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetIncidentTimelineRequest

    def __init__(self, client: IncidentApiClient) -> None:
        self._api = client

    async def _run(self, request: GetIncidentTimelineRequest, ctx: ToolContext) -> ToolResult:
        try:
            history = await self._api.get_incident_history(request.incident_id)
        except IncidentApiError as exc:
            raise _as_tool_error(exc) from exc
        ev = build_evidence(
            ctx,
            source_type=EvidenceSourceType.INCIDENT,
            tool_name=self.name,
            source_reference=f"incident-api:GET /incidents/{request.incident_id}/history",
            summary=f"{len(history)} lifecycle transition(s) for incident {request.incident_id}",
            content={
                "incident_id": request.incident_id,
                "transitions": [h.model_dump(mode="json") for h in history],
            },
        )
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=[ev],
            summary=f"loaded {len(history)} transition(s)",
            query=request.model_dump(mode="json"),
        )


class GetAnomalyEvidenceTool(EvidenceTool[GetAnomalyEvidenceRequest]):
    name: ClassVar[ToolName] = ToolName.GET_ANOMALY_EVIDENCE
    description: ClassVar[str] = (
        "Fetch the anomaly-detection evidence that formed the incident: per "
        "telemetry window, the Phase 2 model's score, threshold, the operational "
        "signals it saw, and which signals were abnormal. Does not run detection."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetAnomalyEvidenceRequest

    def __init__(self, client: IncidentApiClient) -> None:
        self._api = client

    async def _run(self, request: GetAnomalyEvidenceRequest, ctx: ToolContext) -> ToolResult:
        budget = ctx.remaining_evidence()
        if budget <= 0:
            raise ToolExecutionError(
                ToolResultStatus.LIMIT_EXCEEDED, "the investigation evidence budget is exhausted"
            )
        try:
            items = await self._api.get_incident_evidence(request.incident_id)
        except IncidentApiError as exc:
            raise _as_tool_error(exc) from exc

        take = min(request.limit, budget, len(items))
        evidence = [
            build_evidence(
                ctx,
                source_type=EvidenceSourceType.ANOMALY,
                tool_name=self.name,
                source_reference=(
                    f"incident-api:GET /incidents/{request.incident_id}/evidence#{item.event_id}"
                ),
                summary=(
                    f"anomaly {item.detector} score={item.anomaly_score:.3f} "
                    f"(threshold {item.threshold:.3f}); abnormal: "
                    f"{', '.join(item.abnormal_signals) or 'none'}"
                ),
                content=item,
                observed_at=item.window_start,
            )
            for item in items[:take]
        ]
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=evidence,
            summary=f"loaded {len(evidence)} of {len(items)} anomaly window(s)",
            query=request.model_dump(mode="json"),
        )


class GetRelatedIncidentsTool(EvidenceTool[GetRelatedIncidentsRequest]):
    name: ClassVar[ToolName] = ToolName.GET_RELATED_INCIDENTS
    description: ClassVar[str] = (
        "List other incidents for the same service within a bounded recent "
        "window, for correlation (has this service been unstable, or is this "
        "isolated?)."
    )
    availability: ClassVar[ToolAvailability] = ToolAvailability.AVAILABLE
    request_model: ClassVar[type[BaseModel]] = GetRelatedIncidentsRequest

    def __init__(self, client: IncidentApiClient) -> None:
        self._api = client

    async def _run(self, request: GetRelatedIncidentsRequest, ctx: ToolContext) -> ToolResult:
        budget = ctx.remaining_evidence()
        if budget <= 0:
            raise ToolExecutionError(
                ToolResultStatus.LIMIT_EXCEEDED, "the investigation evidence budget is exhausted"
            )
        try:
            items = await self._api.list_incidents(
                service=request.service,
                lookback_hours=request.lookback_hours,
                status=request.status,
                limit=request.limit,
            )
        except IncidentApiError as exc:
            raise _as_tool_error(exc) from exc

        take = min(request.limit, budget, len(items))
        evidence = [
            build_evidence(
                ctx,
                source_type=EvidenceSourceType.RELATED_INCIDENT,
                tool_name=self.name,
                source_reference=f"incident-api:GET /incidents?service={request.service}#{item.id}",
                summary=f"related incident {item.id}: {item.severity} {item.status} - {item.title}",
                content=item,
                service=item.service,
                observed_at=item.started_at,
            )
            for item in items[:take]
        ]
        return ToolResult(
            tool_name=self.name,
            status=ToolResultStatus.OK,
            evidence=evidence,
            summary=f"found {len(items)} related incident(s) for {request.service}",
            query=request.model_dump(mode="json"),
        )
