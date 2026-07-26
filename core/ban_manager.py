"""
Ban Manager
===========
Qoidabuzarni bloklaydi, harakatni bazaga (AuditLog + BannedUser) yozadi
va barcha adminlarga real-time xabar yuboradi (evidence bilan birga).
"""
from __future__ import annotations

import json
from datetime import datetime

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select

from database.db import get_session
from database.models import Admin, AuditLog, ActionType, BannedUser, Whitelist
from utils.logger import logger


class BanManager:
    def __init__(self, bot: Bot):
        self.bot = bot

    async def is_whitelisted(self, user_id: int) -> bool:
        async with get_session() as session:
            result = await session.execute(select(Whitelist).where(Whitelist.user_id == user_id))
            return result.scalar_one_or_none() is not None

    async def is_protected(self, user_id: int) -> tuple[bool, str]:
        """
        Foydalanuvchi ban'dan himoyalanganmi?

        Bu MARKAZIY himoya nuqtasi — barcha ban yo'llari (spam, watermark,
        media job, security engine, join reban, /gban) shu funksiyadan o'tadi.
        Shu sababli admin yoki whitelist'dagi odam HECH QANDAY holatda
        ban olmaydi.

        Qaytaradi: (himoyalanganmi, sabab)
        """
        # 1. Config SUPER_ADMIN_IDS — DB ga bormay tekshiramiz
        from config import settings as cfg
        if user_id in cfg.super_admins:
            return True, "config super_admin"

        # 2. GuardBot DB admini (super_admin / moderator / viewer)
        try:
            from middlewares.role_check import get_admin_role
            role = await get_admin_role(user_id)
            if role is not None:
                return True, f"GuardBot admin ({role.value})"
        except Exception as exc:
            logger.warning(f"[ban_manager] admin tekshiruvda xato: {exc}")

        # 3. Whitelist
        try:
            if await self.is_whitelisted(user_id):
                return True, "whitelist"
        except Exception as exc:
            logger.warning(f"[ban_manager] whitelist tekshiruvda xato: {exc}")

        return False, ""

    async def execute_ban(
        self,
        user_id: int,
        chat_id: int,
        reason: str,
        evidence: dict,
        risk_score: float | None = None,
        banned_by: int | None = None,
    ) -> bool:
        """
        1) Himoya tekshiruvi (admin / whitelist) — himoyalangan bo'lsa ban YO'Q
        2) Chatdan ban qilish (xabarlarini ham o'chirish bilan)
        3) Bazaga yozish (AuditLog + BannedUser)
        4) Adminlarga xabar yuborish
        """
        protected, why = await self.is_protected(user_id)
        if protected:
            logger.info(
                f"[ban_manager] User {user_id} himoyalangan ({why}) — "
                f"ban BEKOR qilindi. Sabab bo'lgan: {reason}"
            )
            return False

        try:
            await self.bot.ban_chat_member(chat_id=chat_id, user_id=user_id, revoke_messages=True)
        except TelegramAPIError as e:
            logger.error(f"Ban qilishda xato (user={user_id}, chat={chat_id}): {e}")
            return False

        evidence_json = json.dumps(evidence, ensure_ascii=False, default=str)

        async with get_session() as session:
            session.add(AuditLog(
                user_id=user_id, chat_id=chat_id, action=ActionType.BAN,
                reason=reason, evidence=evidence_json, risk_score=risk_score,
            ))
            session.add(BannedUser(
                user_id=user_id, chat_id=chat_id, reason=reason,
                evidence=evidence_json, banned_at=datetime.utcnow(), banned_by=banned_by,
            ))

        await self._notify_admins(user_id, chat_id, reason, evidence, risk_score)
        logger.warning(f"User {user_id} chat {chat_id} da bloklandi. Sabab: {reason}")
        return True

    async def unban(self, user_id: int, chat_id: int, unbanned_by: int | None = None) -> bool:
        try:
            await self.bot.unban_chat_member(chat_id=chat_id, user_id=user_id, only_if_banned=True)
        except TelegramAPIError as e:
            logger.error(f"Unban qilishda xato: {e}")
            return False

        async with get_session() as session:
            session.add(AuditLog(
                user_id=user_id, chat_id=chat_id, action=ActionType.UNBAN,
                reason=f"Admin {unbanned_by} tomonidan unban qilindi",
            ))
        return True

    async def _notify_admins(
        self, user_id: int, chat_id: int, reason: str, evidence: dict, risk_score: float | None
    ) -> None:
        from config import settings as cfg

        text = (
            "🚨 <b>AVTOMATIK BAN</b>\n\n"
            f"👤 User ID: <code>{user_id}</code>\n"
            f"💬 Chat ID: <code>{chat_id}</code>\n"
            f"📊 Risk score: <b>{risk_score}</b>\n"
            f"📝 Sabab: {reason}\n"
            f"🔍 Evidence: <pre>{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)[:800]}</pre>"
        )

        sent_ids: set[int] = set()

        # 1. .env super_admins
        for admin_id in cfg.super_admins:
            try:
                await self.bot.send_message(admin_id, text)
                sent_ids.add(admin_id)
            except TelegramAPIError:
                pass

        # 2. DB adminlar
        try:
            async with get_session() as session:
                result = await session.execute(select(Admin))
                admins = result.scalars().all()
            for admin in admins:
                if admin.telegram_id in sent_ids:
                    continue
                try:
                    await self.bot.send_message(admin.telegram_id, text)
                except TelegramAPIError:
                    pass
        except Exception:
            pass
