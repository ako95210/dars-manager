"""Add per-client billing policies and durable impact events.

Revision ID: 20260914_0008
Revises: 20260914_0007
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260914_0008"
down_revision: str | None = "20260914_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "billing_policies",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("monthly_budget_nanos", sa.BigInteger(), nullable=False),
        sa.Column("warning_percent", sa.Integer(), nullable=False),
        sa.Column("approval_threshold_nanos", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(
        "ix_billing_policies_user_id", "billing_policies", ["user_id"], unique=True
    )
    op.create_table(
        "impact_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("project_title", sa.String(length=180), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("kind", sa.String(length=50), nullable=False),
        sa.Column("channel", sa.String(length=80), nullable=False),
        sa.Column("duration_milliseconds", sa.BigInteger(), nullable=False),
        sa.Column("storage_bytes", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_impact_events_user_id", "impact_events", ["user_id"])
    op.create_index("ix_impact_events_project_id", "impact_events", ["project_id"])
    op.create_index("ix_impact_events_job_id", "impact_events", ["job_id"])
    op.create_index("ix_impact_events_kind", "impact_events", ["kind"])
    op.create_index("ix_impact_events_occurred_at", "impact_events", ["occurred_at"])
    op.create_index(
        "ix_impact_events_idempotency_key",
        "impact_events",
        ["idempotency_key"],
        unique=True,
    )
    op.create_index(
        "ix_impact_events_user_occurred",
        "impact_events",
        ["user_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("impact_events")
    op.drop_table("billing_policies")
