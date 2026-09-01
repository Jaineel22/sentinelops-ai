"""initial rca-agent schema (ADR-019)

Revision ID: 0001
Revises:
Create Date: 2026-09-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_ACTIVE_PREDICATE = "status NOT IN ('COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','TIMED_OUT')"
_STATUS_VALUES = (
    "'PENDING','PLANNING','COLLECTING_EVIDENCE','ANALYZING','VERIFYING',"
    "'COMPLETED','INSUFFICIENT_EVIDENCE','FAILED','TIMED_OUT'"
)


def upgrade() -> None:
    op.create_table(
        "investigations",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("trigger", sa.String(12), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("model", sa.String(64), nullable=True),
        sa.Column("termination_reason", sa.Text(), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("evidence_count", sa.Integer(), nullable=False),
        sa.Column("overall_confidence", sa.String(8), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_investigations_status"),
        sa.CheckConstraint("trigger IN ('EVENT','MANUAL')", name="ck_investigations_trigger"),
        sa.CheckConstraint("mode IN ('mock','live')", name="ck_investigations_mode"),
    )
    op.create_index("ix_investigations_incident", "investigations", ["incident_id"])
    op.create_index("ix_investigations_status", "investigations", ["status"])
    op.create_index("ix_investigations_created_at", "investigations", ["created_at"])
    # At most one active investigation per incident (idempotent trigger guard).
    op.create_index(
        "uq_investigations_active_incident",
        "investigations",
        ["incident_id"],
        unique=True,
        postgresql_where=sa.text(_ACTIVE_PREDICATE),
        sqlite_where=sa.text(_ACTIVE_PREDICATE),
    )

    op.create_table(
        "investigation_steps",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", sa.String(40), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("phase", sa.String(24), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=True),
        sa.Column("evidence_ids", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("investigation_id", "seq", name="uq_step_seq"),
    )
    op.create_index(
        "ix_steps_investigation", "investigation_steps", ["investigation_id", "seq"]
    )

    op.create_table(
        "evidence_records",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", sa.String(40), nullable=False),
        sa.Column("evidence_id", sa.String(24), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_reference", sa.String(500), nullable=False),
        sa.Column("trust_level", sa.String(24), nullable=False),
        sa.Column("tool_name", sa.String(64), nullable=False),
        sa.Column("service", sa.String(128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content", _JSON, nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("investigation_id", "evidence_id", name="uq_evidence_ref"),
    )
    op.create_index(
        "ix_evidence_records_investigation", "evidence_records", ["investigation_id"]
    )

    op.create_table(
        "rca_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("investigation_id", sa.String(40), nullable=False),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("overall_confidence", sa.String(8), nullable=False),
        sa.Column("root_cause", _JSON, nullable=True),
        sa.Column("contributing_factors", _JSON, nullable=False),
        sa.Column("findings", _JSON, nullable=False),
        sa.Column("hypotheses", _JSON, nullable=False),
        sa.Column("recommended_action", _JSON, nullable=False),
        sa.Column("timeline", _JSON, nullable=False),
        sa.Column("uncertainty", sa.Text(), nullable=False),
        sa.Column("unavailable_evidence_sources", _JSON, nullable=False),
        sa.Column("investigation_metadata", _JSON, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["investigation_id"], ["investigations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("investigation_id", name="uq_report_investigation"),
    )


def downgrade() -> None:
    op.drop_table("rca_reports")
    op.drop_table("evidence_records")
    op.drop_table("investigation_steps")
    op.drop_table("investigations")
