"""The fixed evidence-tool registry (ADR-020)."""

from __future__ import annotations

import httpx
import pytest

from rca_agent.config import Settings
from rca_agent.tools import ToolAvailability, ToolName, ToolRegistry, build_registry
from rca_agent.tools.names import AVAILABLE_TOOLS, UNAVAILABLE_TOOLS

_EXPECTED_TOOLS = {
    "get_incident",
    "get_incident_timeline",
    "get_anomaly_evidence",
    "get_related_incidents",
    "get_service_metrics",
    "get_service_health",
    "get_recent_logs",
    "get_traces",
    "get_recent_deployments",
    "get_service_dependencies",
}


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry(Settings(), http_client=httpx.AsyncClient())


def test_registry_contains_exactly_the_approved_tools(registry: ToolRegistry) -> None:
    assert {str(n) for n in registry.names()} == _EXPECTED_TOOLS


def test_tool_names_are_a_closed_enum() -> None:
    assert {str(n) for n in ToolName} == _EXPECTED_TOOLS
    assert set(ToolName) == AVAILABLE_TOOLS | UNAVAILABLE_TOOLS
    assert AVAILABLE_TOOLS.isdisjoint(UNAVAILABLE_TOOLS)


def test_registry_has_no_public_registration_api(registry: ToolRegistry) -> None:
    for attr in ("register", "add", "add_tool", "__setitem__", "register_tool"):
        assert not hasattr(registry, attr)


def test_registry_rejects_a_registry_missing_a_tool() -> None:
    with pytest.raises(ValueError, match="missing tools"):
        ToolRegistry([])


def test_registry_rejects_duplicate_tool() -> None:
    reg = build_registry(Settings(), http_client=httpx.AsyncClient())
    dup = [reg.get(n) for n in ToolName] + [reg.get(ToolName.GET_INCIDENT)]
    with pytest.raises(ValueError, match="duplicate"):
        ToolRegistry(dup)


def test_available_vs_unavailable_partition(registry: ToolRegistry) -> None:
    available = {t.name for t in registry.available()}
    unavailable = {t.name for t in registry.unavailable()}
    assert available == AVAILABLE_TOOLS
    assert unavailable == UNAVAILABLE_TOOLS
    assert available.isdisjoint(unavailable)


def test_unavailable_tools_are_the_missing_infra() -> None:
    assert {str(n) for n in UNAVAILABLE_TOOLS} == {
        "get_recent_logs",
        "get_traces",
        "get_recent_deployments",
        "get_service_dependencies",
    }


def test_specs_are_stable_and_typed(registry: ToolRegistry) -> None:
    specs = registry.specs()
    assert [str(s.name) for s in specs] == [str(n) for n in ToolName]  # stable order
    for spec in specs:
        assert spec.read_only is True
        assert spec.description
        assert spec.input_schema["type"] == "object"
        assert spec.availability in (ToolAvailability.AVAILABLE, ToolAvailability.UNAVAILABLE)


def test_every_tool_is_read_only(registry: ToolRegistry) -> None:
    assert all(registry.get(n).is_read_only for n in ToolName)


def test_has_rejects_unknown_names(registry: ToolRegistry) -> None:
    assert registry.has("get_incident")
    assert not registry.has("rm_minus_rf")
    assert not registry.has("get_incident; DROP TABLE incidents")
