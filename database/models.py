"""
GuardBot database modellari — MVP v2

Jadvallar:
  users              — kuzatilgan foydalanuvchilar
  channels           — himoyalanayotgan kanallar (alert_chat_id ixtiyoriy)
  protected_groups   — bot admin bo'lgan guruhlar (kuzatuv uchun)
  protected_posts    — original postlar (hash, phash, ocr_text, watermark)
  audit_logs         — barcha harakatlar tarixi
  banned_users       — bloklangan foydalanuvchilar
  whitelist          — ban'dan ozod
  admins             — bot adminlari (RBAC rollari bilan)
  monitored_channels — klon kuzatuv (source → target)
  clone_incidents    — aniqlangan klon hodisalari
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Enum,
    Float, ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ─── Enum turlari ─────────────────────────────────────────────────────────────

class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    MODERATOR   = "moderator"
    VIEWER      = "viewer"


class ActionType(str, enum.Enum):
    SCAN             = "SCAN"
    WARN             = "WARN"
    BAN              = "BAN"
    UNBAN            = "UNBAN"
    WHITELIST_ADD    = "WHITELIST_ADD"
    WHITELIST_REMOVE = "WHITELIST_REMOVE"
    SETTINGS_CHANGE  = "SETTINGS_CHANGE"
    CLONE_DETECTED   = "CLONE_DETECTED"
    GROUP_ADDED      = "GROUP_ADDED"
    CHANNEL_ADDED    = "CHANNEL_ADDED"
    ADMIN_ADDED      = "ADMIN_ADDED"


# ─── Foydalanuvchilar ─────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id:            Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    telegram_id:   Mapped[int]        = mapped_column(BigInteger, unique=True, index=True)
    username:      Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    forward_count: Mapped[int]        = mapped_column(Integer, default=0)
    risk_score:    Mapped[float]      = mapped_column(Float, default=0.0)
    is_banned:     Mapped[bool]       = mapped_column(Boolean, default=False)


# ─── Himoyalangan kanallar ────────────────────────────────────────────────────

class Channel(Base):
    """
    Botni admin qilib qo'shilgan himoyalangan kanallar.
    alert_chat_id — ixtiyoriy; bo'sh bo'lsa bildirishnomalar
    Super Adminlarga va kanal qo'shgan adminga boriladi.
    """
    __tablename__ = "channels"

    id:            Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    chat_id:       Mapped[int]        = mapped_column(BigInteger, unique=True, index=True)
    title:         Mapped[str | None] = mapped_column(String(255), nullable=True)
    username:      Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active:     Mapped[bool]       = mapped_column(Boolean, default=True)
    # Ixtiyoriy — bo'sh bo'lsa auto-detect (super_admins + qo'shgan admin)
    alert_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Kim qo'shdi — bildirishnoma uchun
    added_by:      Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at:      Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)

    posts: Mapped[list["ProtectedPost"]] = relationship(back_populates="channel")


# ─── Himoyalangan guruhlar ────────────────────────────────────────────────────

class ProtectedGroup(Base):
    """
    Bot admin bo'lgan guruhlar.
    Bu guruhlarda forward/media/text leak tekshiruvi avtomatik ishlaydi.
    Alohida "bog'lash" shart emas — bot a'zo bo'lgan BARCHA guruhlar
    kuzatiladi, lekin bu jadval qo'shimcha metadata saqlash uchun kerak.
    """
    __tablename__ = "protected_groups"

    id:        Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    chat_id:   Mapped[int]        = mapped_column(BigInteger, unique=True, index=True)
    title:     Mapped[str | None] = mapped_column(String(255), nullable=True)
    username:  Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool]       = mapped_column(Boolean, default=True)
    added_by:  Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at:  Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    # Bot bu guruhda admin ekanmi (tekshirilgan vaqt)
    bot_is_admin:   Mapped[bool]             = mapped_column(Boolean, default=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    posts: Mapped[list["ProtectedPost"]] = relationship(back_populates="group")


# ─── Himoyalangan postlar ─────────────────────────────────────────────────────

class ProtectedPost(Base):
    """
    Himoyalangan original kontent -- ikki manbadan biridan kelishi mumkin:
      - channel_id  -> Channel'dan (bot admin bo'lgan kanal posti)
      - group_id    -> ProtectedGroup'dan (guruhda a'zo yozgan original xabar)

    source_chat_id -- HAR DOIM to'ldiriladi va manba chatning haqiqiy
    Telegram chat_id'sini saqlaydi. Shu orqali kontent QAYERDA topilishidan
    qat'iy nazar (boshqa guruh, bot-relay va h.k.) darhol ASL manba chatga
    (bot admin bo'lgan joyga) ban yuborish mumkin -- forward meta-ma'lumoti
    guruh xabarlari uchun ko'pincha manba chatni bermaydi (Telegram
    maxfiylik siyosati), shuning uchun o'zimizning ichki xaritamiz kerak.
    """
    __tablename__ = "protected_posts"

    id:              Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    channel_id:      Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)
    group_id:        Mapped[int | None] = mapped_column(ForeignKey("protected_groups.id"), nullable=True)
    source_chat_id:  Mapped[int]        = mapped_column(BigInteger, index=True)
    message_id:      Mapped[int]        = mapped_column(BigInteger)
    content_hash:    Mapped[str]        = mapped_column(String(128), index=True)
    phash:           Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    ocr_text:        Mapped[str | None] = mapped_column(Text, nullable=True)
    text_excerpt:    Mapped[str | None] = mapped_column(Text, nullable=True)
    media_file_id:   Mapped[str | None] = mapped_column(String(255), nullable=True)
    watermark_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    media_analyzed:  Mapped[bool]       = mapped_column(Boolean, default=False)
    created_at:      Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)

    channel: Mapped["Channel"] = relationship(back_populates="posts")
    group:   Mapped["ProtectedGroup"] = relationship(back_populates="posts")


# ─── Audit log ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id:         Mapped[int]          = mapped_column(primary_key=True, autoincrement=True)
    user_id:    Mapped[int | None]   = mapped_column(BigInteger, nullable=True, index=True)
    chat_id:    Mapped[int | None]   = mapped_column(BigInteger, nullable=True)
    action:     Mapped[ActionType]   = mapped_column(Enum(ActionType))
    reason:     Mapped[str | None]   = mapped_column(Text, nullable=True)
    evidence:   Mapped[str | None]   = mapped_column(Text, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime]     = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )


# ─── Ban / Whitelist ──────────────────────────────────────────────────────────

class BannedUser(Base):
    __tablename__ = "banned_users"

    id:        Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    user_id:   Mapped[int]        = mapped_column(BigInteger, index=True)
    chat_id:   Mapped[int]        = mapped_column(BigInteger, index=True)
    reason:    Mapped[str]        = mapped_column(Text)
    evidence:  Mapped[str | None] = mapped_column(Text, nullable=True)
    banned_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    banned_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Whitelist(Base):
    __tablename__ = "whitelist"

    id:       Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    user_id:  Mapped[int]        = mapped_column(BigInteger, unique=True, index=True)
    added_by: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at: Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    note:     Mapped[str | None] = mapped_column(String(255), nullable=True)


# ─── Adminlar ─────────────────────────────────────────────────────────────────

class Admin(Base):
    __tablename__ = "admins"

    id:          Mapped[int]       = mapped_column(primary_key=True, autoincrement=True)
    telegram_id: Mapped[int]       = mapped_column(BigInteger, unique=True, index=True)
    username:    Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name:   Mapped[str | None] = mapped_column(String(255), nullable=True)
    role:        Mapped[AdminRole] = mapped_column(
        Enum(AdminRole), default=AdminRole.VIEWER
    )
    added_by:    Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    added_at:    Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)


# ─── Clone kuzatuv ────────────────────────────────────────────────────────────

class MonitoredChannel(Base):
    """source_chat_id → himoyalangan, target_chat_id → klon shubhali."""
    __tablename__ = "monitored_channels"

    id:              Mapped[int]             = mapped_column(primary_key=True, autoincrement=True)
    source_chat_id:  Mapped[int]             = mapped_column(BigInteger, index=True)
    target_chat_id:  Mapped[int]             = mapped_column(BigInteger, index=True)
    added_by:        Mapped[int | None]      = mapped_column(BigInteger, nullable=True)
    added_at:        Mapped[datetime]        = mapped_column(DateTime, default=datetime.utcnow)
    is_active:       Mapped[bool]            = mapped_column(Boolean, default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    clone_score:     Mapped[float]           = mapped_column(Float, default=0.0)

    incidents: Mapped[list["CloneIncident"]] = relationship(back_populates="monitor")


class CloneIncident(Base):
    __tablename__ = "clone_incidents"

    id:               Mapped[int]        = mapped_column(primary_key=True, autoincrement=True)
    monitor_id:       Mapped[int]        = mapped_column(ForeignKey("monitored_channels.id"))
    offending_msg_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    matched_post_id:  Mapped[int | None] = mapped_column(
        ForeignKey("protected_posts.id"), nullable=True
    )
    similarity_score: Mapped[float]      = mapped_column(Float)
    evidence:         Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_at:      Mapped[datetime]   = mapped_column(DateTime, default=datetime.utcnow)
    resolved:         Mapped[bool]       = mapped_column(Boolean, default=False)

    monitor: Mapped["MonitoredChannel"] = relationship(back_populates="incidents")
