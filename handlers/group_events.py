"""
Group Events Handler — MVP v2 Final
=====================================

GURUH XAVFSIZLIGI QOIDALARI:
  Bot faqat /add_group orqali qo'shilgan (ProtectedGroup jadvalidagi)
  guruhlarda ishlaydi. Ro'yxatdan o'tmagan guruhlarda hech narsa qilmaydi.

  Quyidagi harakatlar BAN yoki ADMIN CONFIRM bilan tugaydi:

  1. REKLAMA / SPAM
     - Telegram kanal/guruh linki (@username, t.me/...) → darhol BAN
     - Reklama kalit so'zlari (sotamiz, chegirma, obuna...) → BAN/confirm
     - Ko'p URL (≥2 ta) → admin confirm
     - Media + URL caption → admin confirm

  2. BOT RELAY
     - from_user.is_bot = True → xabarni o'chir + adminlarga ogohlantirish
       (botni ban qilib bo'lmaydi, lekin guruhdan chiqarish tavsiya etiladi)

  3. HIMOYALANGAN KONTENT SIZIB CHIQISHI
     - Watermark aniqlansa → BAN
     - Hash/fuzzy matn mos kelsa → risk scoring → BAN / confirm

  4. GURUH HOLATI
     - Bot guruhdan chiqarilsa → is_active=False + super adminlarga xabar

  FORWARD BAN YO'Q:
     Forward qilish taqiqlanmagan — faqat kontent bazasi bilan mos kelsa
     yoki spam/reklama bo'lsa ban qilinadi.
"""
from __future__ import annotations

import json
import re as _re

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select

from config import settings
from core.ban_manager import BanManager
from core.behavior_engine import BehaviorEngine
from core.content_analyzer import (
    ContentAnalyzer,
    compute_text_hash,
    extract_watermark,
    text_similarity,
)
from core.decision_matrix import Action, DecisionMatrix, RiskFactors
from core.jobs import enqueue_group_media_analysis
from core.spam_detector import SpamResult, detect_spam_in_media, detect_spam_in_text
from database.db import get_session
from database.models import (
    ActionType, Admin, AdminRole, AuditLog,
    ProtectedGroup, ProtectedPost,
    SecurityActionType, SecurityDecision,
)
from middlewares.role_check import get_admin_role
from security.engine import SecurityEngine
from utils.logger import logger
from utils.redis_client import redis_client

# Telegram'ning maxsus "GroupAnonymousBot" ID'si — guruhda "Adminlar anonim"
# rejimi yoqilganda, admin yozgan xabarning from_user.id shu qiymatga teng
# bo'ladi (haqiqiy user_id EMAS).
ANONYMOUS_ADMIN_ID = 1087968824

router = Router(name="group_events")

_analyzer = ContentAnalyzer(
    hash_threshold=settings.HASH_SIMILARITY_THRESHOLD,
    ocr_threshold=settings.OCR_SIMILARITY_THRESHOLD,
)
_decision = DecisionMatrix()
_behavior = BehaviorEngine(redis_client)
_security_engine = SecurityEngine(redis_client)

MIN_TEXT_LEN = 15


# ═══════════════════════════════════════════════════════════════════════════════
#  Yordamchi funksiyalar
# ═══════════════════════════════════════════════════════════════════════════════

async def _is_protected_group(chat_id: int) -> bool:
    """Bu chat_id ProtectedGroup jadvalida aktiv holda ro'yxatdan o'tganmi?"""
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


async def _is_admin(user_id: int) -> bool:
    """GuardBot admini ekanini tekshiradi (DB + super_admins config)."""
    return (await get_admin_role(user_id)) is not None


