"""The bounded LangGraph investigation graph (ADR-021).

    initialize -> plan -> collect (loop) -> analyze -> verify --> synthesize -> validate --> END
                                              ^                       |            |  (repair, x1)
                                              +----------- (x1) ------+            +--> synthesize

Every edge is conditional through :func:`_route`, which sends the graph to
``END`` the moment a node sets a terminal ``status`` (COMPLETED /
INSUFFICIENT_EVIDENCE / FAILED / TIMED_OUT). ``recursion_limit`` is a hard
backstop; the real bounds are the deterministic limit checks inside the nodes.
"""

from __future__ import annotations

from collections.abc import Hashable
from typing import Any

from langgraph.graph import END, START, StateGraph

from rca_agent.domain import InvestigationStatus
from rca_agent.engine.deps import GraphDeps
from rca_agent.engine.nodes import InvestigationNodes
from rca_agent.state import InvestigationState

_NODES: tuple[str, ...] = (
    "initialize",
    "plan",
    "collect",
    "analyze",
    "verify",
    "synthesize",
    "validate",
)


def _route(state: InvestigationState) -> str:
    status = state.get("status")
    if isinstance(status, InvestigationStatus) and status.is_terminal:
        return str(END)
    nxt = state.get("next")
    return nxt if nxt in _NODES else str(END)


def recursion_limit(deps: GraphDeps) -> int:
    return 3 * (deps.limits.max_tool_calls + deps.limits.max_steps) + 25


def build_graph(deps: GraphDeps) -> Any:
    nodes = InvestigationNodes(deps)
    graph = StateGraph(InvestigationState)

    graph.add_node("initialize", nodes.initialize)
    graph.add_node("plan", nodes.plan)
    graph.add_node("collect", nodes.collect)
    graph.add_node("analyze", nodes.analyze)
    graph.add_node("verify", nodes.verify)
    graph.add_node("synthesize", nodes.synthesize)
    graph.add_node("validate", nodes.validate)

    graph.add_edge(START, "initialize")
    routes: dict[Hashable, str] = {name: name for name in _NODES}
    routes[END] = str(END)
    for name in _NODES:
        graph.add_conditional_edges(name, _route, routes)

    return graph.compile()
