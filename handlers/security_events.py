"""
Security Events Handler (Security Engine v3)
===============================================
GuardBot'ning "professional Telegram Security System" qatlami. Mavjud
`handlers/group_events.py` (leak/spam himoyasi) bilan PARALLEL ishlaydi —
bir-biriga xalaqit bermaydi, chunki bu handler faqat:

  1. Yangi a'zo qo'shilganda (join)   -> Trust Score init, Raid Detector,
                                          kerak bo'lsa Captcha.
  2. Captcha javob callback'lari.
  3. Har guruh xabari uchun YENGIL risk-tekshiruv (Suspicious Monitor +
     Risk Analyzer) — ADMIN_ALERT/TEMP_RESTRICT/AUTO_BAN qarorlari uchun.

ishlaydi. Kontent-hash/watermark asosidagi "leak" tekshiruvi hamon
`group_events.py`da qoladi — ikkalasi bir xabarni ikki xil nuqtai
nazardan (kontent sizishi vs. xavfsizlik/xulq-atvor) tekshiradi.
"""
from __future__ import annotations

import json

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery, ChatPermissions, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)

from config import settings
from core.ban_manager import BanManager
from database.models import CaptchaStatus, SecurityDecision, SecurityEventType
from middlewares.role_check import get_admin_role
from security.audit import audit
from security.captcha import captcha_manager
from security.engine import SecurityEngine
from utils.logger import logger
from utils.redis_client import redis_client

router = Router(name="security_events")

security_engine = SecurityEngine(redis_client)

_MUTED_PERMISSIONS = ChatPermissions(
    can_send_messages=False, can_send_audios=False, can_send_documents=False,
    can_send_photos=False, can_send_videos=False, can_send_video_notes=False,
    can_send_voice_notes=False, can_send_polls=False, can_send_other_messages=False,
    can_add_web_page_previews=False,
)
_FULL_PERMISSIONS = ChatPermissions(
    can_send_messages=True, can_send_audios=True, can_send_documents=True,
    can_send_photos=True, can_send_videos=True, can_send_video_notes=True,
    can_send_voice_notes=True, can_send_polls=True, can_send_other_messages=True,
    can_add_web_page_previews=True,
)


# ═══════════════════════════════════════════════════════════════════════════
# ─── JOIN — Trust Score init + Raid Detector + Captcha ──────────────────────
# ═══════════════════════════════════════════════════════════════════════════

