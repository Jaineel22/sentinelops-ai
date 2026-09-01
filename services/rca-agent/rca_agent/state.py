"""Typed channel state for the LangGraph investigation graph (Sub-phase 4C).

``total=False`` so each node returns a partial update. ``evidence`` and ``steps``
use ``operator.add`` reducers so a node appending to them is merged, not
overwritten. Everything else is last-write-wins.

Resource meters (tool-call / step / evidence counts, the wall clock) and the
shared services (tool registry, tool context, LLM client, limits) live in
:class:`~rca_agent.engine.deps.GraphDeps`, **not** here — they are deterministic
infrastructure, never investigation data the model can influence.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, TypedDict

from rca_agent.domain import InvestigationStatus
from rca_agent.llm.base import (
    ProposedFinding,
    ProposedHypothesis,
    SynthesisResult,
)
from rca_agent.schemas import Evidence, Hypothesis, InvestigationStep, RCAReport


class InvestigationState(TypedDict, total=False):
    # identity
    incident_id: str
    investigation_id: str
    mode: str  # "mock" | "live"

    # lifecycle / routing
    status: InvestigationStatus
    terminal_reason: str | None
    next: str  # the node's declared successor; the router uses it unless terminal

    # inputs
    incident: dict[str, Any] | None  # the incident payload from get_incident

    # planning / collection bookkeeping
    plan_rationale: str
    queue: list[Any]  # pending rca_agent.engine.plan.ValidatedCall items
    reanalysis_count: int
    repair_count: int
    repair_errors: list[str]

    # accumulating working memory
    evidence: Annotated[list[Evidence], operator.add]
    steps: Annotated[list[InvestigationStep], operator.add]

    # analysis / verification
    proposed_findings: list[ProposedFinding]
    proposed_hypotheses: list[ProposedHypothesis]
    hypotheses: list[Hypothesis]  # finalized, with verdicts

    # output
    synthesis: SynthesisResult | None
    rca: RCAReport | None
