"""rca-agent persistence schema (ADR-019).

Runs the real SQLAlchemy models against file SQLite. PostgreSQL-specific
behaviour (true concurrency) is covered by ``-m integration`` tests later.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from rca_agent.db import Database
from rca_agent.db.models import (
    EvidenceRecordRow,
    InvestigationRow,
    InvestigationStepRow,
    RcaReportRow,
)

_NOW = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)


def _investigation(inv_id: str, incident_id: str, status: str) -> InvestigationRow:
    return InvestigationRow(
        id=inv_id,
        incident_id=incident_id,
        status=status,
        trigger="EVENT",
        mode="mock",
        tool_call_count=0,
        step_count=0,
        evidence_count=0,
        overall_confidence="UNKNOWN",
        created_at=_NOW,
        updated_at=_NOW,
    )


async def test_create_all_and_round_trip(sqlite_rca_db: Database) -> None:
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_1", "inc_1", "PENDING"))
    async with sqlite_rca_db.session() as session:
        row = await session.get(InvestigationRow, "rca_1")
        assert row is not None
        assert row.incident_id == "inc_1"
        assert row.status == "PENDING"


async def test_one_active_investigation_per_incident(sqlite_rca_db: Database) -> None:
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_a", "inc_dup", "COLLECTING_EVIDENCE"))
    with pytest.raises(IntegrityError):
        async with sqlite_rca_db.session() as session, session.begin():
            session.add(_investigation("rca_b", "inc_dup", "PLANNING"))


async def test_new_investigation_allowed_once_prior_is_terminal(
    sqlite_rca_db: Database,
) -> None:
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_done", "inc_x", "COMPLETED"))
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_new", "inc_x", "PENDING"))
    async with sqlite_rca_db.session() as session:
        rows = (
            await session.scalars(
                select(InvestigationRow).where(InvestigationRow.incident_id == "inc_x")
            )
        ).all()
        assert {r.id for r in rows} == {"rca_done", "rca_new"}


async def test_status_check_constraint(sqlite_rca_db: Database) -> None:
    with pytest.raises(IntegrityError):
        async with sqlite_rca_db.session() as session, session.begin():
            session.add(_investigation("rca_bad", "inc_bad", "BOGUS"))


async def test_step_seq_is_unique_per_investigation(sqlite_rca_db: Database) -> None:
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_s", "inc_s", "COLLECTING_EVIDENCE"))
        session.add(
            InvestigationStepRow(
                investigation_id="rca_s",
                seq=1,
                kind="PLAN",
                phase="PLANNING",
                description="planned",
                evidence_ids=[],
                created_at=_NOW,
            )
        )
    with pytest.raises(IntegrityError):
        async with sqlite_rca_db.session() as session, session.begin():
            session.add(
                InvestigationStepRow(
                    investigation_id="rca_s",
                    seq=1,
                    kind="TOOL_CALL",
                    phase="COLLECTING_EVIDENCE",
                    description="dup seq",
                    evidence_ids=[],
                    created_at=_NOW,
                )
            )


async def test_cascade_delete_removes_children(sqlite_rca_db: Database) -> None:
    async with sqlite_rca_db.session() as session, session.begin():
        session.add(_investigation("rca_c", "inc_c", "COMPLETED"))
        session.add(
            InvestigationStepRow(
                investigation_id="rca_c",
                seq=1,
                kind="RCA",
                phase="COMPLETED",
                description="done",
                evidence_ids=[],
                created_at=_NOW,
            )
        )
        session.add(
            EvidenceRecordRow(
                investigation_id="rca_c",
                evidence_id="ev_001",
                source_type="metric",
                source_reference="metric:orders-service/x",
                trust_level="TRUSTED_SYSTEM",
                tool_name="get_service_metrics",
                summary="s",
                content={},
                collected_at=_NOW,
            )
        )
        session.add(
            RcaReportRow(
                investigation_id="rca_c",
                incident_id="inc_c",
                status="COMPLETED",
                summary="s",
                overall_confidence="MEDIUM",
                root_cause=None,
                contributing_factors=[],
                findings=[],
                hypotheses=[],
                recommended_action={"action_type": "MONITOR"},
                timeline=[],
                uncertainty="x",
                unavailable_evidence_sources=[],
                investigation_metadata={},
                created_at=_NOW,
            )
        )

    async with sqlite_rca_db.session() as session, session.begin():
        row = await session.scalar(
            select(InvestigationRow)
            .where(InvestigationRow.id == "rca_c")
            .options(
                selectinload(InvestigationRow.steps),
                selectinload(InvestigationRow.evidence),
                selectinload(InvestigationRow.report),
            )
        )
        assert row is not None
        await session.delete(row)  # ORM-level delete-orphan cascade

    async with sqlite_rca_db.session() as session:
        assert (await session.scalars(select(InvestigationStepRow))).all() == []
        assert (await session.scalars(select(EvidenceRecordRow))).all() == []
        assert (await session.scalars(select(RcaReportRow))).all() == []