@router.message(F.new_chat_members)
async def on_new_chat_members(message: Message, bot: Bot) -> None:
    """
    Yangi a'zo qo'shilganda DARHOL tekshiruv:
      0. Bot ekanmi?
         - Guruhning o'z admini qo'shgan bot → o'tkazib yuborish
         - Boshqa bot → ogohlantirish (ban qilib bo'lmaydi)
      1. Whitelist'da bormi? → o'tkazib yuborish
      2. BannedUser jadvalida bormi? → darhol ban
      3. SecurityEngine evaluate_join → Risk / Raid / Captcha
         (xato bo'lsa — jim o'tamiz, bot crash qilmaydi)
    """
    from database.db import get_session
    from database.models import BannedUser, ProtectedGroup, Whitelist
    from sqlalchemy import select as sa_select

    chat_id = message.chat.id

    # Bu guruh himoyalangan ekanini bir marta tekshiramiz
    pg = None
    try:
        async with get_session() as s:
            pg = (await s.execute(
                sa_select(ProtectedGroup).where(
                    ProtectedGroup.chat_id == chat_id,
                    ProtectedGroup.is_active == True,  # noqa: E712
                )
            )).scalar_one_or_none()
    except Exception as exc:
        logger.error(f"[JOIN] ProtectedGroup tekshiruvda xato: {exc}")

    for member in message.new_chat_members:

        # ── 0a. Bot qo'shildi — admin bot bo'lsa o'tkazib yuboramiz ──────────
        if member.is_bot:
            # Bot guruhda admin ekanini tekshiramiz
            is_admin_bot = False
            try:
                cm = await bot.get_chat_member(chat_id, member.id)
                is_admin_bot = cm.status in ("administrator", "creator")
            except TelegramAPIError:
                pass

            if is_admin_bot:
                logger.info(f"[JOIN] bot={member.id} (@{member.username}) admin sifatida qo'shildi — o'tkazildi.")
                continue

            # Admin bo'lmagan bot — darhol BAN (doimiy, qayta qo'shila olmasin)
            if pg is not None:
                bot_ref = f"@{member.username}" if member.username else f"<code>{member.id}</code>"
                try:
                    await message.delete()
                except TelegramAPIError:
                    pass

                ban_manager = BanManager(bot)
                banned = await ban_manager.execute_ban(
                    user_id=member.id,
                    chat_id=chat_id,
                    reason="Ruxsatsiz bot guruhga qo'shildi",
                    evidence={"type": "unauthorized_bot_join", "bot": bot_ref},
                    risk_score=100.0,
                )
                action_text = "ban qilindi (guruhga qayta qo'shila olmaydi)" if banned else "ban qilib bo'lmadi (botga admin huquqi kerak yoki whitelist'da)"
                logger.warning(f"[JOIN-BOT-BAN] bot={member.id} chat={chat_id} {action_text}")
            continue

        # ── 0b. Himoyalangan guruh emasmi? → o'tkazib yuboramiz ──────────────
        if pg is None:
            continue

        # ── 1. Whitelist tekshiruvi ───────────────────────────────────────────
        try:
            async with get_session() as s:
                wl = (await s.execute(
                    sa_select(Whitelist).where(Whitelist.user_id == member.id)
                )).scalar_one_or_none()
            if wl:
                logger.info(f"[JOIN] user={member.id} whitelist'da — o'tkazildi.")
                continue
        except Exception:
            pass

        # ── 2. BannedUser — qaytib kirmoqchi → DARHOL BAN ────────────────────
        try:
            async with get_session() as s:
                banned_row = (await s.execute(
                    sa_select(BannedUser).where(
                        BannedUser.user_id == member.id,
                        BannedUser.chat_id == chat_id,
                    )
                )).scalar_one_or_none()
            if banned_row:
                ban_manager = BanManager(bot)
                await ban_manager.execute_ban(
                    member.id, chat_id,
                    reason="Banlangan foydalanuvchi qaytib kirdi",
                    evidence={
                        "type": "rebanned",
                        "original_reason": banned_row.reason[:200],
                        "original_at": str(banned_row.banned_at),
                    },
                    risk_score=100.0,
                )
                try:
                    await message.delete()
                except TelegramAPIError:
                    pass
                logger.warning(f"[JOIN-REBAN] user={member.id} chat={chat_id}")
                continue
        except Exception as exc:
            logger.error(f"[JOIN] BannedUser tekshiruvda xato: {exc}")

        # ── 3. SecurityEngine — try/except bilan himoyalangan ─────────────────
        has_username = bool(member.username)
        has_photo = False
        try:
            photos = await bot.get_user_profile_photos(member.id, limit=1)
            has_photo = photos.total_count > 0
        except TelegramAPIError:
            pass

        try:
            evaluation = await security_engine.evaluate_join(
                chat_id, member.id,
                username=member.username,
                full_name=member.full_name,
                has_username=has_username,
                has_photo=has_photo,
            )
        except Exception as exc:
            logger.error(f"[JOIN] SecurityEngine xato (Redis?): {exc} — o'tkazib yuborildi.")
            continue

        if evaluation.decision == SecurityDecision.AUTO_BAN:
            # Join da hech qachon avtoban emas — admindan so'raymiz
            await _confirm_join_ban(
                bot=bot,
                chat_id=chat_id,
                user_id=member.id,
                full_name=member.full_name,
                username=member.username,
                risk_score=evaluation.risk_score,
                join_msg_id=message.message_id,
            )
            await _restrict_member(bot, chat_id, member.id)
            continue

        if evaluation.raid.raid_mode_active:
            await _notify_admins_raid(bot, chat_id, evaluation.raid.join_count)

        if evaluation.require_captcha:
            await _restrict_member(bot, chat_id, member.id)
            await _send_captcha(bot, chat_id, member.id, member.full_name)
        elif evaluation.decision == SecurityDecision.TEMPORARY_RESTRICT:
            await _restrict_member(bot, chat_id, member.id)
            try:
                await audit.log_event(
                    chat_id=chat_id, user_id=member.id,
                    event_type=SecurityEventType.TEMP_RESTRICT,
                    details={"risk_score": evaluation.risk_score},
                )
            except Exception:
                pass


