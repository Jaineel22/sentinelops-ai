"""incident relations table (Phase 8 — cross-service correlation)

Adds ``incident_relations``: a directed edge ``incident_id -> related_incident_id``
(dependent -> dependency) with a relation type and a human-readable reason. The
edge is discovered from the static service-dependency graph
(:mod:`incident_correlator.topology`).

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "incident_relations",
        sa.Column("incident_id", sa.String(40), nullable=False),
        sa.Column("related_incident_id", sa.String(40), nullable=False),
        sa.Column("relation_type", sa.String(32), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("incident_id", "related_incident_id"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["related_incident_id"], ["incidents.id"], ondelete="CASCADE"
        ),
        sa.CheckConstraint(
            "incident_id <> related_incident_id", name="ck_incident_relations_no_self"
        ),
    )
    op.create_index(
        "ix_incident_relations_incident_id", "incident_relations", ["incident_id"]
    )
    op.create_index(
        "ix_incident_relations_related_incident_id",
        "incident_relations",
        ["related_incident_id"],
    )


def downgrade() -> None:
    op.drop_table("incident_relations")
