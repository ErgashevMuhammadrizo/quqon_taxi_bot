"""
Channel Events Handler — MVP v2 (tuzatilgan logika)
=====================================================

ASOSIY QOIDA:
  Faqat Channel jadvalida ro'yxatdan o'tgan kanallar kuzatiladi.
  Kanal /add_channel orqali qo'shilmasa — bot u kanaldan kelgan postlarni
  himoyaga olmaydi va xabarlarni saqlamaydi.

  Kanalda saqlangan post qaysi guruhga, kanalga yoki suhbatga
  forward/nusxa qilinsa — o'sha joyda BAN.

VAZIFALAR:
  1. on_new_channel_post  — ro'yxatdagi kanalga yangi post → hash+watermark → DB
  2. on_my_chat_member    — bot kanaldan chiqarilganda → is_active=False + xabar
"""
from __future__ import annotations

from aiogram import Bot, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import ChatMemberUpdated, Message
from sqlalchemy import select

from config import settings
from core.content_analyzer import (
    compute_bytes_hash,
    compute_text_hash,
    generate_watermark_token,
)
from core.jobs import enqueue_channel_media_analysis
from database.db import get_session
from database.models import Admin, AdminRole, Channel, ProtectedPost
from utils.logger import logger

router = Router(name="channel_events")


# ─── Yordamchilar ─────────────────────────────────────────────────────────────

async def _alert_super_admins(bot: Bot, text: str) -> None:
    sent: set[int] = set()
    for uid in settings.super_admins:
        try:
            await bot.send_message(uid, text)
            sent.add(uid)
        except TelegramAPIError:
            pass
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(Admin).where(Admin.role == AdminRole.SUPER_ADMIN)
            )).scalars().all()
        for a in rows:
            if a.telegram_id not in sent:
                try:
                    await bot.send_message(a.telegram_id, text)
                except TelegramAPIError:
                    pass
    except Exception:
        pass


async def _alert_channel_admins(bot: Bot, channel: Channel, text: str) -> None:
    """Super Adminlar + kanalni qo'shgan admin + alert_chat_id."""
    await _alert_super_admins(bot, text)
    if channel.added_by and channel.added_by not in settings.super_admins:
        try:
            await bot.send_message(channel.added_by, text)
        except TelegramAPIError:
            pass
    if channel.alert_chat_id:
        try:
            await bot.send_message(channel.alert_chat_id, text)
        except TelegramAPIError:
            pass


# ─── my_chat_member ───────────────────────────────────────────────────────────

@router.my_chat_member()
async def on_my_chat_member(update: ChatMemberUpdated, bot: Bot) -> None:
    """Bot kanaldan chiqarilganda yoki huquqi o'zgarganda."""
    chat = update.chat
    if chat.type != "channel":
        return

    new_status = update.new_chat_member.status

    # Bot kanalga admin qilib qo'shildi
    if new_status in ("administrator", "creator"):
        try:
            async with get_session() as s:
                ch = (await s.execute(
                    select(Channel).where(Channel.chat_id == chat.id)
                )).scalar_one_or_none()
                if ch:
                    ch.is_active = True
                    ch.title     = chat.title or ch.title
        except Exception as exc:
            logger.error(f"[CHAN] DB xato: {exc}")
        logger.info(f"[CHAN] Bot kanalda admin: {chat.id} ({chat.title})")
        return

    # Bot chiqarildi / huquqi olib tashlandi
    if new_status in ("left", "kicked", "restricted", "member"):
        channel_title = chat.title or str(chat.id)
        channel_obj: Channel | None = None

        try:
            async with get_session() as s:
                ch = (await s.execute(
                    select(Channel).where(Channel.chat_id == chat.id)
                )).scalar_one_or_none()
                if ch and ch.is_active:
                    ch.is_active = False
                    channel_obj  = ch
        except Exception as exc:
            logger.error(f"[CHAN] DB xato: {exc}")

        if new_status in ("left", "kicked"):
            reason = "🚨 Bot kanaldan <b>chiqarib yuborildi</b>"
        elif new_status == "restricted":
            reason = "⚠️ Bot kanalda <b>cheklandi</b>"
        else:
            reason = "⚠️ Bot kanalidagi <b>admin huquqi olib tashlandi</b>"

        text = (
            f"{reason}\n\n"
            f"📢 Kanal: <b>{channel_title}</b>\n"
            f"🆔 ID: <code>{chat.id}</code>\n\n"
            "🔴 <b>Himoya to'xtatildi!</b>\n"
            "Botni qayta admin qilib, /add_channel orqali qayta ulang."
        )

        if channel_obj:
            await _alert_channel_admins(bot, channel_obj, text)
        else:
            await _alert_super_admins(bot, text)

        logger.warning(f"[CHAN] Bot chiqarildi: {chat.id} status={new_status}")


