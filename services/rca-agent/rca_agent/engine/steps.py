"""Operational investigation-trace helpers.

Records *what the agent did and why* in concise operational terms — never
private model reasoning. Each step gets a monotonic sequence number and a
timestamp from :class:`~rca_agent.engine.deps.GraphDeps`.
"""

from __future__ import annotations

from collections.abc import Sequence

from rca_agent.domain import InvestigationStatus, StepKind
from rca_agent.engine.deps import GraphDeps
from rca_agent.schemas import InvestigationStep


def step(
    deps: GraphDeps,
    *,
    kind: StepKind,
    phase: InvestigationStatus,
    description: str,
    tool_name: str | None = None,
    evidence_ids: Sequence[str] = (),
) -> InvestigationStep:
    seq = deps.next_seq()
    deps.usage.steps = seq
    return InvestigationStep(
        seq=seq,
        kind=kind,
        phase=phase,
        description=description[:2000],
        tool_name=tool_name,
        evidence_ids=list(evidence_ids),
        at=deps.now(),
    )
