"""Add immutable community contributions and allocations.

Revision ID: 20260914_0010
Revises: 20260914_0009
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260914_0010"
down_revision: str | None = "20260914_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "community_contributions",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("contributor_name", sa.String(length=180), nullable=False),
        sa.Column("is_anonymous", sa.Boolean(), nullable=False),
        sa.Column("amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("reference", sa.String(length=180), nullable=False),
        sa.Column("campaign", sa.String(length=180), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_nanos > 0", name="ck_community_contributions_amount"
        ),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_community_contributions_received",
        "community_contributions",
        ["received_at", "status"],
    )
    op.create_index(
        "ix_community_contributions_received_at",
        "community_contributions",
        ["received_at"],
    )
    op.create_index(
        "ix_community_contributions_recorded_by_user_id",
        "community_contributions",
        ["recorded_by_user_id"],
    )
    op.create_index(
        "ix_community_contributions_status",
        "community_contributions",
        ["status"],
    )

    op.create_table(
        "contribution_allocations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("contribution_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("project_title", sa.String(length=180), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=80), nullable=False),
        sa.Column("amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "amount_nanos > 0", name="ck_contribution_allocations_amount"
        ),
        sa.ForeignKeyConstraint(
            ["contribution_id"], ["community_contributions.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_contribution_allocations_contribution_id",
        "contribution_allocations",
        ["contribution_id"],
    )
    op.create_index(
        "ix_contribution_allocations_project_id",
        "contribution_allocations",
        ["project_id"],
    )
    op.create_index(
        "ix_contribution_allocations_project_period",
        "contribution_allocations",
        ["project_id", "period_start", "period_end"],
    )
    op.create_index(
        "ix_contribution_allocations_period_start",
        "contribution_allocations",
        ["period_start"],
    )
    op.create_index(
        "ix_contribution_allocations_recorded_by_user_id",
        "contribution_allocations",
        ["recorded_by_user_id"],
    )
    op.create_index(
        "ix_contribution_allocations_user_id",
        "contribution_allocations",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_table("contribution_allocations")
    op.drop_table("community_contributions")
