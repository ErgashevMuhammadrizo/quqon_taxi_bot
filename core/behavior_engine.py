"""
Behavior Engine
===============
Foydalanuvchi xatti-harakatlarini (forward tezligi, join qilish patterni,
akkaunt yoshi) kuzatib, "bot/scraper" yoki "shubhali foydalanuvchi"
ekanligini aniqlaydi. Ma'lumotlar Redis'da vaqtinchalik (sliding window)
saqlanadi - bu tezkor va bazani ortiqcha yuklamaydigan yechim.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

from config import settings


@dataclass
class BehaviorScore:
    forward_rate_score: float   # 0..1  - forward tezligi bo'yicha shubha darajasi
    account_age_score: float    # 0..1  - yangi akkaunt bo'lsa yuqoriroq
    is_rate_limited: bool       # joriy oynada limitdan oshganmi


class BehaviorEngine:
    def __init__(self, redis: Redis):
        self.redis = redis

    def _forward_key(self, user_id: int) -> str:
        return f"guardbot:forward_count:{user_id}"

    async def register_forward(self, user_id: int) -> int:
        """
        Foydalanuvchining forward harakatini ro'yxatdan o'tkazadi.
        Redis yo'q bo'lsa 0 qaytaradi (rate limit o'chirilgan holda ishlaydi).
        """
        try:
            key = self._forward_key(user_id)
            now = time.time()
            window_start = now - settings.RATE_LIMIT_WINDOW_SECONDS

            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {f"{now}": now})
            pipe.zcard(key)
            pipe.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS * 2)
            _, _, count, _ = await pipe.execute()
            return int(count)
        except Exception:
            return 0  # Redis yo'q — limitdan o'tmagan deb hisoblaymiz

    async def get_forward_count(self, user_id: int) -> int:
        try:
            key = self._forward_key(user_id)
            now = time.time()
            window_start = now - settings.RATE_LIMIT_WINDOW_SECONDS
            await self.redis.zremrangebyscore(key, 0, window_start)
            return int(await self.redis.zcard(key))
        except Exception:
            return 0

    def score_forward_rate(self, count: int) -> float:
        """Forward sonini 0..1 shubha skoriga aylantiradi (limit atrofida chiziqli o'sadi)."""
        limit = settings.RATE_LIMIT_FORWARDS
        if count <= 0:
            return 0.0
        return min(count / (limit * 1.5), 1.0)

    def score_account_age(self, account_created_days_ago: int | None) -> float:
        """
        Telegram akkaunt yoshini aniqlash cheklangan (ochiq API bermaydi), shu sabab
        bu funksiya odatda `first_seen_at` (bot birinchi ko'rgan vaqt) asosida ishlaydi -
        agar foydalanuvchi kanalga yangi qo'shilgan bo'lsa va darrov forward qilsa, shubhali.
        """
        if account_created_days_ago is None:
            return 0.3  # noaniqlik uchun neytral-past qiymat
        if account_created_days_ago <= settings.NEW_ACCOUNT_DAYS_SUSPICIOUS:
            return 1.0
        if account_created_days_ago <= settings.NEW_ACCOUNT_DAYS_SUSPICIOUS * 5:
            return 0.5
        return 0.1

    async def evaluate(self, user_id: int, account_created_days_ago: int | None = None) -> BehaviorScore:
        try:
            count = await self.register_forward(user_id)
        except Exception:
            count = 0
        forward_score = self.score_forward_rate(count)
        age_score = self.score_account_age(account_created_days_ago)
        is_limited = count > settings.RATE_LIMIT_FORWARDS
        return BehaviorScore(forward_score, age_score, is_limited)
