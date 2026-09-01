"""Hermetic integration: the 4B evidence tools against the *real* Phase 3
Incident API (in-process via ``httpx.ASGITransport`` — no network, no DB, no
Kafka). Proves the HTTP contract the rca-agent depends on actually holds.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest_asyncio

from incident_correlator.app import create_app
from incident_correlator.config import Settings as IncidentSettings
from incident_correlator.correlation import CorrelationConfig
from incident_correlator.processor import AnomalyProcessor
from incident_correlator.repository import IncidentFilter, InMemoryIncidentRepository
from rca_agent.tools.context import ToolContext
from rca_agent.tools.incident_api import IncidentApiClient
from rca_agent.tools.incident_tools import (
    GetAnomalyEvidenceTool,
    GetIncidentTimelineTool,
    GetIncidentTool,
    GetRelatedIncidentsTool,
)
from rca_agent.tools.results import ToolResultStatus
from tests.incident_correlator.conftest import make_signal

_BASE = "http://incident-correlator.test"


@pytest_asyncio.fixture
async def live_incident_api() -> AsyncIterator[tuple[IncidentApiClient, str]]:
    repo = InMemoryIncidentRepository()
    processor = AnomalyProcessor(repo, correlation_config=CorrelationConfig(window_seconds=300))
    await processor.process(make_signal(abnormal_signals=["latency_p95_ms"]))
    await processor.process(
        make_signal(offset_seconds=20, abnormal_signals=["error_rate", "latency_p95_ms"])
    )
    incident_id = (await repo.list_incidents(IncidentFilter()))[0].id

    app = create_app(IncidentSettings(), repository=repo, run_consumer=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=_BASE) as client:
        yield IncidentApiClient(_BASE, client=client), incident_id


async def test_get_incident_tool_against_real_api(
    live_incident_api: tuple[IncidentApiClient, str], tool_context: ToolContext
) -> None:
    api, incident_id = live_incident_api
    result = await GetIncidentTool(api).run({"incident_id": incident_id}, tool_context)
    assert result.status is ToolResultStatus.OK
    assert result.evidence[0].content["id"] == incident_id
    assert result.evidence[0].content["anomaly_count"] == 2


async def test_anomaly_evidence_tool_against_real_api(
    live_incident_api: tuple[IncidentApiClient, str], tool_context: ToolContext
) -> None:
    api, incident_id = live_incident_api
    result = await GetAnomalyEvidenceTool(api).run({"incident_id": incident_id}, tool_context)
    assert result.status is ToolResultStatus.OK
    assert len(result.evidence) == 2
    assert all(e.source_type == "anomaly" for e in result.evidence)


async def test_timeline_tool_against_real_api(
    live_incident_api: tuple[IncidentApiClient, str], tool_context: ToolContext
) -> None:
    api, incident_id = live_incident_api
    result = await GetIncidentTimelineTool(api).run({"incident_id": incident_id}, tool_context)
    assert result.status is ToolResultStatus.OK
    assert result.evidence[0].content["transitions"][0]["to_status"] == "OPEN"


async def test_related_incidents_tool_against_real_api(
    live_incident_api: tuple[IncidentApiClient, str], tool_context: ToolContext
) -> None:
    api, _ = live_incident_api
    result = await GetRelatedIncidentsTool(api).run(
        {"service": "orders-service", "lookback_hours": 720}, tool_context
    )
    assert result.status is ToolResultStatus.OK
    assert len(result.evidence) >= 1


async def test_unknown_incident_is_not_found_against_real_api(
    live_incident_api: tuple[IncidentApiClient, str], tool_context: ToolContext
) -> None:
    api, _ = live_incident_api
    result = await GetIncidentTool(api).run({"incident_id": "inc_deadbeefdeadbeef"}, tool_context)
    assert result.status is ToolResultStatus.NOT_FOUND