async def _is_group_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    """
    Foydalanuvchi shu guruhning Telegram admini yoki GuardBot admini ekanini
    tekshiradi. Ikkalasidan biri rost bo'lsa True qaytaradi.
    Botni spam tekshiruvidan himoya qilish uchun ishlatiladi.
    """
    # Anonim admin (guruhda "Adminlar anonim" yoqilgan) — Telegram bu
    # turdagi xabarlarni faqat haqiqiy adminlarga ruxsat beradi.
    if user_id == ANONYMOUS_ADMIN_ID:
        return True

    # GuardBot admini (DB + config) — tezkor tekshiruv
    if await get_admin_role(user_id) is not None:
        return True
    # Telegram guruh admini — API chaqiruv
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except TelegramAPIError:
        return False


_LINK_RE = _re.compile(r"(https?://|t\.me/|@[\w_]{4,})", _re.IGNORECASE)


async def _run_security_risk_check(
    bot: Bot, message: Message, user, action_type: SecurityActionType,
) -> bool:
    """
    Security Engine (v3) — Trust Score / Risk Analyzer / Suspicious Monitor
    orqali xulq-atvor asosidagi tekshiruv. Kontent-hash asosidagi leak
    tekshiruvidan MUSTAQIL ishlaydi (parallel signal).

    True qaytarsa — chaqiruvchi handler DARHOL to'xtashi kerak (AUTO_BAN
    yoki TEMPORARY_RESTRICT allaqachon qo'llanildi).
    """
    text = message.text or message.caption or ""
    is_link = bool(_LINK_RE.search(text))
    is_forward = message.forward_origin is not None or message.forward_from is not None
    is_mention = bool(message.entities and any(e.type == "mention" for e in message.entities))

    effective_action = action_type
    if action_type == SecurityActionType.MESSAGE:
        if is_forward:
            effective_action = SecurityActionType.FORWARD
        elif is_link:
            effective_action = SecurityActionType.LINK
        elif is_mention:
            effective_action = SecurityActionType.MENTION

    try:
        evaluation = await _security_engine.evaluate_action(
            message.chat.id, user.id, effective_action,
            is_link=is_link, is_forward=is_forward, is_mention=is_mention,
            message_text=text or None,
        )
    except Exception as exc:  # Security Engine xatosi asosiy leak-himoyani to'xtatmasin
        logger.error(f"[security] risk-check xato: {exc}")
        return False

    if evaluation.decision == SecurityDecision.AUTO_BAN:
        await _do_ban(
            bot=bot, user_id=user.id, chat_id=message.chat.id, message=message,
            reason="Security Engine: xulq-atvor risk score 100+",
            evidence={"risk_score": evaluation.risk_score, "factors": evaluation.breakdown},
            risk_score=evaluation.risk_score,
        )
        return True

    if evaluation.decision == SecurityDecision.TEMPORARY_RESTRICT:
        try:
            await bot.restrict_chat_member(
                message.chat.id, user.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
        except TelegramAPIError as exc:
            logger.warning(f"[security] restrict xato: {exc}")
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        return True

    if evaluation.decision == SecurityDecision.ADMIN_ALERT:
        text_alert = (
            "⚠️ <b>Admin Alert (Security Engine)</b>\n\n"
            f"💬 Chat: <code>{message.chat.id}</code>\n"
            f"👤 User: <code>{user.id}</code>\n"
            f"📊 Risk score: <b>{evaluation.risk_score}</b>"
        )
        for admin_id in settings.super_admins:
            try:
                await bot.send_message(admin_id, text_alert)
            except TelegramAPIError:
                pass

    return False


async def _get_text_posts() -> list[tuple[int, str, str | None]]:
    """(post_id, text_excerpt, watermark_token) ro'yxati."""
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(
                    ProtectedPost.id,
                    ProtectedPost.text_excerpt,
                    ProtectedPost.watermark_token,
                ).where(ProtectedPost.text_excerpt.is_not(None))
            )).all()
            return [(r.id, r.text_excerpt, r.watermark_token) for r in rows]
    except Exception:
        return []


