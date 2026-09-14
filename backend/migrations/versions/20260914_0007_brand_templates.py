"""Add versioned image and video brand templates.

Revision ID: 20260914_0007
Revises: 20260909_0006
Create Date: 2026-09-14
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260914_0007"
down_revision: str | None = "20260909_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "brand_templates",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("brand_kit_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("source_kind", sa.String(length=20), nullable=False),
        sa.Column("usage_mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_milliseconds", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("settings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["brand_kit_id"], ["brand_kits.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_brand_templates_brand_kit_id", "brand_templates", ["brand_kit_id"])
    op.create_index("ix_brand_templates_user_id", "brand_templates", ["user_id"])
    op.create_index(
        "ix_brand_templates_user_updated",
        "brand_templates",
        ["user_id", "updated_at"],
    )

    op.create_table(
        "brand_template_files",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("template_id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=700), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=64), nullable=True),
        sa.Column("storage_metered_units", sa.BigInteger(), server_default="0", nullable=False),
        sa.Column("storage_metered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["template_id"], ["brand_templates.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
        sa.UniqueConstraint("template_id", "kind", name="uq_brand_template_files_kind"),
    )
    op.create_index(
        "ix_brand_template_files_template_id", "brand_template_files", ["template_id"]
    )
    op.create_index("ix_brand_template_files_user_id", "brand_template_files", ["user_id"])
    op.create_index(
        "ix_brand_template_files_storage_key",
        "brand_template_files",
        ["storage_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_table("brand_template_files")
    op.drop_table("brand_templates")
