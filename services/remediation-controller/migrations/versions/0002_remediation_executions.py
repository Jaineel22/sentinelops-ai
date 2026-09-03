"""remediation_executions table (Phase 5D, ADR-027)

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None

_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")

_ACTION_VALUES = "'RESTART_SERVICE','SCALE_SERVICE','ROLL_BACK_DEPLOYMENT','DISABLE_FEATURE_FLAG'"
_EXECUTOR_VALUES = "'LOCAL_SIMULATION'"
_EXEC_STATUS_VALUES = "'STARTED','SUCCEEDED','FAILED'"


def upgrade() -> None:
    op.create_table(
        "remediation_executions",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("remediation_id", sa.String(24), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("target_service", sa.String(128), nullable=False),
        sa.Column("target_environment", sa.String(64), nullable=False),
        sa.Column("executor_type", sa.String(24), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("dry_run", sa.Boolean(), nullable=False),
        sa.Column("simulated_effect", sa.Text(), nullable=False),
        sa.Column("exec_metadata", _JSON, nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["remediation_id"], ["remediations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("remediation_id", name="uq_execution_remediation"),
        sa.CheckConstraint(f"executor_type IN ({_EXECUTOR_VALUES})", name="ck_execution_executor"),
        sa.CheckConstraint(f"status IN ({_EXEC_STATUS_VALUES})", name="ck_execution_status"),
        sa.CheckConstraint(f"action_type IN ({_ACTION_VALUES})", name="ck_execution_action"),
        sa.CheckConstraint("dry_run = false", name="ck_execution_not_dry_run"),
    )
    op.create_index("ix_executions_remediation", "remediation_executions", ["remediation_id"])


def downgrade() -> None:
    op.drop_table("remediation_executions")
