"""
Admin RBAC Middleware.

AdminOnlyMiddleware — DP darajasida ulanadi (router emas).
Barcha komandalar uchun bitta tekshiruv, ikki marta ishlamaslik kafolatlangan.

Mantiq:
  1. Xabar komanda bo'lmasa yoki guruhdan/kanaldan kelsa → o'tkazamiz
     (group_events.py o'zi ishlaydi)
  2. Private chat'dan kelgan komanda bo'lsa:
     a. Admin komandalar ro'yxatida bormi?
        - Yo'q → faqat /start va /help ishlaydi (start_router o'zi tekshiradi)
        - Bor  → user admin ekanligini tekshiramiz
     b. Admin emas → jim (hech narsa)
     c. Admin, lekin roli yetarli emas → xabar
     d. Admin, roli yetarli → o'tkazamiz
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import Message, TelegramObject
from sqlalchemy import select

from config import settings
from database.db import get_session
from database.models import Admin, AdminRole

# ─── Komanda → minimal rol xaritasi ──────────────────────────────────────────
COMMAND_MIN_ROLE: dict[str, AdminRole] = {
    # Viewer va yuqori
    "start":          AdminRole.VIEWER,
    "help":           AdminRole.VIEWER,
    "stats":          AdminRole.VIEWER,
    "scan_history":   AdminRole.VIEWER,
    "banned":         AdminRole.VIEWER,
    # Moderator va yuqori
    "export_logs":    AdminRole.MODERATOR,
    "whitelist":      AdminRole.MODERATOR,
    "unban":          AdminRole.MODERATOR,
    "add_channel":    AdminRole.MODERATOR,
    "protect_channel":AdminRole.MODERATOR,
    "add_group":      AdminRole.MODERATOR,
    "channels":       AdminRole.MODERATOR,
    "groups":         AdminRole.MODERATOR,
    # Faqat SUPER_ADMIN
    "settings":       AdminRole.SUPER_ADMIN,
    "add_admin":      AdminRole.SUPER_ADMIN,
    "admins":         AdminRole.SUPER_ADMIN,
    "remove_admin":   AdminRole.SUPER_ADMIN,
    "cancel":         AdminRole.VIEWER,
}

_LEVEL: dict[AdminRole, int] = {
    AdminRole.VIEWER:      0,
    AdminRole.MODERATOR:   1,
    AdminRole.SUPER_ADMIN: 2,
}


async def get_admin_role(telegram_id: int) -> AdminRole | None:
    """
    Foydalanuvchi rolini qaytaradi.
    super_admins listida bo'lsa — DB'ga bormay SUPER_ADMIN.
    DB'da bo'lsa — o'sha rol.
    Bo'lmasa — None.
    """
    if telegram_id in settings.super_admins:
        return AdminRole.SUPER_ADMIN
    try:
        async with get_session() as session:
            result = await session.execute(
                select(Admin).where(Admin.telegram_id == telegram_id)
            )
            admin = result.scalar_one_or_none()
            return admin.role if admin else None
    except Exception:
        # DB ishlamasa ham super_admins listiga tayanamiz
        return None


async def is_any_admin(telegram_id: int) -> bool:
    """Foydalanuvchi istalgan adminmi?"""
    return await get_admin_role(telegram_id) is not None


class AdminOnlyMiddleware(BaseMiddleware):
    """
    Dispatcher darajasida ishlaydigan middleware.
    Barcha private chat komandalarini tekshiradi.
    Guruh/kanal xabarlari — o'tkaziladi (group_events.py o'zi ishlaydi).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # Message bo'lmasa (callback_query, channel_post va h.k.) — o'tkazamiz
        if not isinstance(event, Message):
            return await handler(event, data)

        # Guruh / supergroup / kanal'dan kelgan xabar — group_events ishlaydi
        if event.chat.type in (
            ChatType.GROUP, ChatType.SUPERGROUP, ChatType.CHANNEL
        ):
            return await handler(event, data)

        # Private chat'dan kelgan komanda
        if not event.text or not event.text.startswith("/"):
            # Komanda emas, private chat — ham o'tkazamiz
            return await handler(event, data)

        user = event.from_user
        if user is None:
            return None

        # Komanda nomini ajratib olamiz
        command = event.text.split()[0].lstrip("/").split("@")[0].lower()

        required_role = COMMAND_MIN_ROLE.get(command)

        if required_role is None:
            # Ro'yxatda yo'q komanda — faqat adminlar uchun
            # (start/help handler o'zi tekshiradi, lekin boshqa noma'lum komandalar jim)
            user_role = await get_admin_role(user.id)
            if user_role is None:
                return None  # admin emas — jim
            data["admin_role"] = user_role
            return await handler(event, data)

        # Ro'yxatdagi komanda — rolni tekshiramiz
        user_role = await get_admin_role(user.id)

        if user_role is None:
            # Admin emas — jim, hech qanday javob bermaymiz
            return None

        if _LEVEL[user_role] < _LEVEL[required_role]:
            await event.answer(
                f"⛔️ <b>{command}</b> uchun <b>{required_role.value}</b> roli kerak.\n"
                f"Sizning rolingiz: <b>{user_role.value}</b>"
            )
            return None

        data["admin_role"] = user_role
        return await handler(event, data)
