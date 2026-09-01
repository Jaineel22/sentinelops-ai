"""Controlled read-only evidence-tool layer (Phase 4B, ADR-020).

The investigation engine obtains all incident evidence through this layer and
nowhere else. Public surface:

* :class:`ToolRegistry` / :func:`build_registry` — the fixed tool set.
* :class:`ToolName`, :class:`ToolAvailability` — the closed name enumeration.
* :class:`ToolResult`, :class:`ToolError`, :class:`ToolResultStatus` — structured
  outcomes (no exceptions, no stack traces, sanitized messages).
* :class:`ToolContext` — per-investigation evidence-id allocation + budget.
"""

from __future__ import annotations

from rca_agent.tools.context import ToolContext
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.registry import ToolRegistry, ToolSpec, build_registry
from rca_agent.tools.results import (
    ToolError,
    ToolExecutionError,
    ToolResult,
    ToolResultStatus,
)

__all__ = [
    "ToolAvailability",
    "ToolContext",
    "ToolError",
    "ToolExecutionError",
    "ToolName",
    "ToolRegistry",
    "ToolResult",
    "ToolResultStatus",
    "ToolSpec",
    "build_registry",
]
