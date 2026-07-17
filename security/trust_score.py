"""
Trust Score (2-band)
=====================
Har bir user 100 balldan boshlaydi. Harakatlarga qarab kamayadi/ortadi:

    - yangi account            -TRUST_PENALTY_NEW_ACCOUNT
    - username yo'q            -TRUST_PENALTY_NO_USERNAME
    - profile photo yo'q       -TRUST_PENALTY_NO_PHOTO
    - captcha o'tmagan         -TRUST_PENALTY_CAPTCHA_FAIL
    - spam qilgan               -TRUST_PENALTY_SPAM
    - admin tomonidan approved  → 100 ga to'ldiriladi

Har o'zgarish `trust_scores` jadvaliga (TrustScoreLog) yoziladi — audit
uchun. Joriy qiymat `User.trust_score` ustunida saqlanadi.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from config import settings
from database.db import get_session
from database.models import SecurityEventType, TrustScoreLog, User
from security.audit import audit
from utils.logger import logger

MIN_SCORE = 0.0
MAX_SCORE = 100.0


@dataclass
class TrustScoreResult:
    user_id: int
    old_score: float
    new_score: float
    delta: float
    reason: str


class TrustScoreManager:
    """Har user uchun Trust Score'ni hisoblaydi, yangilaydi va tarixini yozadi."""

    async def _get_or_create_user(self, session, telegram_id: int) -> User:  # type: ignore[no-untyped-def]
        result = await session.execute(select(User).where(User.telegram_id == telegram_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                telegram_id=telegram_id,
                trust_score=settings.SECURITY_TRUST_SCORE_INITIAL,
            )
            session.add(user)
            await session.flush()
        return user

    async def adjust(
        self,
        telegram_id: int,
        delta: float,
        reason: str,
        chat_id: int | None = None,
    ) -> TrustScoreResult:
        """Trust Score'ga `delta` qo'shadi (manfiy bo'lishi mumkin), [0,100] ga qisqartiradi."""
        async with get_session() as session:
            user = await self._get_or_create_user(session, telegram_id)
            old_score = user.trust_score
            new_score = max(MIN_SCORE, min(MAX_SCORE, old_score + delta))
            user.trust_score = new_score
            if delta < 0:
                user.warnings += 1

            session.add(
                TrustScoreLog(
                    user_id=telegram_id,
                    chat_id=chat_id,
                    delta=delta,
                    reason=reason,
                    old_score=old_score,
                    new_score=new_score,
                )
            )

        logger.info(f"[trust_score] user={telegram_id} {old_score:.1f} -> {new_score:.1f} ({reason})")

        if chat_id is not None:
            await audit.log_event(
                chat_id=chat_id, user_id=telegram_id,
                event_type=SecurityEventType.TRUST_CHANGE,
                details={"delta": delta, "reason": reason, "old": old_score, "new": new_score},
            )

        return TrustScoreResult(
            user_id=telegram_id, old_score=old_score, new_score=new_score,
            delta=delta, reason=reason,
        )

    # ── Spetsifikatsiyadagi standart harakatlar uchun qulaylik metodlari ────

    async def penalize_new_account(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, -settings.TRUST_PENALTY_NEW_ACCOUNT, "yangi_account", chat_id)

    async def penalize_no_username(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, -settings.TRUST_PENALTY_NO_USERNAME, "username_yoq", chat_id)

    async def penalize_no_photo(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, -settings.TRUST_PENALTY_NO_PHOTO, "profile_photo_yoq", chat_id)

    async def penalize_captcha_fail(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, -settings.TRUST_PENALTY_CAPTCHA_FAIL, "captcha_otmagan", chat_id)

    async def penalize_spam(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, -settings.TRUST_PENALTY_SPAM, "spam_qilgan", chat_id)

    async def reward_captcha_pass(self, telegram_id: int, chat_id: int | None = None) -> TrustScoreResult:
        return await self.adjust(telegram_id, settings.TRUST_BONUS_CAPTCHA_PASS, "captcha_otdi", chat_id)

    async def approve_by_admin(self, telegram_id: int, admin_id: int, chat_id: int | None = None) -> TrustScoreResult:
        async with get_session() as session:
            user = await self._get_or_create_user(session, telegram_id)
            user.is_approved = True
        return await self.adjust(
            telegram_id, settings.TRUST_BONUS_ADMIN_APPROVED, f"admin_approved:{admin_id}", chat_id
        )

    async def get_score(self, telegram_id: int) -> float:
        async with get_session() as session:
            user = await self._get_or_create_user(session, telegram_id)
            return user.trust_score


trust_score_manager = TrustScoreManager()
