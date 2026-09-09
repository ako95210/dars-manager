"""Add worker ownership and lease metadata to jobs.

Revision ID: 20260909_0005
Revises: 20260909_0004
Create Date: 2026-09-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260909_0005"
down_revision: str | None = "20260909_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("execution_backend", sa.String(length=30), server_default="inline", nullable=False),
    )
    op.add_column("jobs", sa.Column("worker_id", sa.String(length=120), nullable=True))
    op.add_column("jobs", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "jobs", sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False)
    )
    op.create_index("ix_jobs_execution_backend", "jobs", ["execution_backend"])
    op.create_index("ix_jobs_worker_id", "jobs", ["worker_id"])
    op.create_index("ix_jobs_lease_expires_at", "jobs", ["lease_expires_at"])


def downgrade() -> None:
    op.drop_index("ix_jobs_lease_expires_at", table_name="jobs")
    op.drop_index("ix_jobs_worker_id", table_name="jobs")
    op.drop_index("ix_jobs_execution_backend", table_name="jobs")
    op.drop_column("jobs", "attempt_count")
    op.drop_column("jobs", "lease_expires_at")
    op.drop_column("jobs", "worker_id")
    op.drop_column("jobs", "execution_backend")
