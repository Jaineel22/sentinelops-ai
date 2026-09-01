"""Request/response models for the investigation API.

Deliberately thin — the response bodies reuse the Sub-phase 4A domain schemas
(:class:`~rca_agent.schemas.Investigation`, :class:`~rca_agent.schemas.InvestigationStep`,
:class:`~rca_agent.schemas.RCAReport`) verbatim, so the API surface and the
persisted/validated documents can never drift apart.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from rca_agent.schemas import Investigation, InvestigationStep, RCAReport
from rca_agent.tools.contracts import INCIDENT_ID_RE


class CreateInvestigationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(pattern=INCIDENT_ID_RE)


class InvestigationDetail(BaseModel):
    """One investigation: its lifecycle row, the operational trace, and — once
    terminal — the structured RCA report (``None`` while running or on a
    ``FAILED`` / ``TIMED_OUT`` outcome)."""

    investigation: Investigation
    steps: list[InvestigationStep] = Field(default_factory=list)
    report: RCAReport | None = None