async def _restrict_member(bot: Bot, chat_id: int, user_id: int) -> None:
    try:
        await bot.restrict_chat_member(chat_id, user_id, permissions=_MUTED_PERMISSIONS)
    except TelegramAPIError as exc:
        logger.warning(f"[security_events] restrict xato: {exc}")


async def _send_captcha(bot: Bot, chat_id: int, user_id: int, full_name: str | None) -> None:
    challenge = await security_engine.request_captcha(chat_id, user_id)
    name = full_name or "Yangi a'zo"
    try:
        sent = await bot.send_message(
            chat_id,
            f"👋 <b>{name}</b>, xush kelibsiz!\n\n"
            f"🧩 {challenge.question}\n"
            f"⏱ {settings.CAPTCHA_TIMEOUT_SECONDS} soniya ichida javob bering, aks holda chetlatilasiz.",
            reply_markup=challenge.keyboard,
        )
        # message_id'ni sessiyaga yozamiz — timeout bo'lganda xabarni o'chirish uchun.
        from sqlalchemy import update as sa_update
        from database.db import get_session
        from database.models import CaptchaSession

        async with get_session() as session:
            await session.execute(
                sa_update(CaptchaSession)
                .where(CaptchaSession.id == challenge.session_id)
                .values(message_id=sent.message_id)
            )
    except TelegramAPIError as exc:
        logger.warning(f"[security_events] captcha yuborilmadi: {exc}")


async def _notify_admins_raid(bot: Bot, chat_id: int, join_count: int) -> None:
    text = (
        "🚨 <b>RAID MODE YOQILDI</b>\n\n"
        f"💬 Chat: <code>{chat_id}</code>\n"
        f"👥 {settings.RAID_WINDOW_SECONDS}s ichida <b>{join_count}</b> ta join aniqlandi.\n\n"
        "Captcha, Media, Link va Forward vaqtincha cheklandi.\n"
        "O'chirish uchun: <code>/raid_off</code>"
    )
    for admin_id in settings.super_admins:
        try:
            await bot.send_message(admin_id, text)
        except TelegramAPIError:
            pass


# ═══════════════════════════════════════════════════════════════════════════
# ─── CAPTCHA — javob callback'lari ───────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════

@router.callback_query(F.data.startswith("sec_captcha:"))
async def on_captcha_answer(cb: CallbackQuery, bot: Bot) -> None:
    try:
        _, session_id_str, answer = cb.data.split(":", 2)
        session_id = int(session_id_str)
    except (ValueError, AttributeError):
        await cb.answer()
        return

    session_row = await captcha_manager.get_session(session_id)
    if session_row is None:
        await cb.answer("Sessiya topilmadi.", show_alert=True)
        return

    if cb.from_user.id != session_row.user_id:
        await cb.answer("Bu captcha sizga tegishli emas.", show_alert=True)
        return

    status = await captcha_manager.submit_answer(session_id, cb.from_user.id, answer)
    chat_id = session_row.chat_id
    user_id = session_row.user_id

    if status == CaptchaStatus.PASSED:
        await security_engine.on_captcha_result(chat_id, user_id, True)
        try:
            await bot.restrict_chat_member(chat_id, user_id, permissions=_FULL_PERMISSIONS)
        except TelegramAPIError:
            pass
        await cb.answer("✅ Tasdiqlandi!")
        try:
            await cb.message.edit_text("✅ Captcha muvaffaqiyatli o'tildi. Xush kelibsiz!")
        except TelegramAPIError:
            pass

    elif status == CaptchaStatus.FAILED:
        await security_engine.on_captcha_result(chat_id, user_id, False)
        await cb.answer("❌ Noto'g'ri javob. Chetlatilyapsiz.", show_alert=True)
        await _kick_user(bot, chat_id, user_id, reason="Captcha muvaffaqiyatsiz")
        try:
            await cb.message.edit_text("❌ Captcha muvaffaqiyatsiz — foydalanuvchi chetlatildi.")
        except TelegramAPIError:
            pass

    elif status == CaptchaStatus.EXPIRED:
        await cb.answer("⏱ Vaqt tugadi.", show_alert=True)

    else:  # PENDING — hali urinish bor
        remaining = settings.CAPTCHA_MAX_ATTEMPTS - session_row.attempts
        await cb.answer(f"❌ Noto'g'ri. Qolgan urinish: {max(remaining, 0)}", show_alert=True)


