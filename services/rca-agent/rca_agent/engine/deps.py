"""Deterministic infrastructure the graph nodes close over (ADR-021).

None of this is LangGraph channel state and none of it is influenced by the LLM:
the tool registry, the per-investigation evidence-id allocator + budget, the
resource meter, the hard limits, the clock, and the reasoner handle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from rca_agent.limits import ResourceLimits, ResourceUsage
from rca_agent.llm.base import LlmClient
from rca_agent.tools import ToolContext, ToolRegistry


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class GraphDeps:
    incident_id: str
    investigation_id: str
    mode: str
    registry: ToolRegistry
    llm: LlmClient
    limits: ResourceLimits
    tool_context: ToolContext
    usage: ResourceUsage
    clock: Callable[[], datetime] = _utcnow
    # bounded model retries (transient provider errors / malformed output)
    max_llm_retries: int = 1
    max_repair_attempts: int = 1
    _seq: int = field(default=0, repr=False)

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def now(self) -> datetime:
        return self.clock()
