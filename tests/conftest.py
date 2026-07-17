"""
pytest conftest.py — test muhiti sozlamalari.

Barcha async test funksiyalari uchun event loop va mock
environment variable'lari taqdim etiladi.
DB va Redis ulanishlari unit testlarda mock qilinadi.
"""
from __future__ import annotations

import os
import pytest

# ─── Test uchun .env o'rniga to'g'ridan-to'g'ri environment variable ──────────
# (config.py import qilinishidan AVVAL o'rnatilishi kerak)
os.environ.setdefault("BOT_TOKEN", "0000000000:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
os.environ.setdefault("SUPER_ADMIN_IDS", "999999999")
os.environ.setdefault("LOG_FILE", "/tmp/guardbot_test.log")
os.environ.setdefault("LOG_LEVEL", "WARNING")  # testlarda log shovqinini kamaytirish


# ─── DB mock (unit testlar uchun real DB ulanish shart emas) ─────────────────

@pytest.fixture(autouse=False)
def mock_get_session(monkeypatch):
    """
    DB sessiyasini mock qiladi. DB talab qilmaydigan testlarda ishlatiladi.
    Ishlatish: test funksiyasiga `mock_get_session` fixture'ini qo'shing.
    """
    from unittest.mock import AsyncMock, MagicMock
    from contextlib import asynccontextmanager

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=MagicMock(
        scalar_one_or_none=MagicMock(return_value=None),
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[]))),
        all=MagicMock(return_value=[]),
    ))
    mock_session.add = MagicMock()
    mock_session.flush = AsyncMock()
    mock_session.refresh = AsyncMock()
    mock_session.commit = AsyncMock()
    mock_session.rollback = AsyncMock()

    @asynccontextmanager
    async def _mock_session():
        yield mock_session

    monkeypatch.setattr("database.db.get_session", _mock_session)
    return mock_session


@pytest.fixture(autouse=False)
def mock_redis(monkeypatch):
    """Redis client'ni mock qiladi."""
    from unittest.mock import AsyncMock, MagicMock
    mock = AsyncMock()
    mock.get = AsyncMock(return_value=None)
    mock.set = AsyncMock(return_value=True)
    mock.ping = AsyncMock(return_value=True)
    mock.pipeline = MagicMock(return_value=AsyncMock(
        __aenter__=AsyncMock(return_value=AsyncMock(execute=AsyncMock(return_value=[None, None, 0, None]))),
        __aexit__=AsyncMock(return_value=False),
        zremrangebyscore=AsyncMock(),
        zadd=AsyncMock(),
        zcard=AsyncMock(return_value=0),
        expire=AsyncMock(),
        execute=AsyncMock(return_value=[None, None, 0, None]),
    ))
    monkeypatch.setattr("utils.redis_client.redis_client", mock)
    return mock
