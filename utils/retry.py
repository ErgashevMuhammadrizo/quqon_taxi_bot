"""
Retry / Exponential Backoff
============================
Tarmoq va Telegram API xatolarida avtomatik qayta urinish uchun decorator.

Foydalanish:
    @retry(max_attempts=3, base_delay=1.0)
    async def fragile_operation():
        ...

    # Yoki context manager sifatida:
    async with RetryContext(max_attempts=3) as ctx:
        await ctx.run(some_coroutine())
"""
from __future__ import annotations

import asyncio
import functools
import logging
import random
from typing import Any, Callable, Coroutine, Tuple, Type

from utils.logger import logger


# ─── Default qayta urinish uchun xato turlari ─────────────────────────────────

_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    OSError,
)

try:
    from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_EXCEPTIONS + (TelegramNetworkError,)
except ImportError:
    TelegramRetryAfter = None

try:
    import asyncpg
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_EXCEPTIONS + (
        asyncpg.PostgresConnectionError,
        asyncpg.TooManyConnectionsError,
    )
except ImportError:
    pass

try:
    import redis.asyncio as aioredis
    _RETRYABLE_EXCEPTIONS = _RETRYABLE_EXCEPTIONS + (aioredis.ConnectionError,)
except ImportError:
    pass


# ─── Decorator ────────────────────────────────────────────────────────────────

def retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: bool = True,
    exceptions: Tuple[Type[Exception], ...] | None = None,
):
    """
    Async funksiya uchun exponential backoff retry decorator.

    Args:
        max_attempts  : maksimal urinishlar soni (birinchi urinish ham hisobga olinadi)
        base_delay    : birinchi kutish vaqti (soniya)
        max_delay     : maksimal kutish vaqti (soniya)
        backoff_factor: har urinishda delay * factor oshadi
        jitter        : random jitter qo'shib, thundering herd oldini oladi
        exceptions    : faqat shu exception'larda retry qiladi (None = default to'plam)
    """
    retryable = exceptions or _RETRYABLE_EXCEPTIONS

    def decorator(func: Callable[..., Coroutine]) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception: Exception | None = None
            delay = base_delay

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)

                # TelegramRetryAfter — flood control, serverning o'zi kutishni aytadi
                except Exception as exc:
                    if TelegramRetryAfter and isinstance(exc, TelegramRetryAfter):
                        wait = exc.retry_after + 1
                        logger.warning(
                            f"[RETRY] Telegram flood control: {wait}s kutilmoqda "
                            f"({func.__name__})"
                        )
                        await asyncio.sleep(wait)
                        last_exception = exc
                        continue

                    if not isinstance(exc, retryable):
                        raise  # qayta urinilmaydigan xato — darhol chiqariladi

                    last_exception = exc
                    if attempt == max_attempts:
                        break

                    actual_delay = min(delay, max_delay)
                    if jitter:
                        actual_delay = actual_delay * (0.5 + random.random() * 0.5)

                    logger.warning(
                        f"[RETRY] {func.__name__} — urinish {attempt}/{max_attempts} "
                        f"muvaffaqiyatsiz ({type(exc).__name__}: {exc}). "
                        f"{actual_delay:.1f}s kutilmoqda..."
                    )
                    await asyncio.sleep(actual_delay)
                    delay *= backoff_factor

            logger.error(
                f"[RETRY] {func.__name__} — barcha {max_attempts} ta urinish muvaffaqiyatsiz. "
                f"Oxirgi xato: {last_exception}"
            )
            raise last_exception  # type: ignore[misc]

        return wrapper
    return decorator


# ─── Bir martalik yordamchi ────────────────────────────────────────────────────

async def with_retry(
    coro: Coroutine,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    exceptions: Tuple[Type[Exception], ...] | None = None,
) -> Any:
    """
    Decorator o'rniga inline retry uchun yordamchi.

    Misol:
        result = await with_retry(some_coro(), max_attempts=5)
    """
    retryable = exceptions or _RETRYABLE_EXCEPTIONS
    last_exc: Exception | None = None
    delay = base_delay

    for attempt in range(1, max_attempts + 1):
        try:
            return await coro
        except Exception as exc:
            if not isinstance(exc, retryable):
                raise
            last_exc = exc
            if attempt < max_attempts:
                await asyncio.sleep(delay)
                delay *= 2.0
    raise last_exc  # type: ignore[misc]
