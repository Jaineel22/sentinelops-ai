"""The bounded LangGraph investigation engine (Phase 4C, ADR-021)."""

from __future__ import annotations

from rca_agent.engine.deps import GraphDeps
from rca_agent.engine.graph import build_graph, recursion_limit
from rca_agent.engine.plan import PlanValidation, ValidatedCall, validate_plan
from rca_agent.engine.service import InvestigationOutcome, InvestigationService

__all__ = [
    "GraphDeps",
    "InvestigationOutcome",
    "InvestigationService",
    "PlanValidation",
    "ValidatedCall",
    "build_graph",
    "recursion_limit",
    "validate_plan",
]
