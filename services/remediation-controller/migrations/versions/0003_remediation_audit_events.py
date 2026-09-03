"""remediation_audit_events append-only table (Phase 5E, ADR-028)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_AUDIT_EVENT_VALUES = (
    "'PROPOSAL_CREATED','POLICY_EVALUATED','REMEDIATION_BLOCKED','APPROVED','REJECTED',"
    "'EXECUTION_REQUESTED','EXECUTION_STARTED','EXECUTION_SUCCEEDED','EXECUTION_FAILED'"
)
_ACTOR_TYPE_VALUES = "'SYSTEM','HUMAN'"
_EXEC_MODE_VALUES = "'REAL'"

# On PostgreSQL, enforce append-only at the database level: any UPDATE or DELETE
# on an audit row raises. SQLite (fast tests) has no equivalent; the repository
# has no update/delete path in either case.
_APPEND_ONLY_FN = """
CREATE OR REPLACE FUNCTION remediation_audit_events_append_only()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'remediation_audit_events is append-only (%.% rejected)',
        TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;
"""

_APPEND_ONLY_TRIGGER = """
CREATE TRIGGER trg_remediation_audit_events_append_only
BEFORE UPDATE OR DELETE ON remediation_audit_events
FOR EACH ROW EXECUTE FUNCTION remediation_audit_events_append_only();
"""


def upgrade() -> None:
    op.create_table(
        "remediation_audit_events",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), primary_key=True),
        sa.Column("audit_id", sa.String(24), nullable=False),
        sa.Column("remediation_id", sa.String(24), nullable=False),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("investigation_id", sa.String(40), nullable=True),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_role", sa.String(24), nullable=True),
        sa.Column("previous_state", sa.String(24), nullable=True),
        sa.Column("new_state", sa.String(24), nullable=True),
        sa.Column("action_type", sa.String(32), nullable=True),
        sa.Column("target_service", sa.String(128), nullable=True),
        sa.Column("target_environment", sa.String(64), nullable=True),
        sa.Column("policy_outcome", sa.String(8), nullable=True),
        sa.Column("policy_version", sa.String(16), nullable=True),
        sa.Column("policy_reason_codes", _JSON, nullable=False),
        sa.Column("execution_id", sa.String(24), nullable=True),
        sa.Column("execution_mode", sa.String(16), nullable=True),
        sa.Column("execution_result", sa.String(12), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("event_metadata", _JSON, nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["remediation_id"], ["remediations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("audit_id", name="uq_audit_audit_id"),
        sa.CheckConstraint(f"event_type IN ({_AUDIT_EVENT_VALUES})", name="ck_audit_event_type"),
        sa.CheckConstraint(f"actor_type IN ({_ACTOR_TYPE_VALUES})", name="ck_audit_actor_type"),
        sa.CheckConstraint(
            f"execution_mode IS NULL OR execution_mode IN ({_EXEC_MODE_VALUES})",
            name="ck_audit_execution_mode",
        ),
        sa.CheckConstraint("length(trim(actor_id)) > 0", name="ck_audit_actor_id"),
    )
    op.create_index(
        "ix_audit_remediation_seq", "remediation_audit_events", ["remediation_id", "seq"]
    )
    op.create_index("ix_audit_incident_seq", "remediation_audit_events", ["incident_id", "seq"])
    op.create_index("ix_audit_execution", "remediation_audit_events", ["execution_id"])
    op.create_index("ix_audit_occurred_at", "remediation_audit_events", ["occurred_at"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute(_APPEND_ONLY_FN)
        op.execute(_APPEND_ONLY_TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS trg_remediation_audit_events_append_only "
            "ON remediation_audit_events"
        )
        op.execute("DROP FUNCTION IF EXISTS remediation_audit_events_append_only()")
    op.drop_table("remediation_audit_events")
