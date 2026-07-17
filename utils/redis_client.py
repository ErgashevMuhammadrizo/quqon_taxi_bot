"""
Redis ulanishi — rate limiting, cache, behavior tracking uchun.
Connection pool bilan sozlangan; graceful shutdown qo'llab-quvvatlanadi.
"""
from __future__ import annotations

import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool

from config import settings
from utils.logger import logger

# ─── Connection pool ──────────────────────────────────────────────────────────
_pool = ConnectionPool.from_url(
    settings.REDIS_URL,
    decode_responses=True,
    max_connections=50,           # parallel ulanishlar limiti
    socket_connect_timeout=5,     # ulanish timeout (s)
    socket_timeout=5,             # so'rov timeout (s)
    retry_on_timeout=True,        # timeout'da avtomatik qayta urinish
    health_check_interval=30,     # har 30s da ulanish salomatligi tekshiriladi
)

redis_client: aioredis.Redis = aioredis.Redis(connection_pool=_pool)


async def close_redis() -> None:
    """Graceful shutdown paytida chaqiriladi."""
    await redis_client.aclose()
    await _pool.aclose()
    logger.info("Redis ulanishi yopildi.")


async def ping_redis() -> bool:
    """Health check uchun Redis ulanishini tekshiradi."""
    try:
        return await redis_client.ping()
    except Exception as e:
        logger.warning(f"Redis ping muvaffaqiyatsiz: {e}")
        return False
