"""Persistence boundary for investigations.

The engine goes through :class:`InvestigationRepository`, never SQLAlchemy
directly. Two implementations, proven equivalent by a test:

* :class:`InMemoryInvestigationRepository` — unit tests.
* ``rca_agent.db.SqlInvestigationRepository`` — PostgreSQL (SQLite in fast tests).

Idempotency for a redelivered trigger is "at most one active investigation per
incident" — the partial unique index in the schema (Sub-phase 4A), mirrored here
in Python. A second attempt while one is active raises
:class:`DuplicateActiveInvestigationError`; the caller decides whether to skip or
return the in-flight one.
"""

from __future__ import annotations

import copy
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from rca_agent.domain import (
    ACTIVE_STATUSES,
    Confidence,
    InvestigationStatus,
    InvestigationTrigger,
)
from rca_agent.schemas import Evidence, Investigation, InvestigationStep, RCAReport


class DuplicateActiveInvestigationError(RuntimeError):
    """An active investigation already exists for this incident."""


def new_investigation_id() -> str:
    return f"rca_{secrets.token_hex(8)}"


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)


class InvestigationRepository(Protocol):
    async def begin_investigation(
        self, incident_id: str, *, trigger: InvestigationTrigger, mode: str
    ) -> Investigation: ...

    async def complete_investigation(
        self,
        investigation_id: str,
        *,
        status: InvestigationStatus,
        termination_reason: str,
        overall_confidence: str,
        model: str | None,
        steps: list[InvestigationStep],
        evidence: list[Evidence],
        report: RCAReport | None,
    ) -> Investigation: ...

    async def get_investigation(self, investigation_id: str) -> Investigation | None: ...

    async def get_active_investigation(self, incident_id: str) -> Investigation | None: ...

    async def get_latest_investigation(self, incident_id: str) -> Investigation | None: ...

    async def get_steps(self, investigation_id: str) -> list[InvestigationStep] | None: ...

    async def get_evidence(self, investigation_id: str) -> list[Evidence] | None: ...

    async def get_report(self, investigation_id: str) -> RCAReport | None: ...


# --- in-memory implementation ------------------------------------------
@dataclass
class _Store:
    investigations: dict[str, Investigation] = field(default_factory=dict)
    steps: dict[str, list[InvestigationStep]] = field(default_factory=dict)
    evidence: dict[str, list[Evidence]] = field(default_factory=dict)
    reports: dict[str, RCAReport] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)


class InMemoryInvestigationRepository:
    def __init__(self) -> None:
        self._store = _Store()

    async def begin_investigation(
        self, incident_id: str, *, trigger: InvestigationTrigger, mode: str
    ) -> Investigation:
        for inv in self._store.investigations.values():
            if inv.incident_id == incident_id and inv.status in ACTIVE_STATUSES:
                raise DuplicateActiveInvestigationError(incident_id)
        now = _utcnow()
        inv = Investigation(
            id=new_investigation_id(),
            incident_id=incident_id,
            status=InvestigationStatus.PENDING,
            trigger=trigger,
            mode=mode,  # type: ignore[arg-type]
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        self._store.investigations[inv.id] = inv
        self._store.order.append(inv.id)
        return copy.deepcopy(inv)

    async def complete_investigation(
        self,
        investigation_id: str,
        *,
        status: InvestigationStatus,
        termination_reason: str,
        overall_confidence: str,
        model: str | None,
        steps: list[InvestigationStep],
        evidence: list[Evidence],
        report: RCAReport | None,
    ) -> Investigation:
        prior = self._store.investigations[investigation_id]
        updated = prior.model_copy(
            update={
                "status": status,
                "termination_reason": termination_reason,
                "overall_confidence": Confidence(overall_confidence),
                "model": model,
                "tool_call_count": sum(1 for s in steps if s.kind == "TOOL_CALL"),
                "step_count": len(steps),
                "evidence_count": len(evidence),
                "completed_at": _utcnow(),
                "updated_at": _utcnow(),
            }
        )
        self._store.investigations[investigation_id] = updated
        self._store.steps[investigation_id] = list(steps)
        self._store.evidence[investigation_id] = list(evidence)
        if report is not None:
            self._store.reports[investigation_id] = report
        return copy.deepcopy(updated)

    async def get_investigation(self, investigation_id: str) -> Investigation | None:
        inv = self._store.investigations.get(investigation_id)
        return copy.deepcopy(inv) if inv else None

    async def get_active_investigation(self, incident_id: str) -> Investigation | None:
        for inv in self._store.investigations.values():
            if inv.incident_id == incident_id and inv.status in ACTIVE_STATUSES:
                return copy.deepcopy(inv)
        return None

    async def get_latest_investigation(self, incident_id: str) -> Investigation | None:
        for inv_id in reversed(self._store.order):
            inv = self._store.investigations[inv_id]
            if inv.incident_id == incident_id:
                return copy.deepcopy(inv)
        return None

    async def get_steps(self, investigation_id: str) -> list[InvestigationStep] | None:
        if investigation_id not in self._store.investigations:
            return None
        return copy.deepcopy(self._store.steps.get(investigation_id, []))

    async def get_evidence(self, investigation_id: str) -> list[Evidence] | None:
        if investigation_id not in self._store.investigations:
            return None
        return copy.deepcopy(self._store.evidence.get(investigation_id, []))

    async def get_report(self, investigation_id: str) -> RCAReport | None:
        return copy.deepcopy(self._store.reports.get(investigation_id))
