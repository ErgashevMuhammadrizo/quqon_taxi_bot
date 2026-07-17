"""
Async SQLAlchemy engine va session factory.
Production uchun connection pool to'liq sozlangan.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from config import settings
from database.models import Base

# ─── Engine (production-grade pool) ──────────────────────────────────────────
# Eslatma: pool_size/max_overflow/pool_timeout/pool_recycle faqat "real"
# connection-pool driverlar (masalan asyncpg) uchun to'g'ri keladi. SQLite
# (dev/test uchun qulay) StaticPool ishlatadi va bu argumentlarni qabul
# qilmaydi — shuning uchun faqat SQLite bo'lmaganda qo'shamiz.
_engine_kwargs: dict = {"echo": False, "pool_pre_ping": True}
if not settings.DATABASE_URL.startswith("sqlite"):
    _engine_kwargs.update(
        pool_size=10,        # doimiy ulanishlar soni
        max_overflow=20,      # qo'shimcha (burstlar uchun) ulanishlar
        pool_timeout=30,      # ulanish olishni kutish vaqti (s)
        pool_recycle=1800,    # 30 daqiqada ulanish yangilanadi (TCP timeout oldini olish)
    )

engine = create_async_engine(settings.DATABASE_URL, **_engine_kwargs)

async_session = async_sessionmaker(
    engine,
    expire_on_commit=False,   # commit'dan keyin obyektlar hali o'qish uchun mavjud
    class_=AsyncSession,
)


async def init_models() -> None:
    """
    Jadvallarni yaratish + mavjud jadvallarga yangi ustunlar qo'shish.
    Dev rejimida ishlatiladi; production'da `alembic upgrade head` tavsiya etiladi.
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # MVP v2 — admins jadvaliga yangi ustunlar (agar yo'q bo'lsa)
        v2_admins_columns = [
            ("username",  "VARCHAR(255)"),
            ("full_name", "VARCHAR(255)"),
            ("added_by",  "BIGINT"),
        ]
        for col_name, col_type in v2_admins_columns:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE admins ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
            except Exception:
                pass  # SQLite yoki boshqa DB da IF NOT EXISTS yo'q — xatoni o'tkazamiz

        # MVP v2 — channels jadvaliga yangi ustunlar
        v2_channels_columns = [
            ("username", "VARCHAR(255)"),
            ("added_by", "BIGINT"),
        ]
        for col_name, col_type in v2_channels_columns:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE channels ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
            except Exception:
                pass

        # MVP v2 — audit_logs ActionType enum ga yangi qiymatlar
        new_enum_values = ["GROUP_ADDED", "CHANNEL_ADDED", "ADMIN_ADDED"]
        for val in new_enum_values:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TYPE actiontype ADD VALUE IF NOT EXISTS '{val}'"
                    )
                )
            except Exception:
                pass  # enum yo'q yoki allaqachon bor

        # ── Security Engine (v3) — users jadvaliga yangi ustunlar ──────────────
        v3_users_columns = [
            ("trust_score",         "FLOAT DEFAULT 100.0"),
            ("warnings",            "INTEGER DEFAULT 0"),
            ("mute_count",          "INTEGER DEFAULT 0"),
            ("ban_count",           "INTEGER DEFAULT 0"),
            ("join_time",           "TIMESTAMP"),
            ("last_active_at",      "TIMESTAMP"),
            ("has_username",        "BOOLEAN DEFAULT FALSE"),
            ("has_profile_photo",   "BOOLEAN DEFAULT FALSE"),
            ("captcha_passed",      "BOOLEAN DEFAULT FALSE"),
            ("is_approved",         "BOOLEAN DEFAULT FALSE"),
            ("suspicious_score",    "FLOAT DEFAULT 0.0"),
            ("groups_joined_count", "INTEGER DEFAULT 0"),
            ("message_count",       "INTEGER DEFAULT 0"),
        ]
        for col_name, col_type in v3_users_columns:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE users ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
            except Exception:
                pass

        # ── Security Engine (v3) — protected_groups jadvaliga sozlama ustunlari
        v3_groups_columns = [
            ("raid_protection_enabled", "BOOLEAN DEFAULT TRUE"),
            ("captcha_enabled",         "BOOLEAN DEFAULT TRUE"),
            ("forward_block_enabled",   "BOOLEAN DEFAULT FALSE"),
            ("link_block_enabled",      "BOOLEAN DEFAULT TRUE"),
            ("media_block_enabled",     "BOOLEAN DEFAULT FALSE"),
            ("ai_detection_enabled",    "BOOLEAN DEFAULT FALSE"),
            ("risk_threshold",          "INTEGER DEFAULT 70"),
            ("trust_threshold",         "INTEGER DEFAULT 40"),
            ("raid_mode_active",        "BOOLEAN DEFAULT FALSE"),
            ("raid_mode_since",         "TIMESTAMP"),
        ]
        for col_name, col_type in v3_groups_columns:
            try:
                await conn.execute(
                    __import__("sqlalchemy").text(
                        f"ALTER TABLE protected_groups ADD COLUMN IF NOT EXISTS {col_name} {col_type}"
                    )
                )
            except Exception:
                pass


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    `async with get_session() as session:` ko'rinishida ishlatiladigan session.
    Xato bo'lsa avtomatik rollback qiladi.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_engine() -> None:
    """Graceful shutdown paytida engine va barcha ulanishlarni yopadi."""
    await engine.dispose()
