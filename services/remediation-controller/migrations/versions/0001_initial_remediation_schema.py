"""initial remediation-controller schema (Phase 5C, ADR-026)

Revision ID: 0001
Revises:
Create Date: 2026-09-02
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

_STATUS_VALUES = (
    "'PROPOSED','POLICY_EVALUATION','PENDING_APPROVAL','APPROVED','EXECUTING','EXECUTED',"
    "'VERIFYING','BLOCKED','REJECTED','EXPIRED','EXECUTION_FAILED','RECOVERED','RECOVERY_FAILED'"
)
_ACTION_VALUES = "'RESTART_SERVICE','SCALE_SERVICE','ROLL_BACK_DEPLOYMENT','DISABLE_FEATURE_FLAG'"
_RISK_VALUES = "'LOW','MEDIUM','HIGH'"
_TRIGGER_VALUES = "'RCA_RECOMMENDATION','MANUAL'"
_ENV_VALUES = "'development','staging','production'"
_DECISION_VALUES = "'APPROVE','REJECT'"
_ROLE_VALUES = "'OPERATOR','INCIDENT_RESPONDER','ADMINISTRATOR'"
_OUTCOME_VALUES = "'ALLOW','DENY'"


def upgrade() -> None:
    op.create_table(
        "remediations",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("investigation_id", sa.String(40), nullable=True),
        sa.Column("trigger", sa.String(24), nullable=False),
        sa.Column("proposed_by", sa.String(128), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("target_service", sa.String(128), nullable=False),
        sa.Column("target_environment", sa.String(64), nullable=False),
        sa.Column("parameters", _JSON, nullable=False),
        sa.Column("risk_level", sa.String(8), nullable=False),
        sa.Column("source_recommendation", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("expected_effect", sa.Text(), nullable=False),
        sa.Column("evidence_references", _JSON, nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("policy_outcome", sa.String(8), nullable=False),
        sa.Column("policy_version", sa.String(16), nullable=False),
        sa.Column("policy_reason_codes", _JSON, nullable=False),
        sa.Column("policy_decision", _JSON, nullable=False),
        sa.Column("policy_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"status IN ({_STATUS_VALUES})", name="ck_remediations_status"),
        sa.CheckConstraint(f"action_type IN ({_ACTION_VALUES})", name="ck_remediations_action"),
        sa.CheckConstraint(f"risk_level IN ({_RISK_VALUES})", name="ck_remediations_risk"),
        sa.CheckConstraint(f"trigger IN ({_TRIGGER_VALUES})", name="ck_remediations_trigger"),
        sa.CheckConstraint(
            f"target_environment IN ({_ENV_VALUES})", name="ck_remediations_environment"
        ),
        sa.CheckConstraint(
            f"policy_outcome IN ({_OUTCOME_VALUES})", name="ck_remediations_outcome"
        ),
    )
    op.create_index("ix_remediations_incident", "remediations", ["incident_id"])
    op.create_index("ix_remediations_status", "remediations", ["status"])
    op.create_index("ix_remediations_created_at", "remediations", ["created_at"])
    op.create_index(
        "ix_remediations_history_key",
        "remediations",
        ["incident_id", "action_type", "target_service", "target_environment"],
    )

    op.create_table(
        "remediation_approvals",
        sa.Column("id", sa.String(24), primary_key=True),
        sa.Column("remediation_id", sa.String(24), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("approver_identity", sa.String(128), nullable=False),
        sa.Column("approver_role", sa.String(24), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["remediation_id"], ["remediations.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("remediation_id", name="uq_approval_remediation"),
        sa.CheckConstraint(f"decision IN ({_DECISION_VALUES})", name="ck_approval_decision"),
        sa.CheckConstraint(f"approver_role IN ({_ROLE_VALUES})", name="ck_approval_role"),
        sa.CheckConstraint(
            "length(trim(approver_identity)) > 0", name="ck_approval_identity"
        ),
    )
    op.create_index("ix_approvals_remediation", "remediation_approvals", ["remediation_id"])


def downgrade() -> None:
    op.drop_table("remediation_approvals")
    op.drop_table("remediations")
