"""Deterministic validation of an LLM-proposed investigation plan (ADR-021).

The model proposes ``PlannedCall``s; this module is the gate. A call is accepted
only if it names a registered, AVAILABLE tool and its arguments pass that tool's
own Pydantic ``request_model`` (which enforces the allow-lists and bounds from
Sub-phase 4B). Everything else is dropped with a human-readable reason that the
graph can feed back to the model for one bounded repair attempt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from rca_agent.llm.base import PlannedCall
from rca_agent.tools import ToolName, ToolRegistry
from rca_agent.tools.names import ToolAvailability


@dataclass(frozen=True)
class ValidatedCall:
    tool_name: ToolName
    arguments: dict[str, object]  # validated + json-normalized


@dataclass
class PlanValidation:
    accepted: list[ValidatedCall] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.accepted)


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()))
    return f"{loc or 'argument'}: {err.get('msg', 'invalid')}"


def validate_plan(
    calls: list[PlannedCall], registry: ToolRegistry, *, max_calls: int
) -> PlanValidation:
    result = PlanValidation()
    seen: set[tuple[str, str]] = set()

    for call in calls:
        if len(result.accepted) >= max_calls:
            result.rejected.append(
                f"{call.tool}: dropped — plan already has the maximum of {max_calls} tool calls"
            )
            continue
        if not registry.has(call.tool):
            result.rejected.append(f"{call.tool!r}: not a registered evidence tool")
            continue

        name = ToolName(call.tool)
        tool = registry.get(name)
        if tool.availability is not ToolAvailability.AVAILABLE:
            result.rejected.append(
                f"{call.tool}: evidence source is not available in this deployment"
            )
            continue

        try:
            request = tool.request_model.model_validate(call.arguments)
        except ValidationError as exc:
            result.rejected.append(f"{call.tool}: invalid arguments ({_first_error(exc)})")
            continue

        args = request.model_dump(mode="json")
        key = (str(name), request.model_dump_json())
        if key in seen:
            result.rejected.append(f"{call.tool}: duplicate call — skipped")
            continue
        seen.add(key)
        result.accepted.append(ValidatedCall(tool_name=name, arguments=args))

    return result
