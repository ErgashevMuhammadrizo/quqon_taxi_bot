"""
Redis ulanishi — rate limiting, cache, behavior tracking uchun.
Redis ishlamasa yoki o'rnatilmagan bo'lsa FakeRedis (xotiradagi stub) ishlatiladi.
Bot Redis-siz ham to'liq ishlayveradi — faqat rate-limit va raid-detect o'chadi.
"""
from __future__ import annotations

import time
from typing import Any

from utils.logger import logger


# ═══════════════════════════════════════════════════════════════════════════════
#  FakeRedis — Redis mavjud bo'lmasa xotirada ishlovchi stub
# ═══════════════════════════════════════════════════════════════════════════════

class _FakePipeline:
    """FakeRedis uchun pipeline stub — hamma metodlar no-op."""
    def __init__(self, store: dict):
        self._store = store
        self._cmds: list = []

    def zremrangebyscore(self, key, mn, mx):
        self._cmds.append(("zremrangebyscore", key, mn, mx))
        return self

    def zadd(self, key, mapping):
        self._cmds.append(("zadd", key, mapping))
        return self

    def zcard(self, key):
        self._cmds.append(("zcard", key))
        return self

    def expire(self, key, seconds):
        self._cmds.append(("expire", key, seconds))
        return self

    def incr(self, key):
        self._cmds.append(("incr", key))
        return self

    def set(self, key, value, ex=None):
        self._cmds.append(("set", key, value, ex))
        return self

    async def execute(self):
        results = []
        now = time.time()
        for cmd in self._cmds:
            op = cmd[0]
            if op == "zremrangebyscore":
                key = cmd[1]
                mn, mx = cmd[2], cmd[3]
                s = self._store.get(key, {})
                self._store[key] = {k: v for k, v in s.items() if not (mn <= v <= mx)}
                results.append(None)
            elif op == "zadd":
                key = cmd[1]
                mapping = cmd[2]
                s = self._store.setdefault(key, {})
                s.update(mapping)
                results.append(len(mapping))
            elif op == "zcard":
                key = cmd[1]
                results.append(len(self._store.get(key, {})))
            elif op == "expire":
                results.append(True)
            elif op == "incr":
                key = cmd[1]
                val = int(self._store.get(key, 0)) + 1
                self._store[key] = val
                results.append(val)
            elif op == "set":
                key, value = cmd[1], cmd[2]
                self._store[key] = value
                results.append(True)
            else:
                results.append(None)
        return results


class FakeRedis:
    """
    Xotiradagi Redis stub.
    Barcha metodlar xatosiz ishlaydi, lekin ma'lumotlar restart bo'lsa yo'qoladi.
    """
    def __init__(self):
        self._store: dict[str, Any] = {}
        logger.warning(
            "Redis ulanmadi — FakeRedis (xotira) ishlatilmoqda. "
            "Rate-limit va raid-detect o'chirilgan. "
            "Redis o'rnatish uchun: apt install redis-server && systemctl start redis"
        )

    def pipeline(self):
        return _FakePipeline(self._store)

    async def get(self, key: str) -> Any:
        return self._store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> bool:
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def incr(self, key: str) -> int:
        val = int(self._store.get(key, 0)) + 1
        self._store[key] = val
        return val

    async def expire(self, key: str, seconds: int) -> bool:
        return True

    async def zcard(self, key: str) -> int:
        return len(self._store.get(key, {}))

    async def zrange(self, key: str, start: int, end: int) -> list:
        s = self._store.get(key, {})
        items = sorted(s.items(), key=lambda x: x[1])
        if end == -1:
            return [k for k, _ in items[start:]]
        return [k for k, _ in items[start:end + 1]]

    async def zremrangebyscore(self, key: str, mn: float, mx: float) -> int:
        s = self._store.get(key, {})
        before = len(s)
        self._store[key] = {k: v for k, v in s.items() if not (mn <= v <= mx)}
        return before - len(self._store[key])

    async def ping(self) -> bool:
        return True

    async def aclose(self) -> None:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Asosiy ulanish — Redis bo'lsa real, bo'lmasa FakeRedis
# ═══════════════════════════════════════════════════════════════════════════════

def _make_redis_client():
    """Redis ulanishini sinab ko'radi. Muvaffaqiyatsiz bo'lsa FakeRedis qaytaradi."""
    try:
        import redis.asyncio as aioredis
        from redis.asyncio.connection import ConnectionPool
        from config import settings

        pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            max_connections=50,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=False,
        )
        client = aioredis.Redis(connection_pool=pool)
        logger.info(f"Redis client yaratildi: {settings.REDIS_URL}")
        return client, pool
    except Exception as exc:
        logger.warning(f"Redis client yaratilmadi ({exc}) — FakeRedis ishlatiladi.")
        return FakeRedis(), None


_redis_client, _pool = _make_redis_client()
redis_client = _redis_client


async def close_redis() -> None:
    """Graceful shutdown."""
    try:
        if hasattr(redis_client, "aclose"):
            await redis_client.aclose()
        if _pool and hasattr(_pool, "aclose"):
            await _pool.aclose()
    except Exception:
        pass
    logger.info("Redis ulanishi yopildi.")


async def ping_redis() -> bool:
    """Health check."""
    try:
        return await redis_client.ping()
    except Exception as e:
        logger.warning(f"Redis ping muvaffaqiyatsiz: {e}")
        return False
