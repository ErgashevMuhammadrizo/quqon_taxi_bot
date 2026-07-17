"""
Security Dashboard (10-band)
==============================
Admin `/statistics` komandasini ochganda ko'rsatiladigan agregatsiya:
    Today's joins, Today's bans, Today's mutes, Risk users, Raid attempts,
    Spam blocked, Deleted messages, Captcha failed, Active users.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select

from database.db import get_session
from database.models import (
    CaptchaSession, CaptchaStatus, RaidLog, SecurityEventType, SecurityLog, User,
)


@dataclass
class SecurityStats:
    today_joins: int
    today_bans: int
    today_mutes: int
    risk_users: int          # trust_score < trust_threshold
    raid_attempts: int
    spam_blocked: int
    deleted_messages: int
    captcha_failed: int
    active_users: int        # oxirgi 24 soatda faol bo'lganlar


class SecurityDashboard:
    async def get_stats(self, chat_id: int | None = None, trust_threshold: int = 40) -> SecurityStats:
        since = datetime.utcnow() - timedelta(hours=24)

        async def count_events(event_type: SecurityEventType, since_only: bool = True) -> int:
            stmt = select(func.count(SecurityLog.id)).where(SecurityLog.event_type == event_type)
            if chat_id is not None:
                stmt = stmt.where(SecurityLog.chat_id == chat_id)
            if since_only:
                stmt = stmt.where(SecurityLog.created_at >= since)
            async with get_session() as session:
                return (await session.execute(stmt)).scalar_one()

        today_joins = await count_events(SecurityEventType.JOIN)
        today_bans = await count_events(SecurityEventType.BAN)
        today_mutes = await count_events(SecurityEventType.MUTE)
        spam_blocked = await count_events(SecurityEventType.SPAM)
        deleted_messages = await count_events(SecurityEventType.DELETE)
        captcha_failed = await count_events(SecurityEventType.CAPTCHA_FAIL)

        async with get_session() as session:
            raid_stmt = select(func.count(RaidLog.id)).where(RaidLog.started_at >= since)
            if chat_id is not None:
                raid_stmt = raid_stmt.where(RaidLog.chat_id == chat_id)
            raid_attempts = (await session.execute(raid_stmt)).scalar_one()

            risk_stmt = select(func.count(User.id)).where(User.trust_score < trust_threshold)
            risk_users = (await session.execute(risk_stmt)).scalar_one()

            active_stmt = select(func.count(User.id)).where(User.last_active_at >= since)
            active_users = (await session.execute(active_stmt)).scalar_one()

        return SecurityStats(
            today_joins=today_joins,
            today_bans=today_bans,
            today_mutes=today_mutes,
            risk_users=risk_users,
            raid_attempts=raid_attempts,
            spam_blocked=spam_blocked,
            deleted_messages=deleted_messages,
            captcha_failed=captcha_failed,
            active_users=active_users,
        )

    def format_stats(self, stats: SecurityStats) -> str:
        return (
            "🛡 <b>Security Dashboard</b> (so'nggi 24 soat)\n\n"
            f"➕ Yangi join'lar:        <b>{stats.today_joins}</b>\n"
            f"🚫 Banlar:                 <b>{stats.today_bans}</b>\n"
            f"🔇 Mute'lar:               <b>{stats.today_mutes}</b>\n"
            f"⚠️ Risk userlar (past trust): <b>{stats.risk_users}</b>\n"
            f"🚨 Raid urinishlari:       <b>{stats.raid_attempts}</b>\n"
            f"🧹 Spam bloklandi:         <b>{stats.spam_blocked}</b>\n"
            f"🗑 O'chirilgan xabarlar:   <b>{stats.deleted_messages}</b>\n"
            f"🧩 Captcha xato:           <b>{stats.captcha_failed}</b>\n"
            f"👥 Faol userlar:           <b>{stats.active_users}</b>"
        )


security_dashboard = SecurityDashboard()