async def _get_watermark_map() -> dict[str, int]:
    """watermark_token → post_id."""
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(ProtectedPost.watermark_token, ProtectedPost.id)
                .where(ProtectedPost.watermark_token.is_not(None))
            )).all()
            return {r.watermark_token: r.id for r in rows}
    except Exception:
        return {}


# ─── Adminlarga xabar ─────────────────────────────────────────────────────────

async def _notify_admins(bot: Bot, text: str) -> None:
    sent: set[int] = set()
    for uid in settings.super_admins:
        try:
            await bot.send_message(uid, text)
            sent.add(uid)
        except TelegramAPIError:
            pass
    try:
        async with get_session() as s:
            admins = (await s.execute(select(Admin))).scalars().all()
        for a in admins:
            if a.telegram_id not in sent:
                try:
                    await bot.send_message(a.telegram_id, text)
                except TelegramAPIError:
                    pass
    except Exception:
        pass


async def _notify_confirm(
    bot: Bot,
    user_id: int,
    chat_id: int,
    message_id: int,
    reason: str,
    risk_score: float,
    evidence: dict,
) -> None:
    """Admin tasdiqi kerak — ban/e'tiborsiz tugmali xabar."""
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(
            text="🚫 Ban qilish",
            callback_data=f"confirm_ban:{user_id}:{chat_id}:{message_id}",
        ),
        InlineKeyboardButton(
            text="✅ E'tiborsiz",
            callback_data=f"ignore_ban:{user_id}:{chat_id}:{message_id}",
        ),
    ]])
    text = (
        "⚠️ <b>ADMIN TASDIQI KERAK</b>\n\n"
        f"👤 User: <code>{user_id}</code>\n"
        f"💬 Guruh: <code>{chat_id}</code>\n"
        f"📊 Risk: <b>{risk_score:.0f}%</b>\n"
        f"📝 Sabab: {reason}\n"
        f"<pre>{json.dumps(evidence, ensure_ascii=False, indent=2)[:400]}</pre>"
    )
    sent: set[int] = set()
    for uid in settings.super_admins:
        try:
            await bot.send_message(uid, text, reply_markup=kb)
            sent.add(uid)
        except TelegramAPIError:
            pass
    try:
        async with get_session() as s:
            admins = (await s.execute(select(Admin))).scalars().all()
        for a in admins:
            if a.telegram_id not in sent:
                try:
                    await bot.send_message(a.telegram_id, text, reply_markup=kb)
                except TelegramAPIError:
                    pass
    except Exception:
        pass


async def _do_ban(
    bot: Bot,
    user_id: int,
    chat_id: int,
    message: Message | None,
    reason: str,
    evidence: dict,
    risk_score: float,
) -> None:
    """Ban + xabarni o'chirish + adminlarga xabar."""
    bm = BanManager(bot)
    banned = await bm.execute_ban(
        user_id=user_id, chat_id=chat_id,
        reason=reason, evidence=evidence, risk_score=risk_score,
    )
    if message:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
    if not banned:
        return  # whitelist

    # Audit log
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=user_id, chat_id=chat_id,
                action=ActionType.BAN, reason=reason,
                evidence=json.dumps(evidence, ensure_ascii=False, default=str),
                risk_score=risk_score,
            ))
    except Exception:
        pass

    user_ref = (
        f"@{message.from_user.username}"
        if message and message.from_user and message.from_user.username
        else f"<code>{user_id}</code>"
    )
    chat_ref = (message.chat.title or str(chat_id)) if message else str(chat_id)
    await _notify_admins(
        bot,
        "🚨 <b>AVTOMATIK BAN</b>\n\n"
        f"👤 {user_ref} (<code>{user_id}</code>)\n"
        f"💬 Guruh: <b>{chat_ref}</b> (<code>{chat_id}</code>)\n"
        f"📊 Risk: <b>{risk_score:.0f}%</b>\n"
        f"📝 {reason}\n"
        f"<pre>{json.dumps(evidence, ensure_ascii=False, default=str)[:400]}</pre>",
    )
    logger.warning(
        f"[BAN] user={user_id} chat={chat_id} "
        f"risk={risk_score:.0f}% reason={reason}"
    )


