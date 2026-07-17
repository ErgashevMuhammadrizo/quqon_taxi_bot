"""
Flood-control middleware.
Admin komandalar va oddiy komandalar throttle'dan ozod.
Redis ishlamasa ham bot ishlashda davom etadi.
"""
from __future__ import annotations

import time
from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.types import Message, TelegramObject

from utils.redis_client import redis_client


class ThrottlingMiddleware(BaseMiddleware):
    def __init__(self, rate_limit_seconds: float = 0.5):
        self.rate_limit_seconds = rate_limit_seconds
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)

        # Komandalar throttle'dan ozod
        if isinstance(event, Message) and event.text and event.text.startswith("/"):
            return await handler(event, data)

        key = f"guardbot:throttle:{user.id}"
        now = time.time()
        try:
            last = await redis_client.get(key)
            if last and (now - float(last)) < self.rate_limit_seconds:
                return None
            await redis_client.set(key, str(now), ex=2)
        except Exception:
            pass  # Redis ishlamasa o'tkazib yuboramiz

        return await handler(event, data)
