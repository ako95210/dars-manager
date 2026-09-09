"""Add roles and the financial ledger.

Revision ID: 20260909_0002
Revises: 20260908_0001
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260909_0002"
down_revision: str | None = "20260908_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=30), server_default="client", nullable=False),
    )
    op.create_index("ix_users_role", "users", ["role"])

    op.create_table(
        "price_rates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("service", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("unit_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source_url", sa.String(length=500), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_price_rates_effective_from", "price_rates", ["effective_from"])
    op.create_index(
        "ix_price_rates_lookup",
        "price_rates",
        ["provider", "service", "model", "unit", "effective_from"],
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("project_id", sa.String(length=32), nullable=True),
        sa.Column("project_title", sa.String(length=180), nullable=False),
        sa.Column("job_id", sa.String(length=32), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("service", sa.String(length=80), nullable=False),
        sa.Column("model", sa.String(length=120), nullable=False),
        sa.Column("quantity", sa.BigInteger(), nullable=False),
        sa.Column("unit", sa.String(length=40), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("price_rate_id", sa.String(length=32), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("provider_request_id", sa.String(length=255), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["price_rate_id"], ["price_rates.id"]),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_idempotency_key", "usage_events", ["idempotency_key"], unique=True)
    op.create_index("ix_usage_events_job_id", "usage_events", ["job_id"])
    op.create_index("ix_usage_events_occurred_at", "usage_events", ["occurred_at"])
    op.create_index("ix_usage_events_price_rate_id", "usage_events", ["price_rate_id"])
    op.create_index("ix_usage_events_project_id", "usage_events", ["project_id"])
    op.create_index("ix_usage_events_project_occurred", "usage_events", ["project_id", "occurred_at"])
    op.create_index("ix_usage_events_provider_request_id", "usage_events", ["provider_request_id"])
    op.create_index("ix_usage_events_status", "usage_events", ["status"])
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_user_occurred", "usage_events", ["user_id", "occurred_at"])

    op.create_table(
        "payments",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=50), nullable=False),
        sa.Column("reference", sa.String(length=180), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_payments_paid_at", "payments", ["paid_at"])
    op.create_index("ix_payments_period_start", "payments", ["period_start"])
    op.create_index("ix_payments_recorded_by_user_id", "payments", ["recorded_by_user_id"])
    op.create_index("ix_payments_user_id", "payments", ["user_id"])
    op.create_index("ix_payments_user_paid", "payments", ["user_id", "paid_at"])

    op.create_table(
        "billing_statements",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("usage_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("payment_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("balance_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_billing_statements_status", "billing_statements", ["status"])
    op.create_index("ix_billing_statements_user_id", "billing_statements", ["user_id"])
    op.create_index(
        "ix_billing_statements_user_period",
        "billing_statements",
        ["user_id", "period_start", "period_end"],
    )


def downgrade() -> None:
    op.drop_table("billing_statements")
    op.drop_table("payments")
    op.drop_table("usage_events")
    op.drop_table("price_rates")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_column("users", "role")
