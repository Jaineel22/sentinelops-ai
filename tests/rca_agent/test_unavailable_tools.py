"""Registered-but-unavailable evidence tools (ADR-020).

logs / traces / deployments / dependencies have no backend in this repository.
The tools are registered (honest interface) but always return
SOURCE_UNAVAILABLE with no evidence — never fabricated data.
"""

from __future__ import annotations

import httpx
import pytest

from rca_agent.config import Settings
from rca_agent.tools import ToolName, ToolRegistry, build_registry
from rca_agent.tools.context import ToolContext
from rca_agent.tools.names import UNAVAILABLE_TOOLS
from rca_agent.tools.results import ToolResultStatus


@pytest.fixture
def registry() -> ToolRegistry:
    return build_registry(Settings(), http_client=httpx.AsyncClient())


@pytest.mark.parametrize("name", sorted(UNAVAILABLE_TOOLS))
async def test_unavailable_tool_reports_unavailable(
    name: ToolName, registry: ToolRegistry, tool_context: ToolContext
) -> None:
    tool = registry.get(name)
    result = await tool.run({"service": "orders-service"}, tool_context)
    assert result.status is ToolResultStatus.SOURCE_UNAVAILABLE
    assert result.evidence == []
    assert result.error is not None
    assert "not available" in result.error.message
    assert tool_context.issued == 0  # no evidence id was consumed


async def test_unavailable_tool_does_not_hit_the_network(
    registry: ToolRegistry, tool_context: ToolContext
) -> None:
    # UnavailableTool has no HTTP client at all; the availability gate fires
    # before _run, so even a wildly invalid request just returns UNAVAILABLE.
    tool = registry.get(ToolName.GET_TRACES)
    result = await tool.run({"anything": "goes", "service": 12345}, tool_context)
    assert result.status is ToolResultStatus.SOURCE_UNAVAILABLE


def test_unavailable_tools_appear_in_specs_marked_unavailable(registry: ToolRegistry) -> None:
    specs = {s.name: s for s in registry.specs()}
    for name in UNAVAILABLE_TOOLS:
        assert specs[name].availability == "UNAVAILABLE"
        assert "UNAVAILABLE" in specs[name].description
