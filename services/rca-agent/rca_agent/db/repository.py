"""PostgreSQL-backed :class:`~rca_agent.repository.InvestigationRepository`.

``begin_investigation`` is one INSERT guarded by the partial unique index;
``complete_investigation`` is one transaction that updates the investigation row
and inserts its steps, evidence snapshots, and RCA report together. The engine
runs (many LLM calls) *between* those two transactions — a transaction is never
held open across model calls.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from rca_agent.db.engine import Database
from rca_agent.db.models import (
    EvidenceRecordRow,
    InvestigationRow,
    InvestigationStepRow,
    RcaReportRow,
)
from rca_agent.domain import (
    ACTIVE_STATUSES,
    Confidence,
    InvestigationStatus,
    InvestigationTrigger,
)
from rca_agent.repository import DuplicateActiveInvestigationError, new_investigation_id
from rca_agent.schemas import (
    Evidence,
    Finding,
    Hypothesis,
    Investigation,
    InvestigationMetadata,
    InvestigationStep,
    RCAReport,
    RecommendedAction,
    RootCause,
    TimelineEntry,
)

_ACTIVE_SQL = tuple(str(s) for s in ACTIVE_STATUSES)


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _utc_opt(dt: datetime | None) -> datetime | None:
    return _utc(dt) if dt is not None else None


def _row_to_investigation(row: InvestigationRow) -> Investigation:
    return Investigation(
        id=row.id,
        incident_id=row.incident_id,
        status=InvestigationStatus(row.status),
        trigger=InvestigationTrigger(row.trigger),
        mode=row.mode,  # type: ignore[arg-type]
        model=row.model,
        termination_reason=row.termination_reason,
        tool_call_count=row.tool_call_count,
        step_count=row.step_count,
        evidence_count=row.evidence_count,
        overall_confidence=Confidence(row.overall_confidence),
        started_at=_utc_opt(row.started_at),
        completed_at=_utc_opt(row.completed_at),
        created_at=_utc(row.created_at),
        updated_at=_utc(row.updated_at),
    )


def _evidence_to_row(investigation_id: str, ev: Evidence) -> EvidenceRecordRow:
    return EvidenceRecordRow(
        investigation_id=investigation_id,
        evidence_id=ev.id,
        source_type=str(ev.source_type),
        source_reference=ev.source_reference,
        trust_level=str(ev.trust_level),
        tool_name=ev.tool_name,
        service=ev.service,
        summary=ev.summary,
        content=ev.content,
        observed_at=ev.observed_at,
        collected_at=ev.collected_at,
    )


def _row_to_evidence(row: EvidenceRecordRow) -> Evidence:
    return Evidence(
        id=row.evidence_id,
        source_type=row.source_type,  # type: ignore[arg-type]
        source_reference=row.source_reference,
        trust_level=row.trust_level,  # type: ignore[arg-type]
        tool_name=row.tool_name,
        service=row.service,
        summary=row.summary,
        content=dict(row.content),
        observed_at=_utc_opt(row.observed_at),
        collected_at=_utc(row.collected_at),
    )


def _step_to_row(investigation_id: str, step: InvestigationStep) -> InvestigationStepRow:
    return InvestigationStepRow(
        investigation_id=investigation_id,
        seq=step.seq,
        kind=str(step.kind),
        phase=str(step.phase),
        description=step.description,
        tool_name=step.tool_name,
        evidence_ids=list(step.evidence_ids),
        created_at=step.at,
    )


def _row_to_step(row: InvestigationStepRow) -> InvestigationStep:
    return InvestigationStep(
        seq=row.seq,
        kind=row.kind,  # type: ignore[arg-type]
        phase=row.phase,  # type: ignore[arg-type]
        description=row.description,
        tool_name=row.tool_name,
        evidence_ids=list(row.evidence_ids),
        at=_utc(row.created_at),
    )


def _report_to_row(investigation_id: str, report: RCAReport) -> RcaReportRow:
    return RcaReportRow(
        investigation_id=investigation_id,
        incident_id=report.incident_id,
        status=str(report.status),
        summary=report.summary,
        overall_confidence=str(report.overall_confidence),
        root_cause=report.root_cause.model_dump(mode="json") if report.root_cause else None,
        contributing_factors=[f.model_dump(mode="json") for f in report.contributing_factors],
        findings=[f.model_dump(mode="json") for f in report.findings],
        hypotheses=[h.model_dump(mode="json") for h in report.hypotheses],
        recommended_action=report.recommended_action.model_dump(mode="json"),
        timeline=[t.model_dump(mode="json") for t in report.timeline],
        uncertainty=report.uncertainty,
        unavailable_evidence_sources=list(report.unavailable_evidence_sources),
        investigation_metadata=report.investigation_metadata.model_dump(mode="json"),
        created_at=datetime.now(tz=UTC),
    )


def _row_to_report(row: RcaReportRow, evidence: list[Evidence]) -> RCAReport:
    return RCAReport(
        incident_id=row.incident_id,
        investigation_id=row.investigation_id,
        status=row.status,  # type: ignore[arg-type]
        summary=row.summary,
        affected_services=sorted({e.service for e in evidence if e.service}),
        timeline=[TimelineEntry.model_validate(t) for t in row.timeline],
        findings=[Finding.model_validate(f) for f in row.findings],
        hypotheses=[Hypothesis.model_validate(h) for h in row.hypotheses],
        root_cause=RootCause.model_validate(row.root_cause) if row.root_cause else None,
        contributing_factors=[Finding.model_validate(f) for f in row.contributing_factors],
        recommended_action=RecommendedAction.model_validate(row.recommended_action),
        evidence=evidence,
        overall_confidence=Confidence(row.overall_confidence),
        uncertainty=row.uncertainty,
        unavailable_evidence_sources=list(row.unavailable_evidence_sources),
        investigation_metadata=InvestigationMetadata.model_validate(row.investigation_metadata),
    )


class SqlInvestigationRepository:
    def __init__(self, database: Database) -> None:
        self._db = database

    async def begin_investigation(
        self, incident_id: str, *, trigger: InvestigationTrigger, mode: str
    ) -> Investigation:
        now = datetime.now(tz=UTC)
        row = InvestigationRow(
            id=new_investigation_id(),
            incident_id=incident_id,
            status=str(InvestigationStatus.PENDING),
            trigger=str(trigger),
            mode=mode,
            tool_call_count=0,
            step_count=0,
            evidence_count=0,
            overall_confidence=str(Confidence.UNKNOWN),
            started_at=now,
            created_at=now,
            updated_at=now,
        )
        async with self._db.session() as session, session.begin():
            session.add(row)
            try:
                await session.flush()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateActiveInvestigationError(incident_id) from exc
            return _row_to_investigation(row)

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
        async with self._db.session() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id)
            if row is None:  # pragma: no cover - caller always has a real id
                raise LookupError(investigation_id)
            row.status = str(status)
            row.termination_reason = termination_reason
            row.overall_confidence = overall_confidence
            row.model = model
            row.tool_call_count = sum(1 for s in steps if s.kind == "TOOL_CALL")
            row.step_count = len(steps)
            row.evidence_count = len(evidence)
            row.completed_at = datetime.now(tz=UTC)
            row.updated_at = datetime.now(tz=UTC)
            session.add_all([_step_to_row(investigation_id, s) for s in steps])
            session.add_all([_evidence_to_row(investigation_id, e) for e in evidence])
            if report is not None:
                session.add(_report_to_row(investigation_id, report))
            await session.flush()
            return _row_to_investigation(row)

    async def get_investigation(self, investigation_id: str) -> Investigation | None:
        async with self._db.session() as session:
            row = await session.get(InvestigationRow, investigation_id)
            return _row_to_investigation(row) if row is not None else None

    async def get_active_investigation(self, incident_id: str) -> Investigation | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(InvestigationRow)
                .where(
                    InvestigationRow.incident_id == incident_id,
                    InvestigationRow.status.in_(_ACTIVE_SQL),
                )
                .order_by(InvestigationRow.created_at.desc())
                .limit(1)
            )
            return _row_to_investigation(row) if row is not None else None

    async def get_latest_investigation(self, incident_id: str) -> Investigation | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(InvestigationRow)
                .where(InvestigationRow.incident_id == incident_id)
                .order_by(InvestigationRow.created_at.desc())
                .limit(1)
            )
            return _row_to_investigation(row) if row is not None else None

    async def get_steps(self, investigation_id: str) -> list[InvestigationStep] | None:
        async with self._db.session() as session:
            if await session.get(InvestigationRow, investigation_id) is None:
                return None
            rows = (
                await session.scalars(
                    select(InvestigationStepRow)
                    .where(InvestigationStepRow.investigation_id == investigation_id)
                    .order_by(InvestigationStepRow.seq)
                )
            ).all()
            return [_row_to_step(r) for r in rows]

    async def get_evidence(self, investigation_id: str) -> list[Evidence] | None:
        async with self._db.session() as session:
            if await session.get(InvestigationRow, investigation_id) is None:
                return None
            return await self._load_evidence(session, investigation_id)

    async def get_report(self, investigation_id: str) -> RCAReport | None:
        async with self._db.session() as session:
            row = await session.scalar(
                select(RcaReportRow).where(RcaReportRow.investigation_id == investigation_id)
            )
            if row is None:
                return None
            evidence = await self._load_evidence(session, investigation_id)
            return _row_to_report(row, evidence)

    @staticmethod
    async def _load_evidence(session: AsyncSession, investigation_id: str) -> list[Evidence]:
        rows = (
            await session.scalars(
                select(EvidenceRecordRow)
                .where(EvidenceRecordRow.investigation_id == investigation_id)
                .order_by(EvidenceRecordRow.collected_at, EvidenceRecordRow.id)
            )
        ).all()
        return [_row_to_evidence(r) for r in rows]


__all__ = ["SqlInvestigationRepository"]
