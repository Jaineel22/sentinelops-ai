"""The evidence-tool base class and its safety wrapper (ADR-020).

Every tool is read-only, has typed input/output, is bounded, and can never raise
to its caller. Subclasses implement ``_run``; the public ``run`` method:

1. refuses immediately if the tool is UNAVAILABLE;
2. validates the raw request against the tool's ``request_model``
   (rejecting invalid / excessive / unexpected-field input *before* any I/O);
3. executes ``_run`` with every exception caught and converted to a structured
   :class:`~rca_agent.tools.results.ToolResult` with a sanitized message.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Mapping
from datetime import datetime
from typing import Any, ClassVar, cast

from pydantic import BaseModel, ValidationError

from rca_agent.domain import EvidenceSourceType, TrustLevel
from rca_agent.schemas import Evidence
from rca_agent.tools.context import ToolContext
from rca_agent.tools.names import ToolAvailability, ToolName
from rca_agent.tools.results import (
    ToolError,
    ToolExecutionError,
    ToolResult,
    ToolResultStatus,
)

logger = logging.getLogger("rca_agent.tools")


class EvidenceTool[ReqT: BaseModel](ABC):
    name: ClassVar[ToolName]
    description: ClassVar[str]
    availability: ClassVar[ToolAvailability]
    request_model: ClassVar[type[BaseModel]]
    # Class invariant, asserted by the security tests — no tool ever writes.
    is_read_only: ClassVar[bool] = True

    @abstractmethod
    async def _run(self, request: ReqT, ctx: ToolContext) -> ToolResult:
        """Execute the validated request. May raise :class:`ToolExecutionError`
        with an already-sanitized message; anything else is caught upstream."""

    async def run(self, raw_request: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        if self.availability is ToolAvailability.UNAVAILABLE:
            return ToolResult.failure(
                self.name,
                ToolResultStatus.SOURCE_UNAVAILABLE,
                f"{self.name} is not available in this deployment "
                "(the backing data source is not deployed)",
                query=dict(raw_request),
            )

        try:
            request = cast(ReqT, self.request_model.model_validate(dict(raw_request)))
        except ValidationError as exc:
            return ToolResult.failure(
                self.name,
                ToolResultStatus.INVALID_INPUT,
                _first_validation_message(exc),
                query=dict(raw_request),
            )

        try:
            return await self._run(request, ctx)
        except ToolExecutionError as exc:
            return ToolResult.failure(
                self.name, exc.code, exc.message, query=request.model_dump(mode="json")
            )
        except Exception:  # never let an exception escape a tool
            logger.exception("unhandled error in tool %s", self.name)
            return ToolResult(
                tool_name=self.name,
                status=ToolResultStatus.UPSTREAM_UNAVAILABLE,
                error=ToolError(
                    code=ToolResultStatus.UPSTREAM_UNAVAILABLE,
                    message="the evidence tool failed unexpectedly",
                    retriable=True,
                ),
                summary=f"{self.name}: unexpected failure",
                query=request.model_dump(mode="json"),
            )


def _first_validation_message(exc: ValidationError) -> str:
    err = exc.errors()[0]
    loc = ".".join(str(p) for p in err.get("loc", ()))
    msg = err.get("msg", "invalid value")
    return f"invalid input at {loc!r}: {msg}"[:500]


def build_evidence(
    ctx: ToolContext,
    *,
    source_type: EvidenceSourceType,
    tool_name: ToolName,
    source_reference: str,
    summary: str,
    content: BaseModel | dict[str, Any],
    service: str | None = None,
    observed_at: datetime | None = None,
    trust_level: TrustLevel = TrustLevel.TRUSTED_SYSTEM,
) -> Evidence:
    """Wrap a normalized payload as an immutable :class:`Evidence` item with a
    deterministic id from ``ctx``. ``content`` is the tool's data — treated as
    untrusted everywhere downstream regardless of ``trust_level``."""

    payload = content.model_dump(mode="json") if isinstance(content, BaseModel) else content
    return Evidence(
        id=ctx.next_evidence_id(),
        source_type=source_type,
        source_reference=source_reference[:500],
        trust_level=trust_level,
        tool_name=str(tool_name),
        service=service,
        summary=summary[:2000],
        content=payload,
        observed_at=observed_at,
        collected_at=ctx.now(),
    )
