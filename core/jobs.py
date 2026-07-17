"""
Background Jobs (arq-based)
============================
Og'ir media tahlil ishlarini Telegram event loop'dan ajratib,
Redis queue orqali background'da ishlatadi.

Ishlatish: arq core.jobs.WorkerSettings

Arxitektura:
  1. Handler media fayl kelganda job.enqueue() qiladi  → Redis queue'ga tushadi
  2. arq worker job'ni olib, analyze_media_job() ni ishga tushiradi
  3. Natija bazaga yoziladi, zarur bo'lsa adminlarga xabar yuboriladi

arq o'rnatish: pip install arq
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from sqlalchemy import select, update

from database.db import get_session
from database.models import ProtectedPost, AuditLog, ActionType
from utils.logger import logger

try:
    import arq
    _HAS_ARQ = True
except ImportError:
    _HAS_ARQ = False
    logger.info("arq o'rnatilmagan — background jobs o'chirilgan, sinxron rejimda ishlaydi.")


# ─── Job funksiyalari ─────────────────────────────────────────────────────────

async def analyze_channel_media_job(
    ctx: dict,
    post_id: int,
    file_id: str,
    bot_token: str,
) -> dict:
    """
    arq worker job: kanal postidagi medianing pHash + OCR ni hisoblab bazaga yozadi.

    ctx: arq tomonidan boshqariladi (redis connection va boshqalar)
    """
    from aiogram import Bot
    from core.media_processor import download_and_analyze

    logger.info(f"[JOB] Kanal media tahlili boshlandi: post_id={post_id}")
    bot = Bot(token=bot_token)
    try:
        result = await download_and_analyze(bot, file_id)
        if result is None:
            logger.warning(f"[JOB] Media yuklab bo'lmadi: post_id={post_id}")
            return {"status": "failed", "post_id": post_id}

        # ProtectedPost ni yangilash
        async with get_session() as session:
            await session.execute(
                update(ProtectedPost)
                .where(ProtectedPost.id == post_id)
                .values(
                    content_hash=result["content_hash"],
                    phash=result["phash"],
                    ocr_text=result.get("ocr_text", ""),
                )
            )

        logger.info(
            f"[JOB] Media tahlili yakunlandi: post_id={post_id} "
            f"phash={str(result.get('phash', ''))[:16]}..."
        )
        return {"status": "ok", "post_id": post_id, "phash": result.get("phash")}
    finally:
        await bot.session.close()


async def analyze_group_media_job(
    ctx: dict,
    user_id: int,
    chat_id: int,
    message_id: int,
    file_id: str,
    bot_token: str,
) -> dict:
    """
    arq worker job: guruh xabaridagi rasmni tahlil qilib, agar leak bo'lsa harakat qiladi.

    v3 TUZATISH: ban endi `chat_id` (rasm QAYERDA topilgani) emas, balki mos
    kelgan postning `source_chat_id`si (rasm ASLIDA QAYSI himoyalangan
    guruhga tegishli ekani) da amalga oshiriladi -- chunki bot faqat manba
    guruhda admin, boshqa (begona) guruhda emas. `chat_id`da ham (agar bot
    u yerda ham admin bo'lsa) qo'shimcha bonus ban/delete urinib ko'riladi.

    Agar hech qanday moslik topilmasa va bu rasm bizning O'Z himoyalangan
    guruhimizga tegishli bo'lsa -- original kontent sifatida fingerprint
    bazasiga qo'shiladi (kelajakda boshqa joyga sizib chiqsa aniqlash uchun).
    """
    from aiogram import Bot
    from aiogram.exceptions import TelegramAPIError
    from core.media_processor import download_and_analyze
    from core.content_analyzer import phash_similarity, text_similarity
    from core.behavior_engine import BehaviorEngine
    from core.decision_matrix import DecisionMatrix, RiskFactors, Action
    from core.ban_manager import BanManager
    from config import settings
    from utils.redis_client import redis_client
    from database.models import ProtectedGroup

    logger.info(f"[JOB] Guruh media tahlili: user={user_id} chat={chat_id} msg={message_id}")
    bot = Bot(token=bot_token)
    try:
        result = await download_and_analyze(bot, file_id)
        if result is None:
            return {"status": "failed"}

        # Himoyalangan postlar bilan solishtirish (kanal + guruh manbalari)
        async with get_session() as session:
            db_result = await session.execute(
                select(
                    ProtectedPost.id,
                    ProtectedPost.content_hash,
                    ProtectedPost.phash,
                    ProtectedPost.ocr_text,
                    ProtectedPost.source_chat_id,
                )
            )
            known_posts = db_result.all()

        hash_score = 0.0
        matched_post_id: Optional[int] = None
        matched_source_chat_id: Optional[int] = None
        ocr_score = 0.0

        incoming_hash = result["content_hash"]
        incoming_phash = result.get("phash")
        incoming_ocr = result.get("ocr_text", "")

        for post in known_posts:
            # Exact hash
            if post.content_hash == incoming_hash:
                hash_score = 1.0
                matched_post_id = post.id
                matched_source_chat_id = post.source_chat_id
                break
            # pHash
            if incoming_phash and post.phash:
                score = phash_similarity(incoming_phash, post.phash)
                if score > hash_score:
                    hash_score = score
                    matched_post_id = post.id
                    matched_source_chat_id = post.source_chat_id
            # OCR
            if incoming_ocr and post.ocr_text:
                score = text_similarity(incoming_ocr, post.ocr_text)
                if score > ocr_score:
                    ocr_score = score
                    if matched_post_id is None:
                        matched_post_id = post.id
                        matched_source_chat_id = post.source_chat_id

        behavior_engine = BehaviorEngine(redis_client)
        behavior = await behavior_engine.evaluate(user_id)

        factors = RiskFactors(
            hash_match_score=hash_score,
            ocr_similarity_score=ocr_score,
            watermark_verified=0.0,
            behavior_score=behavior.forward_rate_score,
            account_age_score=behavior.account_age_score,
        )
        dm = DecisionMatrix()
        decision = dm.decide(factors)

        evidence = {
            "type": "media_scan",
            "message_id": message_id,
            "leaked_to_chat": chat_id,
            "matched_post_id": matched_post_id,
            "source_chat_id": matched_source_chat_id,
            "hash_score": hash_score,
            "ocr_score": ocr_score,
        }

        async with get_session() as session:
            session.add(AuditLog(
                user_id=user_id, chat_id=chat_id, action=ActionType.SCAN,
                reason="Media forward tekshiruvi (background job)",
                evidence=json.dumps(evidence, ensure_ascii=False),
                risk_score=decision.risk_score,
            ))

        if decision.action == Action.AUTO_BAN and matched_source_chat_id is not None:
            ban_manager = BanManager(bot)
            await ban_manager.execute_ban(
                user_id=user_id, chat_id=matched_source_chat_id,
                reason=f"Media leak: risk {decision.risk_score}%",
                evidence=evidence, risk_score=decision.risk_score,
            )
            # Bonus: sizib chiqqan joyda ham (agar bot u yerda admin bo'lsa)
            if matched_source_chat_id != chat_id:
                try:
                    await bot.ban_chat_member(
                        chat_id=chat_id, user_id=user_id, revoke_messages=True
                    )
                except TelegramAPIError:
                    pass
            try:
                await bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
        elif decision.action == Action.ADMIN_CONFIRM:
            from core.ban_manager import BanManager as BM
            bm = BM(bot)
            await bm._notify_admins(
                user_id, chat_id,
                f"Media shubhali (risk {decision.risk_score}%, manba={matched_source_chat_id})",
                evidence, decision.risk_score,
            )
        elif matched_post_id is None:
            # Hech qanday moslik topilmadi -- agar bu bizning O'Z himoyalangan
            # guruhimiz bo'lsa, original kontent sifatida fingerprint qilamiz.
            try:
                async with get_session() as session:
                    pg = (await session.execute(
                        select(ProtectedGroup).where(
                            ProtectedGroup.chat_id == chat_id,
                            ProtectedGroup.is_active == True,  # noqa: E712
                        )
                    )).scalar_one_or_none()
                    if pg:
                        session.add(ProtectedPost(
                            group_id=pg.id,
                            source_chat_id=chat_id,
                            message_id=message_id,
                            content_hash=incoming_hash,
                            phash=incoming_phash,
                            ocr_text=incoming_ocr or None,
                            media_file_id=file_id,
                            media_analyzed=True,
                        ))
            except Exception as exc:
                logger.warning(f"[JOB] Media fingerprint saqlashda xato: {exc}")

        logger.info(
            f"[JOB] Guruh media tahlili yakunlandi: risk={decision.risk_score} "
            f"action={decision.action.value} matched_source={matched_source_chat_id}"
        )
        return {"status": "ok", "risk_score": decision.risk_score, "action": decision.action.value}
    finally:
        await bot.session.close()


# ─── arq Worker sozlamalari ───────────────────────────────────────────────────

class WorkerSettings:
    """
    arq worker konfiguratsiyasi.
    Ishga tushirish: arq core.jobs.WorkerSettings
    """
    functions = [analyze_channel_media_job, analyze_group_media_job]
    redis_settings = None  # runtime'da config'dan yuklanadi

    @classmethod
    def get_settings(cls, redis_url: str):
        if not _HAS_ARQ:
            raise RuntimeError("arq o'rnatilmagan. `pip install arq` qiling.")
        from arq.connections import RedisSettings
        cls.redis_settings = RedisSettings.from_dsn(redis_url)
        return cls


# ─── Enqueue helper (arq o'rnatilmagan bo'lsa sinxron fallback) ──────────────

async def enqueue_channel_media_analysis(
    post_id: int, file_id: str, bot_token: str
) -> None:
    """Kanal media tahlilini queue'ga qo'shadi yoki sinxron ishlatadi."""
    if not _HAS_ARQ:
        # arq yo'q — sinxron ravishda ishlatamiz (thread pool bilan)
        await _sync_channel_media(post_id, file_id, bot_token)
        return

    from arq.connections import RedisSettings, create_pool
    from config import settings
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis.enqueue_job(
        "analyze_channel_media_job",
        post_id=post_id, file_id=file_id, bot_token=bot_token,
    )
    await redis.aclose()


async def enqueue_group_media_analysis(
    user_id: int, chat_id: int, message_id: int, file_id: str, bot_token: str
) -> None:
    """Guruh media tahlilini queue'ga qo'shadi yoki sinxron ishlatadi."""
    if not _HAS_ARQ:
        await _sync_group_media(user_id, chat_id, message_id, file_id, bot_token)
        return

    from arq.connections import RedisSettings, create_pool
    from config import settings
    redis = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    await redis.enqueue_job(
        "analyze_group_media_job",
        user_id=user_id, chat_id=chat_id, message_id=message_id,
        file_id=file_id, bot_token=bot_token,
    )
    await redis.aclose()


# ─── Sinxron fallback ─────────────────────────────────────────────────────────

async def _sync_channel_media(post_id: int, file_id: str, bot_token: str) -> None:
    """arq yo'q bo'lganda kanal media tahlilini to'g'ridan-to'g'ri ishlatadi."""
    import asyncio
    asyncio.create_task(
        analyze_channel_media_job({}, post_id, file_id, bot_token)
    )


async def _sync_group_media(
    user_id: int, chat_id: int, message_id: int, file_id: str, bot_token: str
) -> None:
    """arq yo'q bo'lganda guruh media tahlilini to'g'ridan-to'g'ri ishlatadi."""
    import asyncio
    asyncio.create_task(
        analyze_group_media_job({}, user_id, chat_id, message_id, file_id, bot_token)
    )
