"""Hard resource limits for an investigation.

Deterministic code owns these — the LLM never sees or sets them. When any limit
is hit the engine (Sub-phase 4C) terminates the investigation into a structured
``TIMED_OUT`` / ``INSUFFICIENT_EVIDENCE`` outcome rather than looping.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field


class ResourceLimits(BaseModel):
    """Bounds for one investigation. Values come from ``RCA_*`` settings."""

    model_config = ConfigDict(frozen=True)

    max_tool_calls: int = Field(default=12, ge=1, le=100)
    max_steps: int = Field(default=25, ge=1, le=200)
    max_evidence_items: int = Field(default=40, ge=1, le=200)
    timeout_seconds: float = Field(default=120.0, gt=0, le=900)
    max_hypotheses: int = Field(default=5, ge=1, le=20)


@dataclass
class ResourceUsage:
    """Running counters for the deterministic loop guard."""

    tool_calls: int = 0
    steps: int = 0
    evidence_items: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))

    def elapsed_seconds(self, *, now: datetime | None = None) -> float:
        return ((now or datetime.now(tz=UTC)) - self.started_at).total_seconds()


class LimitExceeded(RuntimeError):
    """Raised when an investigation would exceed one of its :class:`ResourceLimits`."""

    def __init__(self, kind: str, limit: float) -> None:
        self.kind = kind
        self.limit = limit
        super().__init__(f"investigation limit reached: {kind} > {limit}")


def check_limits(
    usage: ResourceUsage, limits: ResourceLimits, *, now: datetime | None = None
) -> None:
    """Raise :class:`LimitExceeded` if any bound is exceeded. Pure guard — call
    it before each tool call / step; never trust the agent to stop itself."""

    if usage.tool_calls > limits.max_tool_calls:
        raise LimitExceeded("tool_calls", limits.max_tool_calls)
    if usage.steps > limits.max_steps:
        raise LimitExceeded("steps", limits.max_steps)
    if usage.evidence_items > limits.max_evidence_items:
        raise LimitExceeded("evidence_items", limits.max_evidence_items)
    if usage.elapsed_seconds(now=now) > limits.timeout_seconds:
        raise LimitExceeded("time", limits.timeout_seconds)