async def _kick_user(bot: Bot, chat_id: int, user_id: int, reason: str) -> None:
    """Kick = ban + darhol unban (guruhdan chiqaradi, lekin butunlay bloklamaydi)."""
    if not settings.CAPTCHA_KICK_ON_FAIL:
        return
    try:
        await bot.ban_chat_member(chat_id, user_id)
        await bot.unban_chat_member(chat_id, user_id, only_if_banned=True)
    except TelegramAPIError as exc:
        logger.warning(f"[security_events] kick xato: {exc}")
    await audit.log_event(
        chat_id=chat_id, user_id=user_id,
        event_type=SecurityEventType.CAPTCHA_FAIL,
        details={"reason": reason, "kicked": True},
    )


# ═══════════════════════════════════════════════════════════════════════════
# ─── MESSAGE risk-check ──────────────────────────────────────────────────────
# NOTE: guruh xabarlaridagi yengil risk-tekshiruv (Suspicious Monitor +
# Risk Analyzer) atayin BU YERDA emas, `handlers/group_events.py` ichida
# (`_run_security_risk_check`, `on_group_text` / `on_group_media` oxirida)
# joylashtirilgan. Sababi: aiogram'da bitta update'ni birinchi mos kelgan
# handler "yutib oladi" — agar shu yerda ham xuddi shunday F.text|F.caption
# filtri bilan alohida handler qo'shilsa, u `group_events.py`dagi
# (birinchi ro'yxatdan o'tgan router) leak/spam handler bilan RAQOBATLASHIB,
# hech qachon ishga tushmaydi. Shu sabab ikkala tekshiruv (kontent-sizish
# va xulq-atvor riski) BITTA handler ichida ketma-ket chaqiriladi.
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# ─── /raid_off — Raid Mode'ni qo'lda o'chirish ──────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════

@router.message(F.text == "/raid_off")
async def cmd_raid_off(message: Message) -> None:
    if message.chat.type not in ("group", "supergroup"):
        return
    role = await get_admin_role(message.from_user.id)
    if role is None:
        return
    await security_engine.disable_raid_mode(message.chat.id, message.from_user.id)
    await message.answer("✅ Raid Mode o'chirildi. Guruh odatiy rejimga qaytdi.")


# ═══════════════════════════════════════════════════════════════════════════
# ─── JOIN CONFIRM — Shubhali yangi a'zoni admindan tasdiqlash ───────────────
# ═══════════════════════════════════════════════════════════════════════════

