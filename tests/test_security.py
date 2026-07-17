"""
Security Engine (v3) uchun unit testlar.
Ishga tushirish: pytest tests/test_security.py -v
"""
from __future__ import annotations

import pytest

from database.models import CaptchaType, SecurityDecision
from security.config_schema import GroupSecurityConfig
from security.dashboard import SecurityDashboard, SecurityStats
from security.risk_analyzer import RiskAnalyzer
from security.suspicious_monitor import SuspiciousFlags
from security.watermark import WatermarkService


# ─── RiskAnalyzer.decide() — spetsifikatsiya chegaralari ──────────────────────

class TestRiskDecision:
    def setup_method(self):
        self.analyzer = RiskAnalyzer()

    def test_below_threshold_allows(self):
        assert self.analyzer.decide(69) == SecurityDecision.ALLOW

    def test_admin_alert_threshold(self):
        assert self.analyzer.decide(70) == SecurityDecision.ADMIN_ALERT
        assert self.analyzer.decide(89) == SecurityDecision.ADMIN_ALERT

    def test_temporary_restrict_threshold(self):
        assert self.analyzer.decide(90) == SecurityDecision.TEMPORARY_RESTRICT
        assert self.analyzer.decide(99) == SecurityDecision.TEMPORARY_RESTRICT

    def test_auto_ban_threshold(self):
        assert self.analyzer.decide(100) == SecurityDecision.AUTO_BAN
        assert self.analyzer.decide(150) == SecurityDecision.AUTO_BAN

    def test_zero_risk_allows(self):
        assert self.analyzer.decide(0) == SecurityDecision.ALLOW


# ─── RiskAnalyzer signal providers (AI Ready — plugin arxitekturasi) ─────────

class TestSignalProviderRegistration:
    def test_register_signal_provider_adds_to_pipeline(self):
        analyzer = RiskAnalyzer()
        initial_count = len(analyzer._providers)

        class DummySignal:
            name = "dummy_ai_signal"

            async def score(self, ctx):
                return 1.0

        analyzer.register_signal_provider(DummySignal(), weight=0.5)
        assert len(analyzer._providers) == initial_count + 1

    @pytest.mark.asyncio
    async def test_compute_risk_score_isolates_failing_signal(self):
        """Bitta signal xato bersa, boshqalar ishlab turishi kerak."""
        from security.risk_analyzer import RiskContext
        from database.models import SecurityActionType

        analyzer = RiskAnalyzer()

        class BrokenSignal:
            name = "broken"

            async def score(self, ctx):
                raise RuntimeError("boom")

        analyzer.register_signal_provider(BrokenSignal(), weight=0.5)
        ctx = RiskContext(user_id=1, chat_id=1, action_type=SecurityActionType.MESSAGE)
        score, breakdown = await analyzer.compute_risk_score(ctx)
        assert isinstance(score, float)
        assert 0.0 <= score <= 100.0


# ─── GroupSecurityConfig — per-guruh sozlamalar ────────────────────────────────

class TestGroupSecurityConfig:
    def test_default_config_has_safe_values(self):
        config = GroupSecurityConfig.default()
        assert config.raid_protection_enabled is True
        assert config.captcha_enabled is True

    def test_from_none_group_returns_default(self):
        config = GroupSecurityConfig.from_protected_group(None)
        assert config == GroupSecurityConfig.default()

    def test_from_protected_group_reads_fields(self):
        class FakeGroup:
            raid_protection_enabled = False
            captcha_enabled = True
            forward_block_enabled = True
            link_block_enabled = False
            media_block_enabled = True
            ai_detection_enabled = True
            risk_threshold = 80
            trust_threshold = 50

        config = GroupSecurityConfig.from_protected_group(FakeGroup())
        assert config.raid_protection_enabled is False
        assert config.risk_threshold == 80
        assert config.trust_threshold == 50


# ─── SuspiciousFlags — xulq-atvor scoring ──────────────────────────────────────

class TestSuspiciousFlagsScore:
    def test_no_flags_zero_score(self):
        assert SuspiciousFlags().score == 0.0

    def test_flood_and_duplicate_combine(self):
        flags = SuspiciousFlags(flood=True, duplicate_messages=True)
        assert flags.score == pytest.approx(0.5)

    def test_all_flags_capped_at_one(self):
        flags = SuspiciousFlags(
            many_groups=True, low_talk=True, silent_watcher=True,
            flood=True, duplicate_messages=True,
        )
        assert flags.score == 1.0


# ─── WatermarkService — API mavjudligi (9-band) ────────────────────────────────

class TestWatermarkService:
    def setup_method(self):
        self.service = WatermarkService()

    def test_embed_is_invisible_to_the_eye(self):
        wm = self.service.generate_for_recipient(chat_id=-100123, user_id=555)
        text = "Salom, bu maxfiy kontent."
        embedded = self.service.embed(text, wm)
        # Ko'rinadigan matn o'zgarmasligi kerak — faqat oxiriga qo'shiladi.
        assert embedded.startswith(text)
        assert len(embedded) > len(text)

    def test_extract_roundtrip(self):
        wm = self.service.generate_for_recipient(chat_id=-100123, user_id=555)
        embedded = self.service.embed("test kontent", wm)
        extracted = self.service.extract(embedded)
        assert extracted == wm.token

    def test_extract_returns_none_without_watermark(self):
        assert self.service.extract("oddiy matn, watermarksiz") is None

    def test_different_recipients_get_different_tokens(self):
        wm1 = self.service.generate_for_recipient(chat_id=1, user_id=1)
        wm2 = self.service.generate_for_recipient(chat_id=1, user_id=2)
        assert wm1.token != wm2.token


# ─── SecurityDashboard — formatlash (10-band) ──────────────────────────────────

class TestSecurityDashboardFormat:
    def test_format_stats_contains_all_metrics(self):
        dashboard = SecurityDashboard()
        stats = SecurityStats(
            today_joins=5, today_bans=1, today_mutes=2, risk_users=3,
            raid_attempts=0, spam_blocked=4, deleted_messages=6,
            captcha_failed=1, active_users=42,
        )
        text = dashboard.format_stats(stats)
        assert "5" in text and "42" in text
        assert "Security Dashboard" in text


# ─── CaptchaManager — random turdagi captcha generatorlari (6-band) ───────────

class TestCaptchaGenerators:
    def setup_method(self):
        from security.captcha import CaptchaManager
        self.manager = CaptchaManager()

    def test_math_captcha_answer_is_correct(self):
        question, options, correct = self.manager._generate_math()
        assert correct in options
        assert len(options) == 4

    def test_emoji_captcha_has_unique_options(self):
        question, options, correct = self.manager._generate_emoji()
        assert correct in options
        assert len(set(options)) == len(options)

    def test_button_captcha_includes_correct_option(self):
        question, options, correct = self.manager._generate_button()
        assert correct in options

    def test_sequence_captcha_answer_present(self):
        question, options, correct = self.manager._generate_sequence()
        assert correct in options