# ─── Spam natijasini harakatga aylantirish ────────────────────────────────────

async def _handle_spam_result(
    bot: Bot,
    result: SpamResult,
    message: Message,
) -> bool:
    """
    SpamResult asosida DARHOL ban qiladi — hech qanday admin confirm yo'q.
    Reklama/spam topilsa xabar o'chiriladi, foydalanuvchi banlanadi,
    adminlarga bildirishnoma ketadi.

    True  → spam topildi va ban qilindi.
    False → spam emas, keyingi tekshiruvga o'tilsin.
    """
    if not result.is_spam:
        return False

    user = message.from_user
    if user is None:
        return False

    # Ishonch darajasi SPAM_CONFIRM_CONFIDENCE dan past bo'lsa — e'tiborsiz
    if result.confidence < settings.SPAM_CONFIRM_CONFIDENCE:
        return False

    conf = result.confidence
    evidence = {
        "spam_type":  result.spam_type,
        "confidence": round(conf, 3),
        "matched":    result.matched[:5],
        "message_id": message.message_id,
        "text_preview": (message.text or message.caption or "")[:200],
    }

    # ── Har qanday spam/reklama → DARHOL BAN ─────────────────────────────────
    # (confirm yo'q — reklama tashgan zahoti ban)
    await _do_ban(
        bot=bot,
        user_id=user.id,
        chat_id=message.chat.id,
        message=message,
        reason=f"Reklama/spam aniqlandi: {result.spam_type} ({conf:.0%})",
        evidence=evidence,
        risk_score=conf * 100,
    )
    return True


# ═══════════════════════════════════════════════════════════════════════════════
#  Handlers
# ═══════════════════════════════════════════════════════════════════════════════

# ─── my_chat_member: bot guruhdan chiqarilganda ──────────────────────────────

@router.my_chat_member()
async def on_my_chat_member_group(update: ChatMemberUpdated, bot: Bot) -> None:
    """Bot guruhdan chiqarilganda yoki admin huquqi olib tashlanganda."""
    chat = update.chat
    if chat.type not in ("group", "supergroup"):
        return

    new_status = update.new_chat_member.status

    if new_status in ("administrator", "creator"):
        try:
            async with get_session() as s:
                pg = (await s.execute(
                    select(ProtectedGroup).where(ProtectedGroup.chat_id == chat.id)
                )).scalar_one_or_none()
                if pg:
                    pg.is_active    = True
                    pg.bot_is_admin = True
                    pg.title        = chat.title or pg.title
        except Exception as exc:
            logger.error(f"[GROUP] DB xato: {exc}")
        return

    if new_status in ("left", "kicked", "restricted", "member"):
        try:
            async with get_session() as s:
                pg = (await s.execute(
                    select(ProtectedGroup).where(ProtectedGroup.chat_id == chat.id)
                )).scalar_one_or_none()
                if pg:
                    pg.is_active    = False
                    pg.bot_is_admin = False
        except Exception as exc:
            logger.error(f"[GROUP] DB xato: {exc}")

        reason_text = (
            "🚨 Bot guruhdan <b>chiqarib yuborildi</b>"
            if new_status in ("left", "kicked")
            else "⚠️ Bot guruhidagi <b>admin huquqi olib tashlandi</b>"
        )
        await _notify_admins(
            bot,
            f"{reason_text}\n\n"
            f"👥 Guruh: <b>{chat.title or chat.id}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>\n\n"
            "🔴 Ushbu guruhda himoya to'xtatildi!\n"
            "Botni qayta admin qilib, /add_group orqali qayta ulang.",
        )
        logger.warning(f"[GROUP] Bot chiqarildi: {chat.id} status={new_status}")