# ─── Yangi kanal posti ────────────────────────────────────────────────────────

async def _get_registered_channel(chat_id: int, title: str | None) -> Channel | None:
    """
    Faqat Channel jadvalida ro'yxatdan o'tgan kanallarni qaytaradi.
    Ro'yxatda bo'lmasa — None (bot bu kanaldan postlarni saqlamaydi).
    """
    try:
        async with get_session() as s:
            ch = (await s.execute(
                select(Channel).where(Channel.chat_id == chat_id)
            )).scalar_one_or_none()

            if ch is None:
                # Ro'yxatda yo'q — saqlamaymiz
                return None

            if not ch.is_active:
                ch.is_active = True
                ch.title     = title or ch.title

            return ch
    except Exception as exc:
        logger.error(f"[CHAN] Kanal olishda xato: {exc}")
        return None


@router.channel_post()
async def on_new_channel_post(message: Message, bot: Bot) -> None:
    """
    Kanalga yangi post kelganda.
    FAQAT Channel jadvalida ro'yxatdan o'tgan kanallar uchun ishlaydi.
    """
    channel = await _get_registered_channel(message.chat.id, message.chat.title)
    if channel is None:
        # Bu kanal ro'yxatda yo'q — e'tiborsiz o'tkazamiz
        return

    watermark_token = generate_watermark_token()
    content_hash    = ""
    media_file_id:  str | None = None
    text_excerpt:   str | None = None
    needs_media_job = False

    # ── Matn ─────────────────────────────────────────────────────────────────
    raw_text = message.text or message.caption or ""
    if raw_text:
        text_excerpt = raw_text[:500]
        content_hash = compute_text_hash(raw_text)

    # ── Media ─────────────────────────────────────────────────────────────────
    if message.photo:
        media_file_id   = message.photo[-1].file_id
        content_hash    = content_hash or compute_bytes_hash(media_file_id.encode())
        needs_media_job = True
    elif message.video:
        media_file_id = message.video.file_id
        content_hash  = content_hash or compute_bytes_hash(media_file_id.encode())
    elif message.document:
        media_file_id = message.document.file_id
        content_hash  = content_hash or compute_bytes_hash(media_file_id.encode())
        if message.document.mime_type and message.document.mime_type.startswith("image/"):
            needs_media_job = True
    elif message.animation:
        media_file_id = message.animation.file_id
        content_hash  = content_hash or compute_bytes_hash(media_file_id.encode())

    if not content_hash:
        content_hash = compute_bytes_hash(str(message.message_id).encode())

    # ── DB ga yozish ──────────────────────────────────────────────────────────
    try:
        async with get_session() as s:
            post = ProtectedPost(
                channel_id=channel.id,
                source_chat_id=message.chat.id,
                message_id=message.message_id,
                content_hash=content_hash,
                text_excerpt=text_excerpt,
                media_file_id=media_file_id,
                watermark_token=watermark_token,
                media_analyzed=(not needs_media_job),
            )
            s.add(post)
            await s.flush()
            await s.refresh(post)
            post_id = post.id
    except Exception as exc:
        logger.error(f"[CHAN] Post saqlashda xato: {exc}")
        return

    logger.info(
        f"[PROTECT] post={post_id} kanal={message.chat.id} "
        f"msg={message.message_id} hash={content_hash[:12]}..."
    )

    # ── Background media job ──────────────────────────────────────────────────
    if needs_media_job and media_file_id:
        try:
            await enqueue_channel_media_analysis(
                post_id=post_id,
                file_id=media_file_id,
                bot_token=settings.BOT_TOKEN,
            )
        except Exception as exc:
            logger.warning(f"[CHAN] Media job xatosi: {exc}")
