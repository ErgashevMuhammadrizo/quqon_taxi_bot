"""
Media Processor
===============
Rasm/video/hujjat fayllarini Telegram serveridan yuklab olib,
perceptual hash va OCR tahlilini bajaradi.

Og'ir CPU ishlarini asyncio executor orqali main event loop'dan ajratadi.
Background job sifatida arq (async redis queue) yordamida ishlatiladi.
"""
from __future__ import annotations

import asyncio
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from core.content_analyzer import (
    compute_bytes_hash,
    compute_perceptual_hash,
    extract_text_from_image,
)
from utils.logger import logger

# CPU-intensive ishlar uchun thread pool
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="guardbot-media")


async def download_file(bot: Bot, file_id: str) -> Optional[bytes]:
    """
    Telegram serveridan fayl baytlarini yuklab oladi.
    Xato bo'lsa None qaytaradi (bot ishlashda davom etadi).
    """
    try:
        file = await bot.get_file(file_id)
        if file.file_path is None:
            logger.warning(f"file_path topilmadi: file_id={file_id}")
            return None

        buffer = io.BytesIO()
        await bot.download_file(file.file_path, destination=buffer)
        buffer.seek(0)
        data = buffer.read()
        logger.debug(f"Fayl yuklandi: {file_id[:20]}... ({len(data)} bayt)")
        return data
    except TelegramAPIError as e:
        logger.warning(f"Fayl yuklab bo'lmadi (file_id={file_id[:20]}...): {e}")
        return None
    except Exception as e:
        logger.error(f"Kutilmagan xato fayl yuklab olishda: {e}", exc_info=True)
        return None


def _compute_media_hashes_sync(image_bytes: bytes) -> dict:
    """
    Sinxron (thread-safe) media tahlili - executor'da ishlaydi.
    Qaytadi: {content_hash, phash, ocr_text}
    """
    result = {
        "content_hash": compute_bytes_hash(image_bytes),
        "phash": None,
        "ocr_text": "",
    }
    try:
        result["phash"] = compute_perceptual_hash(image_bytes)
    except Exception as e:
        logger.debug(f"pHash hisoblashda xato: {e}")

    try:
        result["ocr_text"] = extract_text_from_image(image_bytes)
    except Exception as e:
        logger.debug(f"OCR da xato: {e}")

    return result


async def analyze_media_bytes(image_bytes: bytes) -> dict:
    """
    Rasm baytlarini async ravishda tahlil qiladi.
    CPU-intensive hisob-kitoblarni thread pool'da bajaradi.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        _executor, _compute_media_hashes_sync, image_bytes
    )


async def download_and_analyze(bot: Bot, file_id: str) -> Optional[dict]:
    """
    Faylni yuklab olib, tahlil qiladi.
    Qaytadi: {content_hash, phash, ocr_text} yoki None
    """
    image_bytes = await download_file(bot, file_id)
    if image_bytes is None:
        return None
    return await analyze_media_bytes(image_bytes)


async def get_largest_photo_file_id(message) -> Optional[str]:
    """
    Xabardagi eng katta o'lchamdagi rasmning file_id'sini qaytaradi.
    Telegram bir rasmni bir necha o'lchamda yuboradi — oxirgisi eng kattasi.
    """
    if message.photo:
        return message.photo[-1].file_id
    return None
