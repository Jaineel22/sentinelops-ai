"""SQLAlchemy ORM models — four tables (ADR-019).

``investigations``        one row per RCA investigation of an incident
``investigation_steps``   append-only operational trace (no chain-of-thought)
``evidence_records``      normalized, immutable snapshots of collected evidence
``rca_reports``           one structured RCA document per investigation

A **partial unique index** on ``investigations(incident_id) WHERE status`` is
non-terminal enforces "at most one active investigation per incident" — the
idempotency guard for a redelivered ``incident.opened`` event (mirrors the
Phase 3 "one active incident per correlation_key" pattern).

These tables live in the same PostgreSQL database as the incident tables but are
migrated by the rca-agent's own Alembic lineage (``alembic_version_rca``).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

_JSON = JSONB().with_variant(JSON(), "sqlite")

_ACTIVE_PREDICATE = "status NOT IN ('COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','TIMED_OUT')"
_STATUS_VALUES = (
    "'PENDING','PLANNING','COLLECTING_EVIDENCE','ANALYZING','VERIFYING',"
    "'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','TIMED_OUT'"
)


class Base(DeclarativeBase):
    pass


class InvestigationRow(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    trigger: Mapped[str] = mapped_column(String(12), nullable=False)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)
    model: Mapped[str | None] = mapped_column(String(64))
    termination_reason: Mapped[str | None] = mapped_column(Text)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    step_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    overall_confidence: Mapped[str] = mapped_column(String(8), nullable=False, default="UNKNOWN")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    steps: Mapped[list[InvestigationStepRow]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="InvestigationStepRow.seq",
    )
    evidence: Mapped[list[EvidenceRecordRow]] = relationship(
        back_populates="investigation",
        cascade="all, delete-orphan",
        order_by="EvidenceRecordRow.collected_at",
    )
    report: Mapped[RcaReportRow | None] = relationship(
        back_populates="investigation", cascade="all, delete-orphan", uselist=False
    )

    __table_args__ = (
        Index(
            "uq_investigations_active_incident",
            "incident_id",
            unique=True,
            postgresql_where=text(_ACTIVE_PREDICATE),
            sqlite_where=text(_ACTIVE_PREDICATE),
        ),
        Index("ix_investigations_incident", "incident_id"),
        Index("ix_investigations_status", "status"),
        Index("ix_investigations_created_at", "created_at"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_investigations_status"),
        CheckConstraint("trigger IN ('EVENT','MANUAL')", name="ck_investigations_trigger"),
        CheckConstraint("mode IN ('mock','live')", name="ck_investigations_mode"),
    )


class InvestigationStepRow(Base):
    __tablename__ = "investigation_steps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    phase: Mapped[str] = mapped_column(String(24), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str | None] = mapped_column(String(64))
    evidence_ids: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("investigation_id", "seq", name="uq_step_seq"),
        Index("ix_steps_investigation", "investigation_id", "seq"),
    )


class EvidenceRecordRow(Base):
    __tablename__ = "evidence_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    evidence_id: Mapped[str] = mapped_column(String(24), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    trust_level: Mapped[str] = mapped_column(String(24), nullable=False)
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    service: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False, default=dict)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint("investigation_id", "evidence_id", name="uq_evidence_ref"),
        Index("ix_evidence_records_investigation", "investigation_id"),
    )


class RcaReportRow(Base):
    __tablename__ = "rca_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False
    )
    incident_id: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    overall_confidence: Mapped[str] = mapped_column(String(8), nullable=False)
    root_cause: Mapped[dict[str, object] | None] = mapped_column(_JSON)
    contributing_factors: Mapped[list[object]] = mapped_column(_JSON, nullable=False, default=list)
    findings: Mapped[list[object]] = mapped_column(_JSON, nullable=False, default=list)
    hypotheses: Mapped[list[object]] = mapped_column(_JSON, nullable=False, default=list)
    recommended_action: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    timeline: Mapped[list[object]] = mapped_column(_JSON, nullable=False, default=list)
    uncertainty: Mapped[str] = mapped_column(Text, nullable=False)
    unavailable_evidence_sources: Mapped[list[str]] = mapped_column(
        _JSON, nullable=False, default=list
    )
    investigation_metadata: Mapped[dict[str, object]] = mapped_column(_JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    investigation: Mapped[InvestigationRow] = relationship(back_populates="report")

    __table_args__ = (UniqueConstraint("investigation_id", name="uq_report_investigation"),)
