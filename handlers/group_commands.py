"""
Group Commands Handler
=======================
Guruh ichida ishlatiladigan admin komandalar.
Faqat guruh/supergroup admini yoki GuardBot admini ishlatishi mumkin.

Komandalar:
  /gban   [reply | user_id] [sabab]  — guruhdan ban
  /gunban [reply | user_id]          — blokdan chiqarish
  /gmute  [reply | user_id] [daqiqa] — vaqtincha sukut (default: 60 daq)
  /gunmute [reply | user_id]         — sukutdan chiqarish
  /gwarn  [reply | user_id] [sabab]  — ogohlantirish (3 ta = auto ban)
  /ginfo  [reply | user_id]          — foydalanuvchi haqida ma'lumot
  /gstatus                           — guruh himoya holati
  /gclean [N]                        — so'nggi N ta xabarni tozalash (max 100)
  /raid_off                          — Raid Mode ni o'chirish (security_events.py da ham bor)

Tekshiruv tartibi:
  1. Guruh/supergroup ichida kelishi kerak
  2. Buyruq beruvchi Telegram guruh admini YOKI GuardBot admini bo'lishi kerak
  3. Buyruq berilgan foydalanuvchi GuardBot admini bo'lmasligi kerak (himoya)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import ChatPermissions, Message
from sqlalchemy import func, select

from config import settings
from core.ban_manager import BanManager
from database.db import get_session
from database.models import (
    ActionType, AuditLog, BannedUser,
    ProtectedGroup, User, Whitelist,
)
from middlewares.role_check import get_admin_role
from utils.logger import logger

router = Router(name="group_commands")

# Ogohlantirish soni — Redis da saqlaymiz
_WARN_LIMIT = 3


# ═══════════════════════════════════════════════════════════════════════════════
#  Yordamchi funksiyalar
# ═══════════════════════════════════════════════════════════════════════════════

async def _is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """Foydalanuvchi Telegram guruh admini yoki GuardBot admini ekanini tekshiradi."""
    # GuardBot admini
    if await get_admin_role(user_id) is not None:
        return True
    # Telegram guruh admini
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramAPIError:
        return False


async def _is_protected(chat_id: int) -> bool:
    """Guruh himoyalangan ekanini tekshiradi."""
    try:
        async with get_session() as s:
            row = (await s.execute(
                select(ProtectedGroup).where(
                    ProtectedGroup.chat_id == chat_id,
                    ProtectedGroup.is_active == True,  # noqa: E712
                )
            )).scalar_one_or_none()
            return row is not None
    except Exception:
        return False


def _resolve_target(message: Message, command: CommandObject) -> tuple[int | None, str | None]:
    """
    Reply yoki argument dan maqsad foydalanuvchi ID sini ajratib oladi.
    Qaytadi: (user_id, sabab_qismi)
    """
    # Reply usuli
    if message.reply_to_message and message.reply_to_message.from_user:
        user = message.reply_to_message.from_user
        reason = command.args or ""
        return user.id, reason.strip() or None

    # Argument usuli: /gban 123456789 sabab matni
    if command.args:
        parts = command.args.split(None, 1)
        if parts[0].lstrip("-").isdigit():
            return int(parts[0]), (parts[1].strip() if len(parts) > 1 else None)

    return None, None


async def _get_warn_count(user_id: int, chat_id: int) -> int:
    """Redis dan ogohlantirish sonini oladi."""
    try:
        from utils.redis_client import redis_client
        key = f"guardbot:warn:{chat_id}:{user_id}"
        val = await redis_client.get(key)
        return int(val) if val else 0
    except Exception:
        return 0


async def _increment_warn(user_id: int, chat_id: int) -> int:
    """Ogohlantirish sonini 1 ga oshirib qaytaradi (TTL: 30 kun)."""
    try:
        from utils.redis_client import redis_client
        key = f"guardbot:warn:{chat_id}:{user_id}"
        count = await redis_client.incr(key)
        await redis_client.expire(key, 30 * 24 * 3600)
        return int(count)
    except Exception:
        return 1


async def _reset_warn(user_id: int, chat_id: int) -> None:
    """Ogohlantirish hisoblagichini nolga tushiradi."""
    try:
        from utils.redis_client import redis_client
        key = f"guardbot:warn:{chat_id}:{user_id}"
        await redis_client.delete(key)
    except Exception:
        pass


async def _deny(message: Message, text: str = "⛔️ Ruxsat yo'q.") -> None:
    sent = await message.answer(text)
    # Guruhda xato xabarlarni 5 soniyadan so'ng o'chiramiz
    import asyncio
    await asyncio.sleep(5)
    try:
        await sent.delete()
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  Filtr: faqat guruh/supergroup da ishlaydi
# ═══════════════════════════════════════════════════════════════════════════════

_GROUP_FILTER = F.chat.type.in_({"group", "supergroup"})


# ═══════════════════════════════════════════════════════════════════════════════
#  /gban — Ban qilish
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gban"), _GROUP_FILTER)
async def cmd_gban(message: Message, command: CommandObject, bot: Bot) -> None:
    """
    /gban [reply | user_id] [sabab]
    Maqsad foydalanuvchini guruhdan ban qiladi.
    """
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, reason = _resolve_target(message, command)
    if not target_id:
        await _deny(message, "ℹ️ Foydalanish: /gban javob yoki <code>/gban &lt;user_id&gt; [sabab]</code>")
        return

    # Bot o'zini ban qilmasin
    bot_me = await bot.get_me()
    if target_id == bot_me.id:
        await _deny(message, "⛔️ Botni ban qilib bo'lmaydi.")
        return

    # GuardBot admini bo'lsa himoya
    if await get_admin_role(target_id) is not None:
        await _deny(message, "⛔️ GuardBot adminini ban qilib bo'lmaydi.")
        return

    reason_text = reason or f"Admin @{caller.username or caller.id} tomonidan ban qilindi"
    bm = BanManager(bot)
    ok = await bm.execute_ban(
        user_id=target_id,
        chat_id=message.chat.id,
        reason=reason_text,
        evidence={
            "command": "gban",
            "by": caller.id,
            "chat_id": message.chat.id,
            "message_id": message.message_id,
        },
        risk_score=100.0,
        banned_by=caller.id,
    )

    # Reply xabarini o'chiramiz
    if message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except TelegramAPIError:
            pass

    if ok:
        await message.answer(
            f"🚫 <code>{target_id}</code> ban qilindi.\n"
            f"👮 Admin: @{caller.username or caller.id}\n"
            f"📝 Sabab: {reason_text[:200]}"
        )
        logger.info(f"[GBAN] user={target_id} chat={message.chat.id} by={caller.id}")
    else:
        await message.answer(f"⚠️ <code>{target_id}</code> whitelist'da — ban qilib bo'lmadi.")

    try:
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  /gunban — Blokdan chiqarish
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gunban"), _GROUP_FILTER)
async def cmd_gunban(message: Message, command: CommandObject, bot: Bot) -> None:
    """/gunban [reply | user_id]"""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, _ = _resolve_target(message, command)
    if not target_id:
        await _deny(message, "ℹ️ Foydalanish: /gunban javob yoki <code>/gunban &lt;user_id&gt;</code>")
        return

    bm = BanManager(bot)
    ok = await bm.unban(target_id, message.chat.id, unbanned_by=caller.id)

    if ok:
        await message.answer(
            f"✅ <code>{target_id}</code> blokdan chiqarildi.\n"
            f"👮 Admin: @{caller.username or caller.id}"
        )
        await _reset_warn(target_id, message.chat.id)
        logger.info(f"[GUNBAN] user={target_id} chat={message.chat.id} by={caller.id}")
    else:
        await message.answer(f"⚠️ <code>{target_id}</code> bloklangan emas yoki xatolik.")

    try:
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  /gmute — Vaqtincha sukut
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gmute"), _GROUP_FILTER)
async def cmd_gmute(message: Message, command: CommandObject, bot: Bot) -> None:
    """/gmute [reply | user_id] [daqiqa]  — default 60 daq."""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, extra = _resolve_target(message, command)
    if not target_id:
        await _deny(message, "ℹ️ Foydalanish: /gmute javob yoki <code>/gmute &lt;user_id&gt; [daqiqa]</code>")
        return

    if await get_admin_role(target_id) is not None:
        await _deny(message, "⛔️ GuardBot adminini sukut qilib bo'lmaydi.")
        return

    # Daqiqani argdan yoki extra dan olish
    minutes = 60
    if extra and extra.split()[0].isdigit():
        minutes = min(int(extra.split()[0]), 10080)  # max 7 kun
    elif command.args:
        parts = command.args.split()
        last = parts[-1]
        if last.isdigit():
            minutes = min(int(last), 10080)

    until = datetime.utcnow() + timedelta(minutes=minutes)
    try:
        await bot.restrict_chat_member(
            message.chat.id, target_id,
            permissions=ChatPermissions(can_send_messages=False),
            until_date=until,
        )
    except TelegramAPIError as exc:
        await message.answer(f"❌ Xato: <code>{exc}</code>")
        return

    # Audit log
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=target_id, chat_id=message.chat.id,
                action=ActionType.WARN,
                reason=f"Gmute {minutes} daqiqa — admin {caller.id}",
            ))
    except Exception:
        pass

    await message.answer(
        f"🔇 <code>{target_id}</code> {minutes} daqiqaga sukut qilindi.\n"
        f"👮 Admin: @{caller.username or caller.id}"
    )
    if message.reply_to_message:
        try:
            await message.reply_to_message.delete()
        except TelegramAPIError:
            pass
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    logger.info(f"[GMUTE] user={target_id} chat={message.chat.id} min={minutes} by={caller.id}")


# ═══════════════════════════════════════════════════════════════════════════════
#  /gunmute — Sukutdan chiqarish
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gunmute"), _GROUP_FILTER)
async def cmd_gunmute(message: Message, command: CommandObject, bot: Bot) -> None:
    """/gunmute [reply | user_id]"""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, _ = _resolve_target(message, command)
    if not target_id:
        await _deny(message, "ℹ️ Foydalanish: /gunmute javob yoki <code>/gunmute &lt;user_id&gt;</code>")
        return

    try:
        await bot.restrict_chat_member(
            message.chat.id, target_id,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_audios=True, can_send_documents=True,
                can_send_photos=True, can_send_videos=True,
                can_send_video_notes=True, can_send_voice_notes=True,
                can_send_polls=True, can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
    except TelegramAPIError as exc:
        await message.answer(f"❌ Xato: <code>{exc}</code>")
        return

    await message.answer(
        f"🔊 <code>{target_id}</code> sukutdan chiqarildi.\n"
        f"👮 Admin: @{caller.username or caller.id}"
    )
    try:
        await message.delete()
    except TelegramAPIError:
        pass
    logger.info(f"[GUNMUTE] user={target_id} chat={message.chat.id} by={caller.id}")


# ═══════════════════════════════════════════════════════════════════════════════
#  /gwarn — Ogohlantirish (3 ta = auto ban)
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gwarn"), _GROUP_FILTER)
async def cmd_gwarn(message: Message, command: CommandObject, bot: Bot) -> None:
    """/gwarn [reply | user_id] [sabab]  — 3 ta warn = auto ban."""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, reason = _resolve_target(message, command)
    if not target_id:
        await _deny(message, "ℹ️ Foydalanish: /gwarn javob yoki <code>/gwarn &lt;user_id&gt; [sabab]</code>")
        return

    if await get_admin_role(target_id) is not None:
        await _deny(message, "⛔️ GuardBot adminiga ogohlantirish berish mumkin emas.")
        return

    count = await _increment_warn(target_id, message.chat.id)
    reason_text = reason or "Qoidabuzarlik"

    # Audit
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=target_id, chat_id=message.chat.id,
                action=ActionType.WARN,
                reason=f"Warn #{count}: {reason_text} — admin {caller.id}",
            ))
    except Exception:
        pass

    if count >= _WARN_LIMIT:
        # 3 ta ogohlantirish → BAN
        bm = BanManager(bot)
        await bm.execute_ban(
            user_id=target_id,
            chat_id=message.chat.id,
            reason=f"3 ta ogohlantirish limiti: {reason_text}",
            evidence={"command": "gwarn", "count": count, "by": caller.id},
            risk_score=90.0,
            banned_by=caller.id,
        )
        await _reset_warn(target_id, message.chat.id)
        await message.answer(
            f"🚫 <code>{target_id}</code> <b>3 ta ogohlantirish</b> to'pladi — BAN qilindi!\n"
            f"📝 Oxirgi sabab: {reason_text[:150]}"
        )
        logger.info(f"[GWARN→BAN] user={target_id} chat={message.chat.id} by={caller.id}")
    else:
        remaining = _WARN_LIMIT - count
        await message.answer(
            f"⚠️ <code>{target_id}</code> ogohlantirish oldi [{count}/{_WARN_LIMIT}]\n"
            f"📝 Sabab: {reason_text[:150]}\n"
            f"💡 Yana {remaining} ta ogohlantirishdan so'ng ban qilinadi."
        )
        logger.info(f"[GWARN] user={target_id} chat={message.chat.id} count={count} by={caller.id}")

    try:
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  /ginfo — Foydalanuvchi ma'lumoti
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("ginfo"), _GROUP_FILTER)
async def cmd_ginfo(message: Message, command: CommandObject, bot: Bot) -> None:
    """/ginfo [reply | user_id] — foydalanuvchi holati."""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    target_id, _ = _resolve_target(message, command)

    # Agar reply yoki arg yo'q — o'zi haqida
    if not target_id:
        target_id = caller.id

    # DB dan ma'lumot
    is_banned = False
    ban_reason = None
    ban_date = None
    warn_count = await _get_warn_count(target_id, message.chat.id)
    is_whitelisted = False
    guardbot_role = None

    try:
        async with get_session() as s:
            # Ban holati
            banned_row = (await s.execute(
                select(BannedUser).where(
                    BannedUser.user_id == target_id,
                    BannedUser.chat_id == message.chat.id,
                )
            )).scalar_one_or_none()
            if banned_row:
                is_banned = True
                ban_reason = banned_row.reason
                ban_date = banned_row.banned_at

            # Whitelist
            wl = (await s.execute(
                select(Whitelist).where(Whitelist.user_id == target_id)
            )).scalar_one_or_none()
            is_whitelisted = wl is not None

            # Scan soni
            scan_count = (await s.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.user_id == target_id,
                    AuditLog.action == ActionType.SCAN,
                )
            )).scalar_one()
    except Exception:
        scan_count = 0

    # GuardBot roli
    guardbot_role = await get_admin_role(target_id)

    # Telegram a'zolik holati
    tg_status = "—"
    try:
        cm = await bot.get_chat_member(message.chat.id, target_id)
        tg_status = cm.status
    except TelegramAPIError:
        tg_status = "topilmadi"

    lines = [
        f"👤 <b>Foydalanuvchi ma'lumoti</b>",
        f"🆔 ID: <code>{target_id}</code>",
        f"📊 Telegram holat: <b>{tg_status}</b>",
        f"🛡 GuardBot roli: <b>{guardbot_role.value if guardbot_role else 'oddiy foydalanuvchi'}</b>",
        "",
        f"🚫 Bloklangan: {'Ha' if is_banned else 'Yo\'q'}",
    ]
    if is_banned:
        lines.append(f"   📝 Sabab: {(ban_reason or '—')[:100]}")
        if ban_date:
            lines.append(f"   📅 Sana: {ban_date:%d.%m.%Y %H:%M}")
    lines += [
        f"📝 Whitelist: {'Ha ✅' if is_whitelisted else 'Yo\'q'}",
        f"⚠️ Ogohlantirishlar: <b>{warn_count}/{_WARN_LIMIT}</b>",
        f"🔍 Tekshiruvlar: <b>{scan_count}</b>",
    ]

    sent = await message.answer("\n".join(lines))
    # Admin buyrug'ini o'chiramiz
    try:
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  /gstatus — Guruh himoya holati
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gstatus"), _GROUP_FILTER)
async def cmd_gstatus(message: Message, bot: Bot) -> None:
    """/gstatus — bu guruhning himoya holati va statistikasi."""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    chat_id = message.chat.id
    is_prot = await _is_protected(chat_id)

    # Statistika
    ban_count = 0
    scan_count = 0
    warn_count_total = 0
    try:
        async with get_session() as s:
            ban_count = (await s.execute(
                select(func.count(BannedUser.id)).where(BannedUser.chat_id == chat_id)
            )).scalar_one()
            scan_count = (await s.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.chat_id == chat_id,
                    AuditLog.action == ActionType.SCAN,
                )
            )).scalar_one()
            warn_count_total = (await s.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.chat_id == chat_id,
                    AuditLog.action == ActionType.WARN,
                )
            )).scalar_one()
    except Exception:
        pass

    # Bot admin ekanini tekshiramiz
    bot_me = await bot.get_me()
    bot_is_admin = False
    try:
        bm_status = await bot.get_chat_member(chat_id, bot_me.id)
        bot_is_admin = bm_status.status in ("administrator", "creator")
    except TelegramAPIError:
        pass

    status_icon = "🟢" if (is_prot and bot_is_admin) else "🔴"
    lines = [
        f"{status_icon} <b>Guruh himoya holati</b>",
        f"💬 <b>{message.chat.title}</b>",
        f"🆔 <code>{chat_id}</code>",
        "",
        f"🛡 Himoyalangan: {'Ha ✅' if is_prot else 'Yo\'q ❌ — /add_group orqali qo\'shing'}",
        f"🤖 Bot admin: {'Ha ✅' if bot_is_admin else 'Yo\'q ❌'}",
        "",
        f"📊 <b>Statistika:</b>",
        f"🚫 Jami banlar: <b>{ban_count}</b>",
        f"⚠️ Jami ogohlantirishlar: <b>{warn_count_total}</b>",
        f"🔍 Jami tekshiruvlar: <b>{scan_count}</b>",
        "",
        f"⚙️ <b>Sozlamalar:</b>",
        f"   Auto-ban chegarasi: <b>{settings.AUTO_BAN_RISK_THRESHOLD}%</b>",
        f"   Admin tasdiqi: <b>{settings.ADMIN_CONFIRM_RISK_THRESHOLD}%</b>",
        f"   Rate limit: <b>{settings.RATE_LIMIT_FORWARDS}</b> forward/{settings.RATE_LIMIT_WINDOW_SECONDS}s",
    ]

    await message.answer("\n".join(lines))
    try:
        await message.delete()
    except TelegramAPIError:
        pass


# ═══════════════════════════════════════════════════════════════════════════════
#  /gclean — So'nggi N ta xabarni tozalash
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("gclean"), _GROUP_FILTER)
async def cmd_gclean(message: Message, command: CommandObject, bot: Bot) -> None:
    """/gclean [N] — so'nggi N ta xabarni o'chirish (max 100, default 10)."""
    caller = message.from_user
    if not caller or not await _is_group_admin(bot, message.chat.id, caller.id):
        await _deny(message)
        return

    n = 10
    if command.args and command.args.strip().isdigit():
        n = min(int(command.args.strip()), 100)

    chat_id = message.chat.id
    start_msg_id = message.message_id
    deleted = 0

    for msg_id in range(start_msg_id, max(start_msg_id - n - 1, 0), -1):
        try:
            await bot.delete_message(chat_id, msg_id)
            deleted += 1
        except TelegramAPIError:
            pass  # xabar allaqachon o'chirilgan yoki ruxsat yo'q

    import asyncio
    try:
        sent = await bot.send_message(chat_id, f"🧹 {deleted} ta xabar tozalandi.")
        await asyncio.sleep(4)
        await sent.delete()
    except TelegramAPIError:
        pass

    logger.info(f"[GCLEAN] chat={chat_id} deleted={deleted} by={caller.id}")
