"""
Anti-Raid Engine (4-band)
==========================
5 sekund ichida 10+ account join bo'lsa -> Raid Mode.

Raid Mode paytida (admin o'chirmaguncha):
    - Captcha ON
    - Media OFF
    - Links OFF
    - Forward OFF

Redis sorted-set orqali sliding-window bilan join'lar sanaladi (13-band:
"Performance" — flood detect Redis orqali, DB kamroq ishlatiladi). Raid
Mode holatining o'zi ham Redis'da flag sifatida saqlanadi (tezkor
o'qish uchun); `ProtectedGroup.raid_mode_active` esa persistent nusxa —
bot restart bo'lganda ham holatni tiklash uchun ishlatiladi.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass

from redis.asyncio import Redis

from config import settings
from database.db import get_session
from database.models import ProtectedGroup, RaidLog, SecurityEventType
from security.audit import audit
from utils.logger import logger


@dataclass
class RaidCheckResult:
    is_raid: bool
    join_count: int
    raid_mode_active: bool  # shu tekshiruvdan keyingi holat


class RaidDetector:
    def __init__(self, redis: Redis):
        self.redis = redis

    def _join_key(self, chat_id: int) -> str:
        return f"guardbot:security:raid_joins:{chat_id}"

    def _mode_key(self, chat_id: int) -> str:
        return f"guardbot:security:raid_mode:{chat_id}"

    async def register_join(self, chat_id: int, user_id: int) -> int:
        """Join'ni Redis sliding-window'ga qo'shadi, joriy oynadagi join sonini qaytaradi."""
        try:
            key = self._join_key(chat_id)
            now = time.time()
            window_start = now - settings.RAID_WINDOW_SECONDS

            pipe = self.redis.pipeline()
            pipe.zremrangebyscore(key, 0, window_start)
            pipe.zadd(key, {f"{user_id}:{now}": now})
            pipe.zcard(key)
            pipe.expire(key, settings.RAID_WINDOW_SECONDS * 4)
            _, _, count, _ = await pipe.execute()
            return int(count)
        except Exception as exc:
            logger.debug(f"[raid_detector] register_join Redis xato: {exc}")
            return 0

    async def is_raid_mode_active(self, chat_id: int) -> bool:
        try:
            return bool(await self.redis.get(self._mode_key(chat_id)))
        except Exception:
            return False

    async def activate_raid_mode(self, chat_id: int, join_count: int, joined_user_ids: list[int]) -> None:
        try:
            await self.redis.set(self._mode_key(chat_id), "1")
        except Exception as exc:
            logger.debug(f"[raid_detector] activate Redis xato: {exc}")
        logger.warning(f"[raid_detector] RAID MODE ON chat={chat_id} joins={join_count}")

        try:
            async with get_session() as session:
                session.add(
                    RaidLog(
                        chat_id=chat_id,
                        join_count=join_count,
                        joined_user_ids=json.dumps(joined_user_ids),
                        is_active=True,
                    )
                )
        except Exception as exc:  # pragma: no cover
            logger.error(f"[raid_detector] raid_logs yozib bo'lmadi: {exc}")

        await self._sync_protected_group_flag(chat_id, True)
        await audit.log_event(
            chat_id=chat_id, event_type=SecurityEventType.RAID_MODE_ON,
            details={"join_count": join_count, "user_ids": joined_user_ids},
        )

    async def deactivate_raid_mode(self, chat_id: int, ended_by: int | None = None) -> None:
        try:
            await self.redis.delete(self._mode_key(chat_id))
        except Exception:
            pass

        try:
            from sqlalchemy import select, update
            from datetime import datetime

            async with get_session() as session:
                result = await session.execute(
                    select(RaidLog).where(RaidLog.chat_id == chat_id, RaidLog.is_active == True)  # noqa: E712
                )
                for row in result.scalars().all():
                    row.is_active = False
                    row.ended_at = datetime.utcnow()
                    row.ended_by = ended_by
        except Exception as exc:  # pragma: no cover
            logger.error(f"[raid_detector] raid_logs yopib bo'lmadi: {exc}")

        await self._sync_protected_group_flag(chat_id, False)
        await audit.log_event(
            chat_id=chat_id, event_type=SecurityEventType.RAID_MODE_OFF,
            details={"ended_by": ended_by},
        )

    async def _sync_protected_group_flag(self, chat_id: int, active: bool) -> None:
        try:
            from datetime import datetime
            from sqlalchemy import select

            async with get_session() as session:
                result = await session.execute(
                    select(ProtectedGroup).where(ProtectedGroup.chat_id == chat_id)
                )
                group = result.scalar_one_or_none()
                if group is not None:
                    group.raid_mode_active = active
                    group.raid_mode_since = datetime.utcnow() if active else None
        except Exception as exc:  # pragma: no cover
            logger.error(f"[raid_detector] ProtectedGroup flag yangilanmadi: {exc}")

    async def check_join(self, chat_id: int, user_id: int) -> RaidCheckResult:
        """Har yangi join'da chaqiriladi. Threshold oshsa Raid Mode'ni yoqadi."""
        try:
            count = await self.register_join(chat_id, user_id)
            already_active = await self.is_raid_mode_active(chat_id)

            if not already_active and count >= settings.RAID_JOIN_THRESHOLD:
                key = self._join_key(chat_id)
                try:
                    members = await self.redis.zrange(key, 0, -1)
                except Exception:
                    members = []
                user_ids = []
                for m in members:
                    try:
                        user_ids.append(int(str(m).split(":")[0]))
                    except (ValueError, IndexError):
                        continue
                await self.activate_raid_mode(chat_id, count, user_ids)
                return RaidCheckResult(is_raid=True, join_count=count, raid_mode_active=True)

            return RaidCheckResult(is_raid=False, join_count=count, raid_mode_active=already_active)
        except Exception as exc:
            logger.debug(f"[raid_detector] check_join xato (Redis?): {exc}")
            return RaidCheckResult(is_raid=False, join_count=0, raid_mode_active=False)
