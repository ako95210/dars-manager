"""Add verified email invitations for user accounts.

Revision ID: 20260915_0012
Revises: 20260915_0011
Create Date: 2026-09-15
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "20260915_0012"
down_revision: str | None = "20260915_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("invitation_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET email_verified_at = created_at")
    op.create_index("ix_users_email_verified_at", "users", ["email_verified_at"])
    op.create_table(
        "account_invitations",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_account_invitations_token_hash",
        "account_invitations",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        "ix_account_invitations_user_id", "account_invitations", ["user_id"]
    )
    op.create_index(
        "ix_account_invitations_expires_at",
        "account_invitations",
        ["expires_at"],
    )
    op.create_index(
        "ix_account_invitations_used_at", "account_invitations", ["used_at"]
    )
    op.create_index(
        "ix_account_invitations_created_by_user_id",
        "account_invitations",
        ["created_by_user_id"],
    )


def downgrade() -> None:
    op.drop_table("account_invitations")
    op.drop_index("ix_users_email_verified_at", table_name="users")
    op.drop_column("users", "invitation_sent_at")
    op.drop_column("users", "email_verified_at")
