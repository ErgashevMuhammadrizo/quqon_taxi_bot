"""
MVP v3 — leak-out-of-group tuzatish: ProtectedPost uchun source_chat_id/group_id

Nima uchun kerak: v2'da ProtectedPost faqat Channel'ga bog'langan edi.
Guruhning o'z (kanal bo'lmagan) original kontentini fingerprint qilib,
kontent QAYERGA sizib chiqishidan qat'iy nazar ASL manba guruhga ban
yuborish uchun bu ustunlar zarur.

Revision ID: 0003_v3
Revises: 0002_v2
Create Date: 2026-07-09 00:00:00
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_v3"
down_revision: Union[str, None] = "0002_v2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── protected_posts: channel_id endi ixtiyoriy, group_id + source_chat_id qo'shiladi ──
    op.alter_column("protected_posts", "channel_id", existing_type=sa.Integer(), nullable=True)

    op.add_column(
        "protected_posts",
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("protected_groups.id"), nullable=True),
    )
    op.add_column(
        "protected_posts",
        sa.Column("source_chat_id", sa.BigInteger(), nullable=True),
    )

    # Mavjud yozuvlar uchun source_chat_id'ni channels.chat_id'dan to'ldiramiz
    op.execute(
        """
        UPDATE protected_posts
        SET source_chat_id = channels.chat_id
        FROM channels
        WHERE protected_posts.channel_id = channels.id
          AND protected_posts.source_chat_id IS NULL
        """
    )

    op.alter_column("protected_posts", "source_chat_id", existing_type=sa.BigInteger(), nullable=False)
    op.create_index(
        "ix_protected_posts_source_chat_id", "protected_posts", ["source_chat_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_protected_posts_source_chat_id", table_name="protected_posts")
    op.drop_column("protected_posts", "source_chat_id")
    op.drop_column("protected_posts", "group_id")
    op.alter_column("protected_posts", "channel_id", existing_type=sa.Integer(), nullable=False)
