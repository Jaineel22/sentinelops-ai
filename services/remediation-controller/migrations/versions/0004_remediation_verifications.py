"""remediation_verifications + recovery audit events (Phase 5F, ADR-029)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision: str | None = "0003"
branch_labels = None
depends_on = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_VERIFICATION_STATUS_VALUES = "'STARTED','RECOVERED','RECOVERY_FAILED'"
_VERIFIER_TYPE_VALUES = "'DETERMINISTIC_LOCAL'"

_AUDIT_EVENT_VALUES_V2 = (
    "'PROPOSAL_CREATED','POLICY_EVALUATED','REMEDIATION_BLOCKED','APPROVED','REJECTED',"
    "'EXECUTION_REQUESTED','EXECUTION_STARTED','EXECUTION_SUCCEEDED','EXECUTION_FAILED',"
    "'VERIFICATION_STARTED','VERIFICATION_SUCCEEDED','VERIFICATION_FAILED'"
)
_AUDIT_EVENT_VALUES_V1 = (
    "'PROPOSAL_CREATED','POLICY_EVALUATED','REMEDIATION_BLOCKED','APPROVED','REJECTED',"
    "'EXECUTION_REQUESTED','EXECUTION_STARTED','EXECUTION_SUCCEEDED','EXECUTION_FAILED'"
)


def upgrade() -> None:
    op.create_table(
        "remediation_verifications",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("remediation_id", sa.String(24), nullable=False),
        sa.Column("execution_id", sa.String(24), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("verifier_type", sa.String(32), nullable=False),
        sa.Column("verifier_version", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("checks", _JSON, nullable=False),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("poll_interval_seconds", sa.Float(), nullable=False),
        sa.Column("ver_metadata", _JSON, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["remediation_id"], ["remediations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("remediation_id", name="uq_verification_remediation"),
        sa.CheckConstraint(
            f"status IN ({_VERIFICATION_STATUS_VALUES})", name="ck_verification_status"
        ),
        sa.CheckConstraint(
            f"verifier_type IN ({_VERIFIER_TYPE_VALUES})", name="ck_verification_verifier"
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_verification_attempts"),
    )
    op.create_index(
        "ix_verifications_remediation", "remediation_verifications", ["remediation_id"]
    )
    op.create_index(
        "ix_verifications_execution", "remediation_verifications", ["execution_id"]
    )

    # Additive nullable column on the append-only audit table (safe — no rewrite).
    op.add_column(
        "remediation_audit_events",
        sa.Column("verification_id", sa.String(24), nullable=True),
    )
    op.create_index(
        "ix_audit_verification", "remediation_audit_events", ["verification_id"]
    )

    # Widen the audit event_type CHECK to accept the three recovery events.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE remediation_audit_events DROP CONSTRAINT ck_audit_event_type")
        op.execute(
            "ALTER TABLE remediation_audit_events ADD CONSTRAINT ck_audit_event_type "
            f"CHECK (event_type IN ({_AUDIT_EVENT_VALUES_V2}))"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE remediation_audit_events DROP CONSTRAINT ck_audit_event_type")
        op.execute(
            "ALTER TABLE remediation_audit_events ADD CONSTRAINT ck_audit_event_type "
            f"CHECK (event_type IN ({_AUDIT_EVENT_VALUES_V1}))"
        )
    op.drop_index("ix_audit_verification", table_name="remediation_audit_events")
    op.drop_column("remediation_audit_events", "verification_id")
    op.drop_table("remediation_verifications")
