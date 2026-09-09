"""Add media checksums and cumulative storage meters.

Revision ID: 20260909_0006
Revises: 20260909_0005
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260909_0006"
down_revision: str | None = "20260909_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_price_rates_lookup", table_name="price_rates")
    op.create_index(
        "ix_price_rates_lookup",
        "price_rates",
        ["provider", "service", "model", "unit", "effective_from"],
        unique=True,
    )
    for table in ("assets", "artifacts"):
        op.add_column(table, sa.Column("checksum_sha256", sa.String(length=64), nullable=True))
        op.add_column(
            table,
            sa.Column("storage_metered_units", sa.BigInteger(), server_default="0", nullable=False),
        )
        op.add_column(
            table, sa.Column("storage_metered_at", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for table in ("artifacts", "assets"):
        op.drop_column(table, "storage_metered_at")
        op.drop_column(table, "storage_metered_units")
        op.drop_column(table, "checksum_sha256")
    op.drop_index("ix_price_rates_lookup", table_name="price_rates")
    op.create_index(
        "ix_price_rates_lookup",
        "price_rates",
        ["provider", "service", "model", "unit", "effective_from"],
        unique=False,
    )
