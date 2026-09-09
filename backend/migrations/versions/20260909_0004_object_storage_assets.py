"""Add object storage lifecycle metadata to assets and artifacts.

Revision ID: 20260909_0004
Revises: 20260909_0003
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260909_0004"
down_revision: str | None = "20260909_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("storage_key", sa.String(length=700), nullable=True))
    op.add_column(
        "assets",
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
    )
    op.add_column("assets", sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_assets_storage_key", "assets", ["storage_key"], unique=True)
    op.create_index("ix_assets_status", "assets", ["status"], unique=False)
    op.add_column("artifacts", sa.Column("storage_key", sa.String(length=700), nullable=True))


def downgrade() -> None:
    op.drop_column("artifacts", "storage_key")
    op.drop_index("ix_assets_status", table_name="assets")
    op.drop_index("ix_assets_storage_key", table_name="assets")
    op.drop_column("assets", "uploaded_at")
    op.drop_column("assets", "status")
    op.drop_column("assets", "storage_key")
