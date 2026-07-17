"""
Risk Analyzer (3-band) + AI Ready (11-band)
=============================================
Har bir action (join, message, media, forward, link, mention, reaction,
edit, delete) tekshiriladi va 0-100 oralig'ida "risk score" hisoblanadi.

Chegaralar (spetsifikatsiya bo'yicha):
    risk >= 70   -> ADMIN_ALERT
    risk >= 90   -> TEMPORARY_RESTRICT
    risk >= 100  -> AUTO_BAN

AI Ready arxitektura
---------------------
`RiskAnalyzer` o'zi faqat "rule-based" signal provider'lar bilan ishlaydi
(`_default_signal_providers`). Kelajakda AI Risk Engine / LLM Detection /
Behavior Detection qo'shish uchun shunchaki yangi `SignalProvider`
yozib, `register_signal_provider()` orqali ro'yxatga qo'shish kifoya —
`RiskAnalyzer.analyze()` logikasi o'zgarmaydi:

    class LLMDetectionProvider(SignalProvider):
        name = "llm_detection"
        async def score(self, ctx: RiskContext) -> float:
            ...  # tashqi LLM chaqiruvi, 0..1 qaytaradi
            return 0.0

    risk_analyzer.register_signal_provider(LLMDetectionProvider(), weight=0.4)

Har signal provider mustaqil, xato bersa butun tahlilni to'xtatmaydi
(try/except bilan izolatsiya qilingan) — bu production barqarorligi uchun
muhim (real signal xato bersa ham eski rule-based signal ishlab turadi).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Protocol

from sqlalchemy import select

from config import settings
from database.db import get_session
from database.models import RiskHistory, SecurityActionType, SecurityDecision, User
from security.audit import audit
from utils.logger import logger


@dataclass
class RiskContext:
    """Bitta action haqidagi barcha ma'lumot — signal provider'lar shu asosda baholaydi."""
    user_id: int
    chat_id: int
    action_type: SecurityActionType
    trust_score: float = 100.0
    account_age_days: float | None = None
    has_username: bool = True
    has_photo: bool = True
    captcha_passed: bool = True
    is_link: bool = False
    is_forward: bool = False
    is_mention: bool = False
    suspicious_score: float = 0.0
    extra: dict = field(default_factory=dict)


class SignalProvider(ABC):
    """Bitta risk signali — 0..1 oralig'ida shubha darajasini qaytaradi."""

    name: str = "base"

    @abstractmethod
    async def score(self, ctx: RiskContext) -> float:  # 0..1
        ...


class NewAccountSignal(SignalProvider):
    name = "new_account"

    async def score(self, ctx: RiskContext) -> float:
        if ctx.account_age_days is None:
            return 0.0
        if ctx.account_age_days <= 0:
            return 1.0
        if ctx.account_age_days >= settings.NEW_ACCOUNT_DAYS_SUSPICIOUS:
            return 0.0
        return round(1.0 - (ctx.account_age_days / settings.NEW_ACCOUNT_DAYS_SUSPICIOUS), 2)


class ProfileCompletenessSignal(SignalProvider):
    name = "profile_completeness"

    async def score(self, ctx: RiskContext) -> float:
        missing = (0 if ctx.has_username else 0.5) + (0 if ctx.has_photo else 0.5)
        return round(missing, 2)


class TrustScoreSignal(SignalProvider):
    name = "trust_score"

    async def score(self, ctx: RiskContext) -> float:
        # Trust past bo'lgan sari risk yuqori: 100 trust -> 0 risk, 0 trust -> 1.0 risk
        return round(max(0.0, min(1.0, (100.0 - ctx.trust_score) / 100.0)), 2)


class CaptchaSignal(SignalProvider):
    name = "captcha"

    async def score(self, ctx: RiskContext) -> float:
        return 0.0 if ctx.captcha_passed else 1.0


class ContentTypeSignal(SignalProvider):
    """link/forward/mention harakatlari uchun asosiy shubha manbai."""
    name = "content_type"

    async def score(self, ctx: RiskContext) -> float:
        if ctx.action_type == SecurityActionType.LINK and ctx.is_link:
            return 0.8
        if ctx.action_type == SecurityActionType.FORWARD and ctx.is_forward:
            return 0.5
        if ctx.action_type == SecurityActionType.MENTION and ctx.is_mention:
            return 0.4
        return 0.0


class SuspiciousBehaviorSignal(SignalProvider):
    name = "suspicious_behavior"

    async def score(self, ctx: RiskContext) -> float:
        return round(max(0.0, min(1.0, ctx.suspicious_score)), 2)


