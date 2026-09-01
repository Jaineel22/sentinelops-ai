"""The fixed set of evidence-tool names and their availability (ADR-020).

This is a *closed* enumeration. The investigation engine (Sub-phase 4C) can only
ever reference a name from here; there is no path by which the LLM, evidence
content, or configuration can introduce a new tool.
"""

from __future__ import annotations

from enum import StrEnum


class ToolName(StrEnum):
    # --- available: backed by a real data source in this repository ---
    GET_INCIDENT = "get_incident"
    GET_INCIDENT_TIMELINE = "get_incident_timeline"
    GET_ANOMALY_EVIDENCE = "get_anomaly_evidence"
    GET_RELATED_INCIDENTS = "get_related_incidents"
    GET_SERVICE_METRICS = "get_service_metrics"
    GET_SERVICE_HEALTH = "get_service_health"
    # --- registered but unavailable: no backend exists yet (Phase 7+) ---
    GET_RECENT_LOGS = "get_recent_logs"
    GET_TRACES = "get_traces"
    GET_RECENT_DEPLOYMENTS = "get_recent_deployments"
    GET_SERVICE_DEPENDENCIES = "get_service_dependencies"


class ToolAvailability(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


AVAILABLE_TOOLS: frozenset[ToolName] = frozenset(
    {
        ToolName.GET_INCIDENT,
        ToolName.GET_INCIDENT_TIMELINE,
        ToolName.GET_ANOMALY_EVIDENCE,
        ToolName.GET_RELATED_INCIDENTS,
        ToolName.GET_SERVICE_METRICS,
        ToolName.GET_SERVICE_HEALTH,
    }
)
UNAVAILABLE_TOOLS: frozenset[ToolName] = frozenset(t for t in ToolName if t not in AVAILABLE_TOOLS)
