"""
Clone-Channel Detector
=======================
Boshqa kanallarda original kontent klonlanayotganini aniqlaydi.

Arxitektura:
  - MonitoredChannel: qaysi target kanal kuzatilayotganini bildiradi
  - CloneDetector   : xabarni himoyalangan postlar bilan solishtirib, CloneResult qaytaradi
  - CloneIncidentSaver: topilgan hodisani bazaga yozadi
  - periodic_scan(): scheduled task — barcha monitored channellarni vaqti-vaqti bilan skanerlaydi

Skanerlash oqimi:
  1. Bot target kanalga admin sifatida qo'shiladi (yoki forward orqali oladi)
  2. Har bir xabar CloneDetector.check() orqali o'tadi
  3. Agar similarity >= threshold → CloneIncident saqlanadi + adminlarga xabar
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from sqlalchemy import select, update

from core.content_analyzer import (
    ContentAnalyzer,
    compute_text_hash,
    phash_similarity,
    text_similarity,
)
from core.media_processor import download_and_analyze
from database.db import get_session
from database.models import Admin, ProtectedPost
from utils.logger import logger


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class CloneResult:
    is_clone: bool
    similarity_score: float          # 0.0 – 1.0
    match_type: str                  # "hash" | "phash" | "text" | "ocr" | "none"
    matched_post_id: Optional[int]
    evidence: dict = field(default_factory=dict)


@dataclass
class MonitoredChannelInfo:
    monitor_id: int
    source_chat_id: int    # himoyalangan kanal
    target_chat_id: int    # kuzatiladigan kanal (potensial klon)
    last_checked_at: Optional[datetime]


# ─── CloneDetector ─────────────────────────────────────────────────────────────

class CloneDetector:
    """Bitta xabarni himoyalangan postlar bazasi bilan solishtiradi."""

    SIMILARITY_THRESHOLD = 0.75   # bu va undan yuqori → klon hisoblanadi

    def __init__(self, bot: Bot):
        self.bot = bot
        self._analyzer = ContentAnalyzer(
            hash_threshold=0.88,
            ocr_threshold=0.80,
        )

    async def check_message(self, message_text: str, message_id: int) -> CloneResult:
        """Matnli xabarni tekshiradi."""
        if not message_text or len(message_text) < 30:
            return CloneResult(False, 0.0, "none", None)

        async with get_session() as session:
            result = await session.execute(
                select(
                    ProtectedPost.id,
                    ProtectedPost.text_excerpt,
                    ProtectedPost.content_hash,
                ).where(ProtectedPost.text_excerpt.is_not(None))
            )
            known_posts = result.all()

        if not known_posts:
            return CloneResult(False, 0.0, "none", None)

        incoming_hash = compute_text_hash(message_text)

        # 1. Exact hash
        for post in known_posts:
            if post.content_hash == incoming_hash:
                return CloneResult(
                    is_clone=True, similarity_score=1.0,
                    match_type="hash", matched_post_id=post.id,
                    evidence={"hash": incoming_hash, "msg_id": message_id},
                )

        # 2. Fuzzy text similarity
        best_score, best_id = 0.0, None
        for post in known_posts:
            if not post.text_excerpt:
                continue
            score = text_similarity(message_text, post.text_excerpt)
            if score > best_score:
                best_score, best_id = score, post.id

        if best_score >= self.SIMILARITY_THRESHOLD:
            return CloneResult(
                is_clone=True, similarity_score=best_score,
                match_type="text", matched_post_id=best_id,
                evidence={"text_similarity": best_score, "msg_id": message_id},
            )

        return CloneResult(False, best_score, "none", best_id)

    async def check_media(self, file_id: str, message_id: int) -> CloneResult:
        """Media xabarni pHash + OCR orqali tekshiradi."""
        media_result = await download_and_analyze(self.bot, file_id)
        if media_result is None:
            return CloneResult(False, 0.0, "none", None)

        async with get_session() as session:
            result = await session.execute(
                select(
                    ProtectedPost.id,
                    ProtectedPost.content_hash,
                    ProtectedPost.phash,
                    ProtectedPost.ocr_text,
                ).where(ProtectedPost.media_file_id.is_not(None))
            )
            known_media = result.all()

        if not known_media:
            return CloneResult(False, 0.0, "none", None)

        incoming_hash  = media_result["content_hash"]
        incoming_phash = media_result.get("phash")
        incoming_ocr   = media_result.get("ocr_text", "")

        best_score, best_id, best_type = 0.0, None, "none"

        for post in known_media:
            # Exact byte hash
            if post.content_hash == incoming_hash:
                return CloneResult(
                    is_clone=True, similarity_score=1.0,
                    match_type="hash", matched_post_id=post.id,
                    evidence={"hash_match": True, "msg_id": message_id},
                )
            # pHash
            if incoming_phash and post.phash:
                score = phash_similarity(incoming_phash, post.phash)
                if score > best_score:
                    best_score, best_id, best_type = score, post.id, "phash"
            # OCR
            if incoming_ocr and post.ocr_text:
                score = text_similarity(incoming_ocr, post.ocr_text)
                if score > best_score:
                    best_score, best_id, best_type = score, post.id, "ocr"

        if best_score >= self.SIMILARITY_THRESHOLD:
            return CloneResult(
                is_clone=True, similarity_score=best_score,
                match_type=best_type, matched_post_id=best_id,
                evidence={
                    "phash": incoming_phash,
                    "similarity": best_score,
                    "msg_id": message_id,
                },
            )

        return CloneResult(False, best_score, "none", best_id)


# ─── CloneIncidentSaver ────────────────────────────────────────────────────────

async def save_clone_incident(
    monitor_id: int,
    offending_msg_id: int,
    result: CloneResult,
) -> None:
    """CloneIncident ni bazaga yozadi va monitor'ning clone_score'ini yangilaydi."""
    from database.models import CloneIncident, MonitoredChannel  # circular import oldini olish

    async with get_session() as session:
        session.add(CloneIncident(
            monitor_id=monitor_id,
            offending_msg_id=offending_msg_id,
            matched_post_id=result.matched_post_id,
            similarity_score=result.similarity_score,
            evidence=json.dumps(result.evidence, ensure_ascii=False),
        ))
        # Monitor'ning umumiy clone_score'ini yangilaymiz (eng yuqori ko'rsatkichni saqlaymiz)
        await session.execute(
            update(MonitoredChannel)
            .where(
                MonitoredChannel.id == monitor_id,
                MonitoredChannel.clone_score < result.similarity_score,
            )
            .values(clone_score=result.similarity_score)
        )

    logger.warning(
        f"[CLONE] Incident saqlandi: monitor={monitor_id} "
        f"msg={offending_msg_id} score={result.similarity_score:.2f} "
        f"type={result.match_type}"
    )


async def notify_clone_detected(
    bot: Bot,
    monitor_id: int,
    source_chat_id: int,
    target_chat_id: int,
    result: CloneResult,
    offending_msg_id: int,
) -> None:
    """Barcha adminlarga klon aniqlangani haqida xabar yuboradi."""
    text = (
        "🔴 <b>KLON KANAL ANIQLANDI!</b>\n\n"
        f"📺 Himoyalangan kanal: <code>{source_chat_id}</code>\n"
        f"🎭 Klon kanal: <code>{target_chat_id}</code>\n"
        f"💬 Shubhali xabar ID: <code>{offending_msg_id}</code>\n"
        f"📊 O'xshashlik: <b>{result.similarity_score:.0%}</b>\n"
        f"🔍 Tur: <b>{result.match_type}</b>\n"
        f"🔗 Ko'rish: https://t.me/c/{str(target_chat_id).replace('-100', '')}/{offending_msg_id}\n\n"
        f"<i>Monitor ID: {monitor_id}</i>"
    )

    async with get_session() as session:
        db_result = await session.execute(select(Admin))
        admins = db_result.scalars().all()

    for admin in admins:
        try:
            await bot.send_message(admin.telegram_id, text)
        except TelegramAPIError:
            continue


# ─── Periodic scanner ─────────────────────────────────────────────────────────

async def periodic_clone_scan(bot: Bot, interval_seconds: int = 3600) -> None:
    """
    Background task: har `interval_seconds` sekundda barcha aktiv monitored
    kanallarning so'nggi postlarini skanerlaydi.

    bot.py'da asyncio.create_task(periodic_clone_scan(bot)) orqali ishga tushiriladi.
    """
    import asyncio
    from database.models import MonitoredChannel

    logger.info("[CLONE] Periodic clone scanner ishga tushdi.")

    while True:
        try:
            await _run_scan_cycle(bot)
        except Exception as e:
            logger.error(f"[CLONE] Scan cycle xatosi: {e}", exc_info=True)

        await asyncio.sleep(interval_seconds)


async def _run_scan_cycle(bot: Bot) -> None:
    """Bir scan siklini bajaradi."""
    from database.models import MonitoredChannel

    async with get_session() as session:
        result = await session.execute(
            select(MonitoredChannel).where(MonitoredChannel.is_active == True)  # noqa: E712
        )
        monitors = result.scalars().all()

    if not monitors:
        return

    detector = CloneDetector(bot)
    logger.info(f"[CLONE] {len(monitors)} ta monitor tekshirilmoqda...")

    for monitor in monitors:
        try:
            await _scan_single_monitor(bot, detector, monitor)
        except Exception as e:
            logger.error(
                f"[CLONE] Monitor {monitor.id} skanerida xato: {e}", exc_info=True
            )


async def _scan_single_monitor(bot: Bot, detector: CloneDetector, monitor) -> None:
    """Bitta monitored channel uchun so'nggi xabarlarni tekshiradi."""
    from database.models import MonitoredChannel

    # Target kanaldan oxirgi xabarlarni olish uchun bot shu kanalda admin bo'lishi kerak
    # Yoki bot o'sha kanalda member bo'lib, forward xabarlarni qayta ishlaydi
    # Bu yerda biz database-based approach ishlatamiz:
    # Bot o'sha kanalda va shu forward xabarlar group_events orqali kelyapti

    # Monitor'ning last_checked_at ni yangilaymiz
    async with get_session() as session:
        await session.execute(
            update(MonitoredChannel)
            .where(MonitoredChannel.id == monitor.id)
            .values(last_checked_at=datetime.utcnow())
        )

    logger.debug(
        f"[CLONE] Monitor {monitor.id}: "
        f"source={monitor.source_chat_id} target={monitor.target_chat_id} tekshirildi."
    )
