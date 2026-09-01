"""initial incident schema (ADR-014)

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


def upgrade() -> None:
    op.create_table(
        "incidents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("correlation_key", sa.String(200), nullable=False),
        sa.Column("service", sa.String(128), nullable=False),
        sa.Column("environment", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("severity_reasons", _JSON, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("anomaly_count", sa.Integer(), nullable=False),
        sa.Column("abnormal_signal_names", _JSON, nullable=False),
        sa.Column("max_anomaly_score", sa.Float(), nullable=False),
        sa.Column("max_error_rate", sa.Float(), nullable=False),
        sa.Column("max_latency_p95_ms", sa.Float(), nullable=False),
        sa.Column("detector", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_evidence_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution", sa.String(64), nullable=True),
        sa.CheckConstraint(
            "status IN ('OPEN','ACKNOWLEDGED','INVESTIGATING','MITIGATING','RESOLVED')",
            name="ck_incidents_status",
        ),
        sa.CheckConstraint(
            "severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_incidents_severity"
        ),
    )
    op.create_index("ix_incidents_service_status", "incidents", ["service", "status"])
    op.create_index("ix_incidents_severity", "incidents", ["severity"])
    op.create_index("ix_incidents_created_at", "incidents", ["created_at"])
    # One active incident per correlation_key (ADR-016).
    op.create_index(
        "uq_incidents_active_key",
        "incidents",
        ["correlation_key"],
        unique=True,
        postgresql_where=sa.text("status <> 'RESOLVED'"),
        sqlite_where=sa.text("status <> 'RESOLVED'"),
    )

    op.create_table(
        "incident_evidence",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("event_id", sa.String(64), nullable=False),
        sa.Column("detector", sa.String(64), nullable=False),
        sa.Column("detector_version", sa.String(32), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("signals", _JSON, nullable=False),
        sa.Column("abnormal_signals", _JSON, nullable=False),
        sa.Column("trace_id", sa.String(64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("correlation_reason", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_id", name="uq_evidence_event_id"),
    )
    op.create_index("ix_evidence_incident", "incident_evidence", ["incident_id", "occurred_at"])

    op.create_table(
        "incident_state_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("actor", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("severity_at_transition", sa.String(12), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
    )
    op.create_index("ix_history_incident", "incident_state_history", ["incident_id", "created_at"])


def downgrade() -> None:
    op.drop_table("incident_state_history")
    op.drop_table("incident_evidence")
    op.drop_table("incidents")