# Action turi bo'yicha signal og'irliklari — 0..1 oralig'ida yig'indisi ~1.0 bo'lishi shart emas
# (yakunda normalizatsiya qilinadi), lekin nisbatlar mantiqiyligini saqlaydi.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "new_account": 0.20,
    "profile_completeness": 0.10,
    "trust_score": 0.30,
    "captcha": 0.20,
    "content_type": 0.15,
    "suspicious_behavior": 0.05,
}


class RiskAnalyzer:
    def __init__(self) -> None:
        self._providers: list[tuple[SignalProvider, float]] = []
        for provider in (
            NewAccountSignal(), ProfileCompletenessSignal(), TrustScoreSignal(),
            CaptchaSignal(), ContentTypeSignal(), SuspiciousBehaviorSignal(),
        ):
            self._providers.append((provider, _DEFAULT_WEIGHTS[provider.name]))

    def register_signal_provider(self, provider: SignalProvider, weight: float) -> None:
        """AI Risk Engine / LLM Detection / Behavior Detection shu orqali qo'shiladi."""
        self._providers.append((provider, weight))
        logger.info(f"[risk_analyzer] yangi signal provider ro'yxatga olindi: {provider.name} (weight={weight})")

    async def compute_risk_score(self, ctx: RiskContext) -> tuple[float, dict[str, float]]:
        total_weight = sum(w for _, w in self._providers) or 1.0
        weighted_sum = 0.0
        breakdown: dict[str, float] = {}

        for provider, weight in self._providers:
            try:
                s = await provider.score(ctx)
            except Exception as exc:  # signal xatosi butun tahlilni to'xtatmasin
                logger.warning(f"[risk_analyzer] signal '{provider.name}' xato berdi: {exc}")
                s = 0.0
            s = max(0.0, min(1.0, s))
            breakdown[provider.name] = s
            weighted_sum += s * weight

        risk_score = round((weighted_sum / total_weight) * 100, 2)
        return risk_score, breakdown

    def decide(self, risk_score: float) -> SecurityDecision:
        if risk_score >= settings.SECURITY_RISK_AUTO_BAN:
            return SecurityDecision.AUTO_BAN
        if risk_score >= settings.SECURITY_RISK_TEMP_RESTRICT:
            return SecurityDecision.TEMPORARY_RESTRICT
        if risk_score >= settings.SECURITY_RISK_ADMIN_ALERT:
            return SecurityDecision.ADMIN_ALERT
        return SecurityDecision.ALLOW

    async def analyze(self, ctx: RiskContext) -> tuple[float, SecurityDecision, dict[str, float]]:
        """To'liq tahlil: risk score + qaror + har signalning ulushi. risk_history'ga yozadi."""
        risk_score, breakdown = await self.compute_risk_score(ctx)
        decision = self.decide(risk_score)

        try:
            async with get_session() as session:
                session.add(
                    RiskHistory(
                        user_id=ctx.user_id,
                        chat_id=ctx.chat_id,
                        action_type=ctx.action_type,
                        risk_score=risk_score,
                        decision=decision,
                        factors=json.dumps(breakdown, ensure_ascii=False),
                    )
                )
        except Exception as exc:  # pragma: no cover
            logger.error(f"[risk_analyzer] risk_history yozib bo'lmadi: {exc}")

        return risk_score, decision, breakdown

    async def build_context(
        self,
        user_id: int,
        chat_id: int,
        action_type: SecurityActionType,
        *,
        account_age_days: float | None = None,
        has_username: bool = True,
        has_photo: bool = True,
        captcha_passed: bool = True,
        is_link: bool = False,
        is_forward: bool = False,
        is_mention: bool = False,
        suspicious_score: float = 0.0,
    ) -> RiskContext:
        """DB'dan Trust Score'ni olib, to'liq RiskContext yasaydi (handler'lar shuni chaqiradi)."""
        trust = 100.0
        try:
            async with get_session() as session:
                result = await session.execute(select(User).where(User.telegram_id == user_id))
                user = result.scalar_one_or_none()
                if user is not None:
                    trust = user.trust_score
                    has_username = has_username and bool(user.has_username or user.username)
                    has_photo = has_photo and user.has_profile_photo
                    captcha_passed = captcha_passed and (user.captcha_passed or not user.captcha_passed)
        except Exception as exc:  # pragma: no cover
            logger.warning(f"[risk_analyzer] user o'qib bo'lmadi: {exc}")

        return RiskContext(
            user_id=user_id, chat_id=chat_id, action_type=action_type,
            trust_score=trust, account_age_days=account_age_days,
            has_username=has_username, has_photo=has_photo,
            captcha_passed=captcha_passed, is_link=is_link,
            is_forward=is_forward, is_mention=is_mention,
            suspicious_score=suspicious_score,
        )


risk_analyzer = RiskAnalyzer()
