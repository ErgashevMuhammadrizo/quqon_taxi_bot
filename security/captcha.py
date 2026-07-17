"""
Captcha System (6-band)
=========================
Oddiy "bosing" captcha emas — random turdagi captcha generatsiya qilinadi:
    - button captcha    — bir nechta tugmadan to'g'risini bosish
    - emoji captcha      — ko'rsatilgan emojini tanlash
    - math captcha        — oddiy arifmetik masala
    - sequence captcha    — ketma-ketlikni to'g'ri tartibda bosish

Timeout: 60s (settings.CAPTCHA_TIMEOUT_SECONDS). Fail -> Kick.
Holat `captcha_sessions` jadvalida saqlanadi; join paytida guruh
cheklanadi (restrict) va captcha o'tgach cheklov olib tashlanadi.
"""
from __future__ import annotations

import json
import random
import string
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from config import settings
from database.db import get_session
from database.models import CaptchaSession, CaptchaStatus, CaptchaType
from utils.logger import logger

_EMOJIS = ["🍎", "🚗", "⚽", "🎈", "🐱", "🌙", "🔥", "🎸", "☕", "🌵"]
_SEQUENCE_ITEMS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]


@dataclass
class CaptchaChallenge:
    session_id: int
    captcha_type: CaptchaType
    question: str
    keyboard: InlineKeyboardMarkup
    correct_answer: str
    expires_at: datetime


def _cb(session_id: int, value: str) -> str:
    return f"sec_captcha:{session_id}:{value}"


class CaptchaManager:
    """Random captcha generatsiya qiladi va javoblarni tekshiradi."""

    def _generate_button(self) -> tuple[str, list[str], str]:
        correct = "✅ Men botman emasman"
        decoys = ["❌ Spam", "🤖 Bot", "🚫 Reklama"]
        options = decoys[:2] + [correct]
        random.shuffle(options)
        return "Iltimos, quyidagi tugmani bosing:", options, correct

    def _generate_emoji(self) -> tuple[str, list[str], str]:
        target = random.choice(_EMOJIS)
        decoys = random.sample([e for e in _EMOJIS if e != target], 3)
        options = decoys + [target]
        random.shuffle(options)
        return f"Ushbu emojini tanlang: {target}", options, target

    def _generate_math(self) -> tuple[str, list[str], str]:
        a, b = random.randint(1, 9), random.randint(1, 9)
        op = random.choice(["+", "-"])
        answer = a + b if op == "+" else a - b
        wrong = {answer + 1, answer - 1, answer + 2}
        wrong.discard(answer)
        options = [str(answer)] + [str(w) for w in list(wrong)[:3]]
        random.shuffle(options)
        return f"Masalani yeching: {a} {op} {b} = ?", options, str(answer)

    def _generate_sequence(self) -> tuple[str, list[str], str]:
        # Foydalanuvchi 1,2,3,4 tartibida bosishi kerak — bu yerda soddalashtirilgan
        # versiyasi: to'g'ri KEYINGI raqamni tanlash.
        current = random.randint(1, 3)
        answer = str(current + 1)
        options = [str(x) for x in range(1, 5)]
        random.shuffle(options)
        return f"Ketma-ketlikni davom ettiring: {current} → ?", options, answer

    async def create_challenge(
        self, chat_id: int, user_id: int, captcha_type: CaptchaType | None = None,
    ) -> CaptchaChallenge:
        captcha_type = captcha_type or random.choice(list(CaptchaType))

        generators = {
            CaptchaType.BUTTON: self._generate_button,
            CaptchaType.EMOJI: self._generate_emoji,
            CaptchaType.MATH: self._generate_math,
            CaptchaType.SEQUENCE: self._generate_sequence,
        }
        question, options, correct = generators[captcha_type]()

        expires_at = datetime.utcnow() + timedelta(seconds=settings.CAPTCHA_TIMEOUT_SECONDS)

        async with get_session() as session:
            session_row = CaptchaSession(
                chat_id=chat_id,
                user_id=user_id,
                captcha_type=captcha_type,
                correct_answer=correct,
                options=json.dumps(options, ensure_ascii=False),
                status=CaptchaStatus.PENDING,
                expires_at=expires_at,
            )
            session.add(session_row)
            await session.flush()
            session_id = session_row.id

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text=opt, callback_data=_cb(session_id, opt))]
                for opt in options
            ]
        )

        return CaptchaChallenge(
            session_id=session_id, captcha_type=captcha_type, question=question,
            keyboard=keyboard, correct_answer=correct, expires_at=expires_at,
        )

    async def get_session(self, session_id: int) -> CaptchaSession | None:
        async with get_session() as session:
            result = await session.execute(select(CaptchaSession).where(CaptchaSession.id == session_id))
            return result.scalar_one_or_none()

    async def submit_answer(self, session_id: int, user_id: int, answer: str) -> CaptchaStatus:
        """Javobni tekshiradi va sessiya holatini yangilaydi. PASSED/FAILED/EXPIRED qaytaradi."""
        async with get_session() as session:
            result = await session.execute(select(CaptchaSession).where(CaptchaSession.id == session_id))
            row = result.scalar_one_or_none()
            if row is None:
                return CaptchaStatus.EXPIRED
            if row.user_id != user_id:
                # Boshqa user tugmani bossa — hech narsa o'zgarmaydi
                return row.status
            if row.status != CaptchaStatus.PENDING:
                return row.status
            if datetime.utcnow() > row.expires_at:
                row.status = CaptchaStatus.EXPIRED
                return CaptchaStatus.EXPIRED

            row.attempts += 1
            if answer == row.correct_answer:
                row.status = CaptchaStatus.PASSED
            elif row.attempts >= settings.CAPTCHA_MAX_ATTEMPTS:
                row.status = CaptchaStatus.FAILED
            else:
                # Hali urinish qoldi — PENDING'da qoladi
                return CaptchaStatus.PENDING

            return row.status

    async def expire_stale_sessions(self) -> list[CaptchaSession]:
        """Muddati o'tgan PENDING sessiyalarni FAILED qiladi (fail -> kick uchun chaqiruvchiga qaytaradi)."""
        expired: list[CaptchaSession] = []
        async with get_session() as session:
            result = await session.execute(
                select(CaptchaSession).where(
                    CaptchaSession.status == CaptchaStatus.PENDING,
                    CaptchaSession.expires_at < datetime.utcnow(),
                )
            )
            for row in result.scalars().all():
                row.status = CaptchaStatus.EXPIRED
                expired.append(row)
        return expired


captcha_manager = CaptchaManager()