async def _confirm_join_ban(
    bot: Bot,
    chat_id: int,
    user_id: int,
    full_name: str | None,
    username: str | None,
    risk_score: float,
    join_msg_id: int,
) -> None:
    """
    Shubhali yangi a'zo uchun adminlarga confirm xabari yuboradi.
    Foydalanuvchi vaqtinchalik cheklangan (sukut) holda qoladi.
    Admin "Ban" bosguncha yoki "Ruxsat" bosguncha u yoza olmaydi.
    """
    user_ref = f"@{username}" if username else f"<code>{user_id}</code>"
    name_str = full_name or "Noma'lum"

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚫 Ban qilish",
            callback_data=f"join_ban:{user_id}:{chat_id}:{join_msg_id}",
        ),
        InlineKeyboardButton(
            text="✅ Ruxsat berish",
            callback_data=f"join_allow:{user_id}:{chat_id}",
        ),
    ]])
    text = (
        "🔶 <b>SHUBHALI YANGI A'ZO</b>\n\n"
        f"👤 Ism: <b>{name_str}</b>\n"
        f"🔗 Username: {user_ref}\n"
        f"🆔 ID: <code>{user_id}</code>\n"
        f"💬 Guruh: <code>{chat_id}</code>\n"
        f"📊 Risk score: <b>{risk_score:.0f}/100</b>\n\n"
        "⏸ Foydalanuvchi <b>vaqtinchalik sukut</b> qilindi.\n"
        "Quyidagi tugmalardan birini bosing:"
    )

    sent: set[int] = set()
    for admin_id in settings.super_admins:
        try:
            await bot.send_message(admin_id, text, reply_markup=kb)
            sent.add(admin_id)
        except TelegramAPIError:
            pass

    # DB adminlarga ham yuboramiz
    try:
        from database.db import get_session
        from database.models import Admin
        from sqlalchemy import select as sa_select
        async with get_session() as s:
            admins = (await s.execute(sa_select(Admin))).scalars().all()
        for a in admins:
            if a.telegram_id not in sent:
                try:
                    await bot.send_message(a.telegram_id, text, reply_markup=kb)
                except TelegramAPIError:
                    pass
    except Exception:
        pass

    logger.warning(
        f"[JOIN-CONFIRM] user={user_id} chat={chat_id} "
        f"risk={risk_score:.0f} — admindan tasdiq kutilmoqda."
    )


@router.callback_query(F.data.startswith("join_ban:"))
async def on_join_ban_confirm(cb: CallbackQuery, bot: Bot) -> None:
    """Admin shubhali a'zoni ban qilishni tasdiqladi."""
    if await get_admin_role(cb.from_user.id) is None:
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()

    parts = cb.data.split(":")
    user_id   = int(parts[1])
    chat_id   = int(parts[2])
    join_msg_id = int(parts[3])

    bm = BanManager(bot)
    ok = await bm.execute_ban(
        user_id=user_id,
        chat_id=chat_id,
        reason=f"Admin @{cb.from_user.username or cb.from_user.id} — shubhali join ban",
        evidence={"type": "join_confirm_ban", "by": cb.from_user.id},
        risk_score=100.0,
        banned_by=cb.from_user.id,
    )
    # Kirish xabarini o'chirishga urinamiz
    try:
        await bot.delete_message(chat_id=chat_id, message_id=join_msg_id)
    except TelegramAPIError:
        pass

    admin_ref = f"@{cb.from_user.username or cb.from_user.id}"
    if ok:
        await cb.message.edit_text(
            f"🚫 <code>{user_id}</code> ban qilindi.\n"
            f"👮 Admin: {admin_ref}"
        )
        logger.info(f"[JOIN-BAN] user={user_id} chat={chat_id} by={cb.from_user.id}")
    else:
        await cb.message.edit_text(
            f"⚠️ <code>{user_id}</code> whitelist'da — ban qilib bo'lmadi."
        )


@router.callback_query(F.data.startswith("join_allow:"))
async def on_join_allow(cb: CallbackQuery, bot: Bot) -> None:
    """Admin shubhali a'zoga ruxsat berdi — cheklovni olib tashlaymiz."""
    if await get_admin_role(cb.from_user.id) is None:
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()

    parts = cb.data.split(":")
    user_id = int(parts[1])
    chat_id = int(parts[2])

    try:
        await bot.restrict_chat_member(
            chat_id, user_id,
            permissions=_FULL_PERMISSIONS,
        )
    except TelegramAPIError as exc:
        logger.warning(f"[JOIN-ALLOW] restrict olib tashlashda xato: {exc}")

    # Trust score ni oshiramiz
    await security_engine.on_captcha_result(chat_id, user_id, passed=True)

    admin_ref = f"@{cb.from_user.username or cb.from_user.id}"
    await cb.message.edit_text(
        f"✅ <code>{user_id}</code> ga ruxsat berildi.\n"
        f"👮 Admin: {admin_ref}"
    )
    logger.info(f"[JOIN-ALLOW] user={user_id} chat={chat_id} by={cb.from_user.id}")