# ─── Bot relay xabarlari ──────────────────────────────────────────────────────

@router.message(F.from_user.is_bot.is_(True))
async def on_bot_message(message: Message, bot: Bot) -> None:
    """
    Guruhda boshqa bot xabar yubordi.
    Himoyalangan kontent mos kelsa — xabarni o'chir + adminlarga ogohlantirish.
    Botni ban qilib bo'lmaydi, lekin admin o'zi chiqarishi kerak.
    """
    if message.chat.type not in ("group", "supergroup"):
        return
    if not await _is_protected_group(message.chat.id):
        return

    text = message.text or message.caption or ""
    if not text:
        return

    # Watermark tekshiruvi
    wm = extract_watermark(text)
    if wm:
        wm_map = await _get_watermark_map()
        if wm in wm_map:
            try:
                await message.delete()
            except TelegramAPIError:
                pass
            await _notify_admins(
                bot,
                "🤖 <b>BOT RELAY — WATERMARK ANIQLANDI</b>\n\n"
                f"Guruh: <b>{message.chat.title}</b> (<code>{message.chat.id}</code>)\n"
                f"Bot: @{message.from_user.username or message.from_user.id}\n"
                f"Post ID: <code>{wm_map[wm]}</code>\n\n"
                "Xabar o'chirildi. ⚠️ Botni guruhdan qo'lda chiqaring.",
            )
            return

    # Matn o'xshashlik tekshiruvi
    if len(text) >= MIN_TEXT_LEN:
        posts = await _get_text_posts()
        for post_id, excerpt, _ in posts:
            if excerpt and text_similarity(text, excerpt) >= settings.HASH_SIMILARITY_THRESHOLD:
                try:
                    await message.delete()
                except TelegramAPIError:
                    pass
                await _notify_admins(
                    bot,
                    "🤖 <b>BOT RELAY — HIMOYALANGAN MATN ANIQLANDI</b>\n\n"
                    f"Guruh: <b>{message.chat.title}</b> (<code>{message.chat.id}</code>)\n"
                    f"Bot: @{message.from_user.username or message.from_user.id}\n"
                    f"Mos post ID: <code>{post_id}</code>\n\n"
                    "Xabar o'chirildi. ⚠️ Botni guruhdan qo'lda chiqaring.",
                )
                return

    # Spam tekshiruvi (bot reklama tarqatayaptimi)
    spam = detect_spam_in_text(text)
    if spam.is_spam and spam.confidence >= settings.SPAM_AUTO_BAN_CONFIDENCE:
        try:
            await message.delete()
        except TelegramAPIError:
            pass
        await _notify_admins(
            bot,
            "🤖 <b>BOT SPAM ANIQLANDI</b>\n\n"
            f"Guruh: <b>{message.chat.title}</b> (<code>{message.chat.id}</code>)\n"
            f"Bot: @{message.from_user.username or message.from_user.id}\n"
            f"Spam turi: <b>{spam.spam_type}</b> ({spam.confidence:.0%})\n"
            f"Topilgan: {', '.join(spam.matched[:3])}\n\n"
            "Xabar o'chirildi. ⚠️ Botni guruhdan qo'lda chiqaring.",
        )


# ─── Matn xabarlari ───────────────────────────────────────────────────────────

