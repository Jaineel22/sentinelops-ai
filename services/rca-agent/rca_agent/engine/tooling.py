"""Deterministic execution of one validated tool call inside the graph.

Every call goes through :func:`rca_agent.limits.check_limits` first (never trust
the model to stop), then the Sub-phase 4B tool wrapper (which re-validates and
sanitizes), then the resource meters are advanced.
"""

from __future__ import annotations

from rca_agent.engine.deps import GraphDeps
from rca_agent.engine.plan import ValidatedCall
from rca_agent.limits import check_limits
from rca_agent.tools.results import ToolResult


async def run_validated_call(deps: GraphDeps, call: ValidatedCall) -> ToolResult:
    check_limits(deps.usage, deps.limits, now=deps.now())
    deps.usage.tool_calls += 1

    tool = deps.registry.get(call.tool_name)
    result = await tool.run(call.arguments, deps.tool_context)

    deps.usage.evidence_items += len(result.evidence)
    return result
