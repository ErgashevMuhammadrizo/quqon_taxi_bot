"""
Suspicious User Monitor (5-band)
===================================
Bot userlarni kuzatib, quyidagi xulq-atvor patternlari uchun
"Suspicious Score" (0..1) hisoblaydi:

    - juda ko'p guruhga kiradi         -> many_groups
    - juda kam gapiradi                -> low_talk (faqat kuzatadi)
    - faqat kuzatadi                   -> silent_watcher
    - juda tez flood qiladi            -> flood
    - bir xil message yuboradi         -> duplicate_messages

Flood va duplicate-message tekshiruvi Redis orqali (tez, DB'ni yuklamaydi);
guruh soni va "faqat kuzatuvchi" holati esa `User` jadvalidagi
akkumulyativ maydonlar (`groups_joined_count`, `message_count`,
`last_active_at`, `join_time`) asosida hisoblanadi.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field

from redis.asyncio import Redis

from config import settings
from database.db import get_session
from database.models import User
from sqlalchemy import select
from utils.logger import logger


@dataclass
class SuspiciousFlags:
    many_groups: bool = False
    low_talk: bool = False
    silent_watcher: bool = False
    flood: bool = False
    duplicate_messages: bool = False

    @property
    def score(self) -> float:
        weights = {
            "many_groups": 0.2, "low_talk": 0.15, "silent_watcher": 0.15,
            "flood": 0.3, "duplicate_messages": 0.2,
        }
        total = 0.0
        for field_name, w in weights.items():
            if getattr(self, field_name):
                total += w
        return round(min(total, 1.0), 2)


class SuspiciousUserMonitor:
    def __init__(self, redis: Redis):
        self.redis = redis

    def _flood_key(self, user_id: int) -> str:
        return f"guardbot:security:flood:{user_id}"

    def _dup_key(self, user_id: int) -> str:
        return f"guardbot:security:lastmsgs:{user_id}"

    async def register_message(self, user_id: int, text: str | None) -> tuple[bool, bool]:
        """Xabarni ro'yxatdan o'tkazadi. (is_flood, is_duplicate) qaytaradi."""
        now = time.time()

        # ── Flood: 10s ichida N ta xabardan ko'p ──────────────────────────
        flood_key = self._flood_key(user_id)
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(flood_key, 0, now - 10)
        pipe.zadd(flood_key, {f"{now}": now})
        pipe.zcard(flood_key)
        pipe.expire(flood_key, 20)
        _, _, count, _ = await pipe.execute()
        is_flood = int(count) >= settings.SUSPICIOUS_FLOOD_MSG_PER_10S

        # ── Duplicate: ketma-ket bir xil matn ──────────────────────────────
        is_duplicate = False
        if text:
            digest = hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()
            dup_key = self._dup_key(user_id)
            last = await self.redis.get(dup_key)
            if last == digest:
                dup_count = int(await self.redis.incr(f"{dup_key}:count"))
                await self.redis.expire(f"{dup_key}:count", 120)
                is_duplicate = dup_count >= settings.SUSPICIOUS_DUPLICATE_MSG_COUNT
            else:
                await self.redis.delete(f"{dup_key}:count")
            await self.redis.set(dup_key, digest, ex=120)

        return is_flood, is_duplicate

    async def register_group_join(self, user_id: int) -> int:
        """User necha guruhga qo'shilganini oshiradi (24 soatlik oyna, Redis)."""
        key = f"guardbot:security:groups_joined:{user_id}"
        now = time.time()
        pipe = self.redis.pipeline()
        pipe.zremrangebyscore(key, 0, now - 86400)
        pipe.zadd(key, {f"{now}": now})
        pipe.zcard(key)
        pipe.expire(key, 90000)
        _, _, count, _ = await pipe.execute()
        return int(count)

    async def evaluate(self, user_id: int, message_text: str | None = None) -> SuspiciousFlags:
        flags = SuspiciousFlags()

        is_flood, is_duplicate = (False, False)
        if message_text is not None:
            is_flood, is_duplicate = await self.register_message(user_id, message_text)
        flags.flood = is_flood
        flags.duplicate_messages = is_duplicate

        many_groups_count = await self.register_group_join(user_id) if message_text is None else 0
        if many_groups_count >= settings.SUSPICIOUS_MANY_GROUPS_THRESHOLD:
            flags.many_groups = True

        try:
            async with get_session() as session:
                result = await session.execute(select(User).where(User.telegram_id == user_id))
                user = result.scalar_one_or_none()
                if user is not None:
                    flags.low_talk = user.message_count == 0 and user.groups_joined_count > 0
                    if user.join_time is not None and user.last_active_at is not None:
                        idle_days = (user.last_active_at - user.join_time).days
                    else:
                        idle_days = None
                    flags.silent_watcher = (
                        user.message_count == 0
                        and idle_days is not None
                        and idle_days >= settings.SUSPICIOUS_SILENT_WATCH_DAYS
                    )
                    user.suspicious_score = flags.score
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[suspicious_monitor] User o'qib/yozib bo'lmadi: {exc}")

        return flags
