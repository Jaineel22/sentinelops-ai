"""Deterministic validation of an LLM-proposed plan (ADR-021)."""

from __future__ import annotations

import httpx
import pytest

from rca_agent.config import Settings
from rca_agent.engine.plan import validate_plan
from rca_agent.llm.base import PlannedCall
from rca_agent.tools import ToolName, ToolRegistry, build_registry


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry(Settings(), http_client=httpx.AsyncClient())


def test_valid_calls_are_accepted(registry: ToolRegistry) -> None:
    calls = [
        PlannedCall(
            tool="get_anomaly_evidence",
            arguments={"incident_id": "inc_00112233", "limit": 10},
        ),
        PlannedCall(tool="get_service_health", arguments={"service": "orders-service"}),
    ]
    result = validate_plan(calls, registry, max_calls=12)
    assert [str(c.tool_name) for c in result.accepted] == [
        "get_anomaly_evidence",
        "get_service_health",
    ]
    assert result.rejected == []


def test_unknown_tool_is_rejected(registry: ToolRegistry) -> None:
    result = validate_plan(
        [PlannedCall(tool="run_shell", arguments={"cmd": "rm -rf /"})], registry, max_calls=12
    )
    assert not result.accepted
    assert "not a registered evidence tool" in result.rejected[0]


def test_unavailable_tool_is_rejected(registry: ToolRegistry) -> None:
    result = validate_plan(
        [PlannedCall(tool="get_recent_logs", arguments={"service": "orders-service"})],
        registry,
        max_calls=12,
    )
    assert not result.accepted
    assert "not available" in result.rejected[0]


def test_bad_arguments_are_rejected(registry: ToolRegistry) -> None:
    result = validate_plan(
        [
            PlannedCall(tool="get_incident", arguments={"incident_id": "'; DROP TABLE x"}),
            PlannedCall(  # missing metric_names
                tool="get_service_metrics", arguments={"service": "orders-service"}
            ),
            PlannedCall(
                tool="get_related_incidents",
                arguments={"service": "orders-service", "lookback_hours": 99999},
            ),
        ],
        registry,
        max_calls=12,
    )
    assert not result.accepted
    assert len(result.rejected) == 3
    assert all("invalid arguments" in r for r in result.rejected)


def test_arbitrary_extra_argument_is_rejected(registry: ToolRegistry) -> None:
    result = validate_plan(
        [
            PlannedCall(
                tool="get_incident",
                arguments={"incident_id": "inc_00112233", "url": "http://evil", "sql": "select 1"},
            )
        ],
        registry,
        max_calls=12,
    )
    assert not result.accepted  # extra="forbid" on the request model


def test_duplicate_calls_are_deduped(registry: ToolRegistry) -> None:
    call = PlannedCall(tool="get_incident", arguments={"incident_id": "inc_00112233"})
    result = validate_plan([call, call], registry, max_calls=12)
    assert len(result.accepted) == 1
    assert any("duplicate" in r for r in result.rejected)


def test_max_calls_is_enforced(registry: ToolRegistry) -> None:
    calls = [
        PlannedCall(tool="get_incident", arguments={"incident_id": f"inc_00112{i:03d}"})
        for i in range(10)
    ]
    result = validate_plan(calls, registry, max_calls=3)
    assert len(result.accepted) == 3
    assert any("maximum" in r for r in result.rejected)


def test_registry_names_stay_closed(registry: ToolRegistry) -> None:
    # nothing the plan does can add a tool
    before = registry.names()
    validate_plan(
        [PlannedCall(tool="register_tool", arguments={"name": "x"})], registry, max_calls=12
    )
    assert registry.names() == before
    assert not registry.has("register_tool")
    assert set(registry.names()) == set(ToolName)
