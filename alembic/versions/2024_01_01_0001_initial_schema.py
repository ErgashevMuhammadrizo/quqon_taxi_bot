"""Initial schema — barcha jadvallar

Revision ID: 0001_initial
Revises:
Create Date: 2024-01-01 00:00:00
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id",            sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("telegram_id",   sa.BigInteger(), nullable=False),
        sa.Column("username",      sa.String(255),  nullable=True),
        sa.Column("full_name",     sa.String(255),  nullable=True),
        sa.Column("first_seen_at", sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("forward_count", sa.Integer(),    nullable=False, server_default="0"),
        sa.Column("risk_score",    sa.Float(),      nullable=False, server_default="0.0"),
        sa.Column("is_banned",     sa.Boolean(),    nullable=False, server_default="false"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    # ── channels ──────────────────────────────────────────────────────────────
    op.create_table(
        "channels",
        sa.Column("id",        sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("chat_id",   sa.BigInteger(), nullable=False),
        sa.Column("title",     sa.String(255),  nullable=True),
        sa.Column("is_active", sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("added_at",  sa.DateTime(),   nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_channels_chat_id", "channels", ["chat_id"], unique=True)

    # ── protected_posts ───────────────────────────────────────────────────────
    op.create_table(
        "protected_posts",
        sa.Column("id",              sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("channel_id",      sa.Integer(),    sa.ForeignKey("channels.id"), nullable=False),
        sa.Column("message_id",      sa.BigInteger(), nullable=False),
        sa.Column("content_hash",    sa.String(128),  nullable=False),
        sa.Column("phash",           sa.String(64),   nullable=True),
        sa.Column("ocr_text",        sa.Text(),       nullable=True),
        sa.Column("text_excerpt",    sa.Text(),       nullable=True),
        sa.Column("media_file_id",   sa.String(255),  nullable=True),
        sa.Column("watermark_token", sa.String(64),   nullable=True),
        sa.Column("media_analyzed",  sa.Boolean(),    nullable=False, server_default="false"),
        sa.Column("created_at",      sa.DateTime(),   nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_protected_posts_content_hash", "protected_posts", ["content_hash"])
    op.create_index("ix_protected_posts_phash",        "protected_posts", ["phash"])

    # ── audit_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id",         sa.Integer(),  primary_key=True, autoincrement=True),
        sa.Column("user_id",    sa.BigInteger(), nullable=True),
        sa.Column("chat_id",    sa.BigInteger(), nullable=True),
        sa.Column("action",     sa.Enum(
            "SCAN", "WARN", "BAN", "UNBAN",
            "WHITELIST_ADD", "WHITELIST_REMOVE", "SETTINGS_CHANGE", "CLONE_DETECTED",
            name="actiontype",
        ), nullable=False),
        sa.Column("reason",     sa.Text(),   nullable=True),
        sa.Column("evidence",   sa.Text(),   nullable=True),
        sa.Column("risk_score", sa.Float(),  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_audit_logs_user_id",    "audit_logs", ["user_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    # ── banned_users ──────────────────────────────────────────────────────────
    op.create_table(
        "banned_users",
        sa.Column("id",        sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("user_id",   sa.BigInteger(), nullable=False),
        sa.Column("chat_id",   sa.BigInteger(), nullable=False),
        sa.Column("reason",    sa.Text(),       nullable=False),
        sa.Column("evidence",  sa.Text(),       nullable=True),
        sa.Column("banned_at", sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("banned_by", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_banned_users_user_id", "banned_users", ["user_id"])
    op.create_index("ix_banned_users_chat_id", "banned_users", ["chat_id"])

    # ── whitelist ─────────────────────────────────────────────────────────────
    op.create_table(
        "whitelist",
        sa.Column("id",       sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("user_id",  sa.BigInteger(), nullable=False),
        sa.Column("added_by", sa.BigInteger(), nullable=True),
        sa.Column("added_at", sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("note",     sa.String(255),  nullable=True),
    )
    op.create_index("ix_whitelist_user_id", "whitelist", ["user_id"], unique=True)

    # ── admins ────────────────────────────────────────────────────────────────
    op.create_table(
        "admins",
        sa.Column("id",          sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("role",        sa.Enum(
            "super_admin", "moderator", "viewer", name="adminrole",
        ), nullable=False, server_default="viewer"),
        sa.Column("added_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_admins_telegram_id", "admins", ["telegram_id"], unique=True)

    # ── monitored_channels ────────────────────────────────────────────────────
    op.create_table(
        "monitored_channels",
        sa.Column("id",              sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("source_chat_id",  sa.BigInteger(), nullable=False),
        sa.Column("target_chat_id",  sa.BigInteger(), nullable=False),
        sa.Column("added_by",        sa.BigInteger(), nullable=True),
        sa.Column("added_at",        sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("is_active",       sa.Boolean(),    nullable=False, server_default="true"),
        sa.Column("last_checked_at", sa.DateTime(),   nullable=True),
        sa.Column("clone_score",     sa.Float(),      nullable=False, server_default="0.0"),
    )
    op.create_index("ix_monitored_source", "monitored_channels", ["source_chat_id"])
    op.create_index("ix_monitored_target", "monitored_channels", ["target_chat_id"])

    # ── clone_incidents ───────────────────────────────────────────────────────
    op.create_table(
        "clone_incidents",
        sa.Column("id",               sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column("monitor_id",       sa.Integer(),    sa.ForeignKey("monitored_channels.id")),
        sa.Column("offending_msg_id", sa.BigInteger(), nullable=True),
        sa.Column("matched_post_id",  sa.Integer(),    sa.ForeignKey("protected_posts.id"), nullable=True),
        sa.Column("similarity_score", sa.Float(),      nullable=False),
        sa.Column("evidence",         sa.Text(),       nullable=True),
        sa.Column("reported_at",      sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("resolved",         sa.Boolean(),    nullable=False, server_default="false"),
    )
    op.create_index("ix_clone_incidents_monitor", "clone_incidents", ["monitor_id"])


def downgrade() -> None:
    op.drop_table("clone_incidents")
    op.drop_table("monitored_channels")
    op.drop_table("admins")
    op.drop_table("whitelist")
    op.drop_table("banned_users")
    op.drop_table("audit_logs")
    op.drop_table("protected_posts")
    op.drop_table("channels")
    op.drop_table("users")
    op.execute("DROP TYPE IF EXISTS actiontype")
    op.execute("DROP TYPE IF EXISTS adminrole")