@router.message(
    F.text
    & F.chat.type.in_({"group", "supergroup"})
)
async def on_group_text(message: Message, bot: Bot) -> None:
    """
    Guruhga kelgan har bir matnli xabar — 24/7 kuzatuv.
    Tekshiruv tartibi:
      0. Security Engine — xulq-atvor risk
      1. Spam/reklama   → DARHOL BAN (confirm yo'q)
      2. Watermark      → DARHOL BAN
      3. Hash/fuzzy     → risk scoring → BAN / confirm
      *  Har xabar audit log ga yoziladi
    """
    if not await _is_protected_group(message.chat.id):
        return

    user = message.from_user
    if user is None or user.is_bot:
        return
    if await _is_group_admin(bot, message.chat.id, user.id):
        return

    text = message.text or ""
    text_len = len(text.strip())

    # ── Har xabar uchun scan log (24/7 monitoring) ────────────────────────────
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=user.id,
                chat_id=message.chat.id,
                action=ActionType.SCAN,
                reason="24/7 matn kuzatuvi",
                risk_score=0.0,
            ))
    except Exception:
        pass

    if text_len < MIN_TEXT_LEN:
        return

    # ── 0. Security Engine — xulq-atvor asosidagi risk-tekshiruv ──────────────
    if await _run_security_risk_check(bot, message, user, SecurityActionType.MESSAGE):
        return

    # ── 1. Spam / reklama tekshiruvi → DARHOL BAN ─────────────────────────────
    spam_result = detect_spam_in_text(text)
    if await _handle_spam_result(bot, spam_result, message):
        return

    # ── 2. Watermark tekshiruvi ───────────────────────────────────────────────
    wm = extract_watermark(text)
    if wm:
        wm_map = await _get_watermark_map()
        if wm in wm_map:
            await _do_ban(
                bot=bot, user_id=user.id, chat_id=message.chat.id,
                message=message,
                reason="Watermark aniqlandi — himoyalangan kontent",
                evidence={
                    "type": "watermark",
                    "token": wm,
                    "post_id": wm_map[wm],
                    "message_id": message.message_id,
                },
                risk_score=95.0,
            )
            return

    # ── 3. Hash / fuzzy matn mos kelish ───────────────────────────────────────
    posts = await _get_text_posts()
    if not posts:
        return

    known = [(pid, exc) for pid, exc, _ in posts if exc]
    analysis = _analyzer.analyze_text(text, known)

    # Scan natijasini audit log ga yangilaymiz
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=user.id, chat_id=message.chat.id,
                action=ActionType.SCAN,
                reason=f"Kontent tahlili: {'mos' if analysis.is_match else 'mos emas'} ({analysis.match_type})",
                risk_score=analysis.similarity * 100,
            ))
    except Exception:
        pass

    if not analysis.is_match:
        return

    behavior = await _behavior.evaluate(user.id)
    factors = RiskFactors(
        hash_match_score=analysis.similarity,
        ocr_similarity_score=0.0,
        watermark_verified=0.0,
        behavior_score=behavior.forward_rate_score,
        account_age_score=behavior.account_age_score,
    )
    decision = _decision.decide(factors)
    evidence = {
        "type": "text_match",
        "match_type": analysis.match_type,
        "similarity": round(analysis.similarity, 3),
        "matched_post": analysis.matched_post_id,
        "message_id": message.message_id,
    }

    if decision.action == Action.AUTO_BAN:
        await _do_ban(
            bot=bot, user_id=user.id, chat_id=message.chat.id,
            message=message,
            reason=f"Himoyalangan kontent: {analysis.match_type} ({decision.risk_score:.0f}%)",
            evidence=evidence,
            risk_score=decision.risk_score,
        )
    elif decision.action == Action.ADMIN_CONFIRM:
        await _notify_confirm(
            bot=bot, user_id=user.id, chat_id=message.chat.id,
            message_id=message.message_id,
            reason=f"Shubhali kontent — {analysis.match_type}",
            risk_score=decision.risk_score,
            evidence=evidence,
        )


# ─── Media xabarlari ──────────────────────────────────────────────────────────

