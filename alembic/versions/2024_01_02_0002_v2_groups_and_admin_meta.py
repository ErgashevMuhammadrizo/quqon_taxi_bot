"""
MVP v2 — protected_groups, admin metadata, channel/audit enhancements

Revision ID: 0002_v2
Revises: 0001_initial
Create Date: 2024-01-02 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_v2"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── protected_groups (yangi jadval) ──────────────────────────────────────
    op.create_table(
        "protected_groups",
        sa.Column("id",              sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("chat_id",         sa.BigInteger(), nullable=False),
        sa.Column("title",           sa.String(255),  nullable=True),
        sa.Column("username",        sa.String(255),  nullable=True),
        sa.Column("is_active",       sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("added_by",        sa.BigInteger(), nullable=True),
        sa.Column("added_at",        sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("bot_is_admin",    sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("last_checked_at", sa.DateTime(),   nullable=True),
    )
    op.create_index("ix_protected_groups_chat_id", "protected_groups", ["chat_id"], unique=True)

    # ── channels — yangi ustunlar ─────────────────────────────────────────────
    op.add_column("channels", sa.Column("username", sa.String(255), nullable=True))
    op.add_column("channels", sa.Column("added_by", sa.BigInteger(), nullable=True))

    # ── admins — username, full_name, added_by qo'shish ──────────────────────
    op.add_column("admins", sa.Column("username",  sa.String(255), nullable=True))
    op.add_column("admins", sa.Column("full_name", sa.String(255), nullable=True))
    op.add_column("admins", sa.Column("added_by",  sa.BigInteger(), nullable=True))

    # ── audit_logs — yangi action turlari ─────────────────────────────────────
    # PostgreSQL Enum ga yangi qiymatlar qo'shish
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'GROUP_ADDED'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'CHANNEL_ADDED'")
    op.execute("ALTER TYPE actiontype ADD VALUE IF NOT EXISTS 'ADMIN_ADDED'")


def downgrade() -> None:
    op.drop_index("ix_protected_groups_chat_id", table_name="protected_groups")
    op.drop_table("protected_groups")

    op.drop_column("channels", "username")
    op.drop_column("channels", "added_by")

    op.drop_column("admins", "username")
    op.drop_column("admins", "full_name")
    op.drop_column("admins", "added_by")
    # Enum qiymatlarini PostgreSQL da olib tashlash murakkab — downgrade'da o'tkazib yuboriladi
