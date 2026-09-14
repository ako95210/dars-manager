"""Add immutable provider invoice reconciliations.

Revision ID: 20260914_0009
Revises: 20260914_0008
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260914_0009"
down_revision: str | None = "20260914_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "provider_invoices",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=80), nullable=False),
        sa.Column("service", sa.String(length=80), nullable=False),
        sa.Column("reference", sa.String(length=180), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("invoiced_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("internal_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("variance_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("tolerance_amount_nanos", sa.BigInteger(), nullable=False),
        sa.Column("include_estimated", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("recorded_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["recorded_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider", "service", "reference", name="uq_provider_invoice_reference"
        ),
    )
    op.create_index("ix_provider_invoices_provider", "provider_invoices", ["provider"])
    op.create_index("ix_provider_invoices_period_start", "provider_invoices", ["period_start"])
    op.create_index("ix_provider_invoices_status", "provider_invoices", ["status"])
    op.create_index(
        "ix_provider_invoices_recorded_by_user_id",
        "provider_invoices",
        ["recorded_by_user_id"],
    )
    op.create_index(
        "ix_provider_invoices_period",
        "provider_invoices",
        ["period_start", "period_end"],
    )


def downgrade() -> None:
    op.drop_table("provider_invoices")