@router.message(
    (F.photo | F.video | (F.document & F.document.mime_type.regexp(r"^image/")))
    & F.chat.type.in_({"group", "supergroup"})
)
async def on_group_media(message: Message, bot: Bot) -> None:
    """
    Guruhga kelgan media xabar — 24/7 kuzatuv.
    Tekshiruv tartibi:
      0. Security Engine — xulq-atvor risk
      1. Caption spam/reklama → DARHOL BAN (confirm yo'q)
      2. pHash + OCR background job
      *  Har media audit log ga yoziladi
    """
    if not await _is_protected_group(message.chat.id):
        return

    user = message.from_user
    if user is None or user.is_bot:
        return
    if await _is_group_admin(bot, message.chat.id, user.id):
        return

    caption = message.caption

    # ── Har media uchun scan log (24/7 monitoring) ────────────────────────────
    try:
        async with get_session() as s:
            s.add(AuditLog(
                user_id=user.id,
                chat_id=message.chat.id,
                action=ActionType.SCAN,
                reason="24/7 media kuzatuvi",
                risk_score=0.0,
            ))
    except Exception:
        pass

    # ── 0. Security Engine ────────────────────────────────────────────────────
    if await _run_security_risk_check(bot, message, user, SecurityActionType.MEDIA):
        return

    # ── 1. Caption spam tekshiruvi → DARHOL BAN ───────────────────────────────
    if caption:
        spam_result = detect_spam_in_media(caption)
        if await _handle_spam_result(bot, spam_result, message):
            return

    # ── 2. pHash / OCR background job ────────────────────────────────────────
    file_id: str | None = None
    if message.photo:
        file_id = message.photo[-1].file_id
    elif message.video:
        file_id = message.video.file_id
    elif message.document:
        file_id = message.document.file_id

    if not file_id:
        return

    try:
        await enqueue_group_media_analysis(
            user_id=user.id,
            chat_id=message.chat.id,
            message_id=message.message_id,
            file_id=file_id,
            bot_token=settings.BOT_TOKEN,
        )
    except Exception as exc:
        logger.warning(f"[GROUP] Media job xatosi: {exc}")


# ─── Sticker / GIF xabarlari ─────────────────────────────────────────────────

@router.message(
    (F.sticker | F.animation)
    & F.chat.type.in_({"group", "supergroup"})
)
async def on_group_sticker_anim(message: Message, bot: Bot) -> None:
    """
    Sticker va GIF'lar odatda zararli emas.
    Faqat guruh aktiv himoyada bo'lsa va xabar bot relay bo'lsa o'chirish.
    Bu handler asosan kelajakdagi kengaytma uchun placeholder.
    """
    pass  # hozircha aralashmaymiz


# ─── Admin confirm / ignore callbacks ────────────────────────────────────────

@router.callback_query(F.data.startswith("confirm_ban:"))
async def on_confirm_ban(cb: CallbackQuery, bot: Bot) -> None:
    if not await _is_admin(cb.from_user.id):
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()

    parts = cb.data.split(":")
    user_id = int(parts[1])
    chat_id = int(parts[2])
    msg_id  = int(parts[3])

    bm = BanManager(bot)
    await bm.execute_ban(
        user_id=user_id,
        chat_id=chat_id,
        reason=f"Admin @{cb.from_user.username or cb.from_user.id} tasdiqladi",
        evidence={"type": "admin_confirmed", "message_id": msg_id},
        risk_score=100.0,
        banned_by=cb.from_user.id,
    )
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramAPIError:
        pass
    await cb.message.edit_text(
        f"✅ <code>{user_id}</code> ban qilindi.\n"
        f"Admin: @{cb.from_user.username or cb.from_user.id}"
    )
    logger.info(f"[CONFIRM-BAN] user={user_id} by={cb.from_user.id}")


@router.callback_query(F.data.startswith("ignore_ban:"))
async def on_ignore_ban(cb: CallbackQuery) -> None:
    if not await _is_admin(cb.from_user.id):
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()

    user_id_s = cb.data.split(":")[1]
    await cb.message.edit_text(
        f"➖ <code>{user_id_s}</code> e'tiborsiz qoldirildi.\n"
        f"Admin: @{cb.from_user.username or cb.from_user.id}"
    )
    logger.info(f"[IGNORE-BAN] user={user_id_s} by={cb.from_user.id}")
