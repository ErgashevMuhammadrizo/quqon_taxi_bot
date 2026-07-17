"""
SecurityEngine — barcha Security modullarni bog'lovchi yagona facade.

Handler'lar (`handlers/security_events.py`) to'g'ridan-to'g'ri
`trust_score_manager`, `risk_analyzer` va h.k.ni chaqirishi ham mumkin,
lekin odatiy oqim (join / action) uchun shu klass qulay yagona kirish
nuqtasini beradi — bu Clean Architecture / SOLID (Facade pattern)ga mos.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from redis.asyncio import Redis
from sqlalchemy import select

from database.db import get_session
from database.models import (
    ProtectedGroup, SecurityActionType, SecurityDecision, User,
)
from security.audit import audit
from security.captcha import captcha_manager
from security.config_schema import GroupSecurityConfig
from security.raid_detector import RaidCheckResult, RaidDetector
from security.risk_analyzer import RiskContext, risk_analyzer
from security.suspicious_monitor import SuspiciousUserMonitor
from security.trust_score import trust_score_manager
from utils.logger import logger


@dataclass
class JoinEvaluation:
    raid: RaidCheckResult
    risk_score: float
    decision: SecurityDecision
    require_captcha: bool
    config: GroupSecurityConfig


@dataclass
class ActionEvaluation:
    risk_score: float
    decision: SecurityDecision
    breakdown: dict[str, float]


class SecurityEngine:
    def __init__(self, redis: Redis):
        self.redis = redis
        self.raid_detector = RaidDetector(redis)
        self.suspicious_monitor = SuspiciousUserMonitor(redis)

    async def get_group_config(self, chat_id: int) -> GroupSecurityConfig:
        async with get_session() as session:
            result = await session.execute(select(ProtectedGroup).where(ProtectedGroup.chat_id == chat_id))
            group = result.scalar_one_or_none()
        return GroupSecurityConfig.from_protected_group(group)

    async def _touch_user(
        self, telegram_id: int, username: str | None, full_name: str | None,
        has_username: bool, has_photo: bool, is_join: bool = False,
    ) -> None:
        async with get_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == telegram_id))
            user = result.scalar_one_or_none()
            now = datetime.utcnow()
            if user is None:
                user = User(
                    telegram_id=telegram_id, username=username, full_name=full_name,
                    has_username=has_username, has_profile_photo=has_photo,
                    join_time=now if is_join else None, last_active_at=now,
                    groups_joined_count=1 if is_join else 0,
                )
                session.add(user)
            else:
                user.username = username or user.username
                user.full_name = full_name or user.full_name
                user.has_username = has_username
                user.has_profile_photo = has_photo
                user.last_active_at = now
                if is_join:
                    user.join_time = user.join_time or now
                    user.groups_joined_count += 1

    async def evaluate_join(
        self,
        chat_id: int,
        user_id: int,
        *,
        username: str | None = None,
        full_name: str | None = None,
        has_username: bool = True,
        has_photo: bool = True,
        account_age_days: float | None = None,
    ) -> JoinEvaluation:
        """Yangi a'zo qo'shilganda chaqiriladi: raid check + risk + captcha kerakmi."""
        config = await self.get_group_config(chat_id)

        await self._touch_user(user_id, username, full_name, has_username, has_photo, is_join=True)
        await audit.join(chat_id, user_id, username=username)

        raid_result = RaidCheckResult(is_raid=False, join_count=0, raid_mode_active=False)
        if config.raid_protection_enabled:
            raid_result = await self.raid_detector.check_join(chat_id, user_id)
            if raid_result.is_raid:
                await audit.raid(chat_id, raid_result.join_count)

        if not has_username:
            await trust_score_manager.penalize_no_username(user_id, chat_id)
        if not has_photo:
            await trust_score_manager.penalize_no_photo(user_id, chat_id)
        if account_age_days is not None and account_age_days <= 0:
            await trust_score_manager.penalize_new_account(user_id, chat_id)

        ctx = await risk_analyzer.build_context(
            user_id, chat_id, SecurityActionType.JOIN,
            account_age_days=account_age_days, has_username=has_username,
            has_photo=has_photo, captcha_passed=not config.captcha_enabled,
        )
        risk_score, decision, _ = await risk_analyzer.analyze(ctx)

        require_captcha = config.captcha_enabled or raid_result.raid_mode_active

        return JoinEvaluation(
            raid=raid_result, risk_score=risk_score, decision=decision,
            require_captcha=require_captcha, config=config,
        )

    async def evaluate_action(
        self,
        chat_id: int,
        user_id: int,
        action_type: SecurityActionType,
        *,
        is_link: bool = False,
        is_forward: bool = False,
        is_mention: bool = False,
        message_text: str | None = None,
    ) -> ActionEvaluation:
        """message/media/forward/link/mention/reaction/edit/delete uchun umumiy tekshiruv."""
        flags = await self.suspicious_monitor.evaluate(user_id, message_text)

        if flags.flood or flags.duplicate_messages:
            await trust_score_manager.penalize_spam(user_id, chat_id)
            await audit.spam(chat_id, user_id, flood=flags.flood, duplicate=flags.duplicate_messages)

        async with get_session() as session:
            result = await session.execute(select(User).where(User.telegram_id == user_id))
            user = result.scalar_one_or_none()
            if user is not None:
                user.last_active_at = datetime.utcnow()
                if action_type == SecurityActionType.MESSAGE:
                    user.message_count += 1
                if action_type == SecurityActionType.FORWARD:
                    user.forward_count += 1

        ctx = await risk_analyzer.build_context(
            user_id, chat_id, action_type,
            is_link=is_link, is_forward=is_forward, is_mention=is_mention,
            suspicious_score=flags.score,
        )
        risk_score, decision, breakdown = await risk_analyzer.analyze(ctx)

        if action_type == SecurityActionType.LINK and is_link:
            await audit.link_block(chat_id, user_id)
        if action_type == SecurityActionType.FORWARD and is_forward:
            await audit.forward_block(chat_id, user_id)

        return ActionEvaluation(risk_score=risk_score, decision=decision, breakdown=breakdown)

    async def request_captcha(self, chat_id: int, user_id: int):  # -> CaptchaChallenge
        return await captcha_manager.create_challenge(chat_id, user_id)

    async def on_captcha_result(self, chat_id: int, user_id: int, passed: bool) -> None:
        if passed:
            await trust_score_manager.reward_captcha_pass(user_id, chat_id)
            await audit.captcha_pass(chat_id, user_id)
            async with get_session() as session:
                result = await session.execute(select(User).where(User.telegram_id == user_id))
                user = result.scalar_one_or_none()
                if user is not None:
                    user.captcha_passed = True
        else:
            await trust_score_manager.penalize_captcha_fail(user_id, chat_id)
            await audit.captcha_fail(chat_id, user_id)

    async def disable_raid_mode(self, chat_id: int, admin_id: int) -> None:
        await self.raid_detector.deactivate_raid_mode(chat_id, ended_by=admin_id)
