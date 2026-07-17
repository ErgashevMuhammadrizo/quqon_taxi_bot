"""
Audit — security_logs jurnali va har-user audit tarixi.

Ikki narsani ajratib turish kerak:
  - `core.ban_manager` / mavjud `AuditLog` (audit_logs) — umumiy admin
    harakatlari (ban/unban/whitelist/settings...).
  - `security.audit.AuditRecorder` (security_logs) — Security Engine
    voqealari: JOIN, LEAVE, BAN, MUTE, DELETE, FORWARD_BLOCK, LINK_BLOCK,
    RAID, SPAM, CAPTCHA_FAIL va h.k. (7-band: "Security Log").

`get_user_audit_trail()` — 8-band ("Audit Log"): bitta user uchun to'liq
tarix (joined, warnings, deleted messages, mutes, bans, captcha,
trust changes, risk changes) — bir nechta jadvaldan yig'ib beradi.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select

from database.db import get_session
from database.models import (
    CaptchaSession,
    RiskHistory,
    SecurityEventType,
    SecurityLog,
    TrustScoreLog,
)
from utils.logger import logger


class AuditRecorder:
    """security_logs jadvaliga yozadigan yagona kirish nuqtasi."""

    async def log_event(
        self,
        *,
        chat_id: int,
        event_type: SecurityEventType,
        user_id: int | None = None,
        message_id: int | None = None,
        details: dict[str, Any] | str | None = None,
    ) -> None:
        if isinstance(details, dict):
            details_str = json.dumps(details, ensure_ascii=False, default=str)
        else:
            details_str = details

        try:
            async with get_session() as session:
                session.add(
                    SecurityLog(
                        chat_id=chat_id,
                        user_id=user_id,
                        event_type=event_type,
                        message_id=message_id,
                        details=details_str,
                    )
                )
        except Exception as exc:  # pragma: no cover - DB muammosi botni to'xtatmasin
            logger.error(f"[audit] security_logs yozib bo'lmadi: {exc}")

    # ── Qulaylik metodlari (aniq event turlari) ────────────────────────────

    async def join(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.JOIN, details=extra)

    async def leave(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.LEAVE, details=extra)

    async def ban(self, chat_id: int, user_id: int, reason: str, **extra: Any) -> None:
        await self.log_event(
            chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.BAN,
            details={"reason": reason, **extra},
        )

    async def mute(self, chat_id: int, user_id: int, reason: str, **extra: Any) -> None:
        await self.log_event(
            chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.MUTE,
            details={"reason": reason, **extra},
        )

    async def delete(self, chat_id: int, user_id: int | None, message_id: int | None, reason: str) -> None:
        await self.log_event(
            chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.DELETE,
            message_id=message_id, details={"reason": reason},
        )

    async def forward_block(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.FORWARD_BLOCK, details=extra)

    async def link_block(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.LINK_BLOCK, details=extra)

    async def raid(self, chat_id: int, join_count: int, **extra: Any) -> None:
        await self.log_event(
            chat_id=chat_id, event_type=SecurityEventType.RAID,
            details={"join_count": join_count, **extra},
        )

    async def spam(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.SPAM, details=extra)

    async def captcha_fail(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.CAPTCHA_FAIL, details=extra)

    async def captcha_pass(self, chat_id: int, user_id: int, **extra: Any) -> None:
        await self.log_event(chat_id=chat_id, user_id=user_id, event_type=SecurityEventType.CAPTCHA_PASS, details=extra)


@dataclass
class UserAuditTrail:
    user_id: int
    joined_events: list[dict[str, Any]] = field(default_factory=list)
    warnings: int = 0
    deleted_messages: int = 0
    mutes: int = 0
    bans: int = 0
    captcha_events: list[dict[str, Any]] = field(default_factory=list)
    trust_changes: list[dict[str, Any]] = field(default_factory=list)
    risk_changes: list[dict[str, Any]] = field(default_factory=list)


async def get_user_audit_trail(user_id: int, limit: int = 50) -> UserAuditTrail:
    """Bitta foydalanuvchi uchun to'liq audit tarixini yig'ib qaytaradi (8-band)."""
    trail = UserAuditTrail(user_id=user_id)

    async with get_session() as session:
        sec_logs = (
            await session.execute(
                select(SecurityLog)
                .where(SecurityLog.user_id == user_id)
                .order_by(SecurityLog.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()

        for row in sec_logs:
            entry = {
                "type": row.event_type.value,
                "chat_id": row.chat_id,
                "at": row.created_at.isoformat(),
                "details": row.details,
            }
            if row.event_type == SecurityEventType.JOIN:
                trail.joined_events.append(entry)
            elif row.event_type == SecurityEventType.DELETE:
                trail.deleted_messages += 1
            elif row.event_type == SecurityEventType.MUTE:
                trail.mutes += 1
            elif row.event_type == SecurityEventType.BAN:
                trail.bans += 1
            elif row.event_type in (SecurityEventType.CAPTCHA_FAIL, SecurityEventType.CAPTCHA_PASS):
                trail.captcha_events.append(entry)
            elif row.event_type == SecurityEventType.ADMIN_ALERT:
                trail.warnings += 1

        trust_rows = (
            await session.execute(
                select(TrustScoreLog)
                .where(TrustScoreLog.user_id == user_id)
                .order_by(TrustScoreLog.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        trail.trust_changes = [
            {
                "delta": r.delta, "reason": r.reason,
                "old": r.old_score, "new": r.new_score,
                "at": r.created_at.isoformat(),
            }
            for r in trust_rows
        ]

        risk_rows = (
            await session.execute(
                select(RiskHistory)
                .where(RiskHistory.user_id == user_id)
                .order_by(RiskHistory.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
        trail.risk_changes = [
            {
                "action": r.action_type.value, "score": r.risk_score,
                "decision": r.decision.value, "at": r.created_at.isoformat(),
            }
            for r in risk_rows
        ]

    return trail


audit = AuditRecorder()
