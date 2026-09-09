"""Persist the complete job state in PostgreSQL.

Revision ID: 20260909_0003
Revises: 20260909_0002
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260909_0003"
down_revision: str | None = "20260909_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("model_name", sa.String(length=120), server_default="", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("language", sa.String(length=20), server_default="", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("stage", sa.String(length=40), server_default="upload", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("message", sa.Text(), server_default="", nullable=False),
    )
    op.add_column(
        "jobs",
        sa.Column("payload", sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("jobs", "payload")
    op.drop_column("jobs", "message")
    op.drop_column("jobs", "stage")
    op.drop_column("jobs", "language")
    op.drop_column("jobs", "model_name")
