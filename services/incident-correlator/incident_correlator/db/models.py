"""SQLAlchemy ORM models — three tables (ADR-014).

``incidents``              one row per operational incident
``incident_evidence``      the anomaly signals that formed / grew it (event_id UNIQUE => dedup)
``incident_state_history`` an append-only audit of every lifecycle transition

A **partial unique index** on ``incidents(correlation_key) WHERE status <>
'RESOLVED'`` enforces "at most one active incident per service+environment" at
the database level — the backstop against a concurrent-create race (ADR-016).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Float,
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


class Base(DeclarativeBase):
    pass


class IncidentRow(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    correlation_key: Mapped[str] = mapped_column(String(200), nullable=False)
    service: Mapped[str] = mapped_column(String(128), nullable=False)
    environment: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(12), nullable=False)
    severity_reasons: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    anomaly_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    abnormal_signal_names: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    max_anomaly_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_error_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    max_latency_p95_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_evidence_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(64))

    evidence: Mapped[list[EvidenceRow]] = relationship(
        back_populates="incident", cascade="all, delete-orphan", order_by="EvidenceRow.occurred_at"
    )
    history: Mapped[list[StateHistoryRow]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="StateHistoryRow.created_at",
    )

    __table_args__ = (
        Index(
            "uq_incidents_active_key",
            "correlation_key",
            unique=True,
            postgresql_where=text("status <> 'RESOLVED'"),
            sqlite_where=text("status <> 'RESOLVED'"),
        ),
        Index("ix_incidents_service_status", "service", "status"),
        Index("ix_incidents_severity", "severity"),
        Index("ix_incidents_created_at", "created_at"),
        CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','INVESTIGATING','MITIGATING','RESOLVED')",
            name="ck_incidents_status",
        ),
        CheckConstraint(
            "severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_incidents_severity"
        ),
    )


class EvidenceRow(Base):
    __tablename__ = "incident_evidence"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detector: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    anomaly_score: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signals: Mapped[dict[str, float]] = mapped_column(_JSON, nullable=False, default=dict)
    abnormal_signals: Mapped[list[str]] = mapped_column(_JSON, nullable=False, default=list)
    trace_id: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    correlation_reason: Mapped[str] = mapped_column(Text, nullable=False)

    incident: Mapped[IncidentRow] = relationship(back_populates="evidence")

    __table_args__ = (
        UniqueConstraint("event_id", name="uq_evidence_event_id"),
        Index("ix_evidence_incident", "incident_id", "occurred_at"),
    )


class StateHistoryRow(Base):
    __tablename__ = "incident_state_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    severity_at_transition: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    incident: Mapped[IncidentRow] = relationship(back_populates="history")

    __table_args__ = (Index("ix_history_incident", "incident_id", "created_at"),)
