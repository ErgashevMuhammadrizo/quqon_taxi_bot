"""Security Engine v3 — trust score, risk analyzer, raid, captcha, audit jadvallari

Revision ID: 0004_v4
Revises: 0003_v3
Create Date: 2026-07-15 00:00:00
"""
from __future__ import annotations
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "0004_v4"
down_revision: Union[str, None] = "0003_v3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users — Security Engine ustunlari ───────────────────────────────────
    op.add_column("users", sa.Column("trust_score", sa.Float(), nullable=False, server_default="100.0"))
    op.add_column("users", sa.Column("warnings", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("mute_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("ban_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("join_time", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("last_active_at", sa.DateTime(), nullable=True))
    op.add_column("users", sa.Column("has_username", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("has_profile_photo", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("captcha_passed", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("is_approved", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("users", sa.Column("suspicious_score", sa.Float(), nullable=False, server_default="0.0"))
    op.add_column("users", sa.Column("groups_joined_count", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("users", sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"))

    # ── protected_groups — har guruh uchun xavfsizlik sozlamalari ───────────
    op.add_column("protected_groups", sa.Column("raid_protection_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("protected_groups", sa.Column("captcha_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("protected_groups", sa.Column("forward_block_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("protected_groups", sa.Column("link_block_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("protected_groups", sa.Column("media_block_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("protected_groups", sa.Column("ai_detection_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("protected_groups", sa.Column("risk_threshold", sa.Integer(), nullable=False, server_default="70"))
    op.add_column("protected_groups", sa.Column("trust_threshold", sa.Integer(), nullable=False, server_default="40"))
    op.add_column("protected_groups", sa.Column("raid_mode_active", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("protected_groups", sa.Column("raid_mode_since", sa.DateTime(), nullable=True))

    # ── risk_history ──────────────────────────────────────────────────────────
    op.create_table(
        "risk_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.Enum(
            "join", "message", "media", "forward", "link", "mention",
            "reaction", "edit", "delete", name="securityactiontype",
        ), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("decision", sa.Enum(
            "ALLOW", "ADMIN_ALERT", "TEMPORARY_RESTRICT", "AUTO_BAN", name="securitydecision",
        ), nullable=False),
        sa.Column("factors", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_risk_history_user_id", "risk_history", ["user_id"])
    op.create_index("ix_risk_history_chat_id", "risk_history", ["chat_id"])
    op.create_index("ix_risk_history_created_at", "risk_history", ["created_at"])

    # ── security_logs ────────────────────────────────────────────────────────
    op.create_table(
        "security_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("event_type", sa.Enum(
            "JOIN", "LEAVE", "BAN", "MUTE", "DELETE", "FORWARD_BLOCK", "LINK_BLOCK",
            "RAID", "SPAM", "CAPTCHA_FAIL", "CAPTCHA_PASS", "ADMIN_ALERT",
            "TEMP_RESTRICT", "TRUST_CHANGE", "RISK_CHANGE", "RAID_MODE_ON", "RAID_MODE_OFF",
            name="securityeventtype",
        ), nullable=False),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_security_logs_chat_id", "security_logs", ["chat_id"])
    op.create_index("ix_security_logs_user_id", "security_logs", ["user_id"])
    op.create_index("ix_security_logs_event_type", "security_logs", ["event_type"])
    op.create_index("ix_security_logs_created_at", "security_logs", ["created_at"])

    # ── captcha_sessions ─────────────────────────────────────────────────────
    op.create_table(
        "captcha_sessions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("captcha_type", sa.Enum(
            "button", "emoji", "math", "sequence", name="captchatype",
        ), nullable=False),
        sa.Column("correct_answer", sa.String(64), nullable=False),
        sa.Column("options", sa.Text(), nullable=True),
        sa.Column("message_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.Enum(
            "pending", "passed", "failed", "expired", name="captchastatus",
        ), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_captcha_sessions_chat_id", "captcha_sessions", ["chat_id"])
    op.create_index("ix_captcha_sessions_user_id", "captcha_sessions", ["user_id"])

    # ── trust_scores (o'zgarish tarixi) ─────────────────────────────────────
    op.create_table(
        "trust_scores",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=True),
        sa.Column("delta", sa.Float(), nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column("old_score", sa.Float(), nullable=False),
        sa.Column("new_score", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_trust_scores_user_id", "trust_scores", ["user_id"])
    op.create_index("ix_trust_scores_chat_id", "trust_scores", ["chat_id"])
    op.create_index("ix_trust_scores_created_at", "trust_scores", ["created_at"])

    # ── raid_logs ────────────────────────────────────────────────────────────
    op.create_table(
        "raid_logs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("join_count", sa.Integer(), nullable=False),
        sa.Column("joined_user_ids", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("ended_by", sa.BigInteger(), nullable=True),
    )
    op.create_index("ix_raid_logs_chat_id", "raid_logs", ["chat_id"])


def downgrade() -> None:
    op.drop_table("raid_logs")
    op.drop_table("trust_scores")
    op.drop_table("captcha_sessions")
    op.drop_table("security_logs")
    op.drop_table("risk_history")

    for col in (
        "raid_mode_since", "raid_mode_active", "trust_threshold", "risk_threshold",
        "ai_detection_enabled", "media_block_enabled", "link_block_enabled",
        "forward_block_enabled", "captcha_enabled", "raid_protection_enabled",
    ):
        op.drop_column("protected_groups", col)

    for col in (
        "message_count", "groups_joined_count", "suspicious_score", "is_approved",
        "captcha_passed", "has_profile_photo", "has_username", "last_active_at",
        "join_time", "ban_count", "mute_count", "warnings", "trust_score",
    ):
        op.drop_column("users", col)

    for enum_name in ("captchastatus", "captchatype", "securityeventtype", "securitydecision", "securityactiontype"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
