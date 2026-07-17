"""
Alembic Environment — async SQLAlchemy bilan ishlash uchun sozlangan.

Async migration uchun asyncio.run() + AsyncEngine ishlatiladi.
DATABASE_URL .env fayldan pydantic-settings orqali o'qiladi,
shuning uchun alembic.ini dagi placeholder e'tiborga olinmaydi.
"""
from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# ─── Loyiha ildizini sys.path ga qo'shamiz ────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ─── Modellar va config import ────────────────────────────────────────────────
from database.models import Base          # noqa: E402 — path qo'shilgandan keyin
from config import settings               # noqa: E402

# ─── Alembic Config obyekti ───────────────────────────────────────────────────
config = context.config

# Logging konfiguratsiyasini alembic.ini dan o'qish
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL ni .env dan override qilamiz
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

# AutoGenerate uchun target metadata
target_metadata = Base.metadata


# ─── Offline migration (faqat SQL fayl chiqaradi, DB ulanish kerak emas) ──────

def run_migrations_offline() -> None:
    """
    Haqiqiy DB ulanishsiz SQL skriptini faylga yozadi.
    `alembic upgrade head --sql > migration.sql` uchun foydali.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,          # SQLite bilan ham ishlash uchun
    )
    with context.begin_transaction():
        context.run_migrations()


# ─── Online migration (haqiqiy DB ga ulanib migratsiya qiladi) ────────────────

def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Async engine yaratib, migratsiyalarni ishga tushiradi."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,         # migration uchun connection pool kerak emas
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


# ─── Entry point ──────────────────────────────────────────────────────────────
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
