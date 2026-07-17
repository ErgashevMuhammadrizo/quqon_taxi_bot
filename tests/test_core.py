"""
Core modullari uchun unit testlar.
Ishga tushirish: pytest tests/ -v
"""
from __future__ import annotations

import pytest

from core.content_analyzer import (
    ContentAnalyzer,
    compute_bytes_hash,
    compute_text_hash,
    embed_watermark,
    extract_watermark,
    generate_watermark_token,
    normalize_text,
    text_similarity,
)
from core.decision_matrix import Action, DecisionMatrix, RiskFactors


# ─── ContentAnalyzer ──────────────────────────────────────────────────────────

class TestNormalizeText:
    def test_strips_and_lowercases(self):
        assert normalize_text("  Salom, Dunyo!!!  ") == "salom dunyo"

    def test_collapses_whitespace(self):
        assert normalize_text("bir   ikki\tuch") == "bir ikki uch"

    def test_empty_string(self):
        assert normalize_text("") == ""

    def test_unicode_preserved(self):
        result = normalize_text("O'zbek tili")
        assert "uzbek" in result or "o'zbek" in result or "zbek" in result


class TestComputeHash:
    def test_text_hash_deterministic(self):
        """Normalizatsiyadan keyin bir xil matnlar bir xil hash berishi kerak."""
        a = compute_text_hash("Bu test matni")
        b = compute_text_hash("bu test matni!!!")
        assert a == b

    def test_different_texts_different_hashes(self):
        assert compute_text_hash("salom") != compute_text_hash("xayr")

    def test_bytes_hash_deterministic(self):
        data = b"binary content"
        assert compute_bytes_hash(data) == compute_bytes_hash(data)

    def test_bytes_hash_length(self):
        """SHA-256 hex digest 64 belgi bo'lishi kerak."""
        assert len(compute_bytes_hash(b"test")) == 64


class TestTextSimilarity:
    def test_identical_texts(self):
        assert text_similarity("hello world", "hello world") == 1.0

    def test_empty_strings(self):
        assert text_similarity("", "") == 0.0

    def test_completely_different(self):
        score = text_similarity("hello world", "completely unrelated sentence here")
        assert score < 0.5

    def test_partial_match(self):
        score = text_similarity("bu kanal maxfiy", "bu kanal ommaviy")
        assert 0.3 < score < 1.0


class TestWatermark:
    def test_roundtrip(self):
        token = generate_watermark_token()
        embedded = embed_watermark("Ochiq matn", token)
        assert extract_watermark(embedded) == token

    def test_original_text_preserved(self):
        token = generate_watermark_token()
        original = "Maxfiy post matni"
        embedded = embed_watermark(original, token)
        assert original in embedded

    def test_no_watermark_returns_none(self):
        assert extract_watermark("Oddiy matn, watermark yo'q.") is None

    def test_token_uniqueness(self):
        tokens = {generate_watermark_token() for _ in range(100)}
        assert len(tokens) == 100  # har bir token noyob

    def test_different_tokens_different_embedded(self):
        t1, t2 = generate_watermark_token(), generate_watermark_token()
        e1 = embed_watermark("Matn", t1)
        e2 = embed_watermark("Matn", t2)
        assert e1 != e2


class TestContentAnalyzer:
    def setup_method(self):
        self.analyzer = ContentAnalyzer(hash_threshold=0.9, ocr_threshold=0.85)

    def test_exact_match(self):
        known = [(1, "Original maxfiy post matni bu yerda")]
        result = self.analyzer.analyze_text("Original maxfiy post matni bu yerda", known)
        assert result.is_match is True
        assert result.match_type == "hash"
        assert result.matched_post_id == 1
        assert result.similarity == 1.0

    def test_no_match(self):
        known = [(1, "Original maxfiy post matni bu yerda")]
        result = self.analyzer.analyze_text("Butunlay boshqa mavzudagi matn", known)
        assert result.is_match is False

    def test_empty_known_posts(self):
        result = self.analyzer.analyze_text("Qandaydir matn", [])
        assert result.is_match is False

    def test_multiple_known_best_match(self):
        known = [
            (1, "Birinchi post haqida"),
            (2, "Ikkinchi post haqida ma'lumot"),
            (3, "Bu post eng o'xshash bo'ladi test uchun"),
        ]
        result = self.analyzer.analyze_text(
            "Bu post eng o'xshash bo'ladi test uchun", known
        )
        assert result.matched_post_id == 3


# ─── DecisionMatrix ───────────────────────────────────────────────────────────

class TestDecisionMatrix:
    def setup_method(self):
        self.dm = DecisionMatrix()

    def test_score_in_range(self):
        for _ in range(10):
            factors = RiskFactors(
                hash_match_score=0.5,
                ocr_similarity_score=0.3,
                watermark_verified=0.0,
                behavior_score=0.2,
                account_age_score=0.1,
            )
            decision = self.dm.decide(factors)
            assert 0 <= decision.risk_score <= 100

    def test_zero_factors_ignore(self):
        decision = self.dm.decide(RiskFactors())
        assert decision.action == Action.IGNORE
        assert decision.risk_score == 0.0

    def test_all_max_autoban(self):
        decision = self.dm.decide(RiskFactors(1.0, 1.0, 1.0, 1.0, 1.0))
        assert decision.action == Action.AUTO_BAN
        assert decision.risk_score == 100.0

    @pytest.mark.parametrize(
        "factors,expected_action",
        [
            # Faqat hash + watermark (~50%) → admin tasdig'i
            (RiskFactors(hash_match_score=1.0, watermark_verified=1.0), Action.ADMIN_CONFIRM),
            # Past signal → e'tiborsiz
            (RiskFactors(0.05, 0.05, 0.0, 0.05, 0.05), Action.IGNORE),
            # Barcha maksimal → auto ban
            (RiskFactors(1.0, 1.0, 1.0, 1.0, 1.0), Action.AUTO_BAN),
        ],
    )
    def test_parametrized_actions(self, factors, expected_action):
        decision = self.dm.decide(factors)
        assert decision.action == expected_action

    def test_risk_score_proportional(self):
        """Yuqoriroq faktorlar → yuqoriroq risk score."""
        low  = self.dm.decide(RiskFactors(0.1, 0.1, 0.0, 0.1, 0.1))
        high = self.dm.decide(RiskFactors(0.9, 0.9, 1.0, 0.9, 0.9))
        assert low.risk_score < high.risk_score

    def test_decision_has_factors(self):
        """Decision obyekti original faktorlarni saqlashi kerak."""
        factors = RiskFactors(hash_match_score=0.5)
        decision = self.dm.decide(factors)
        assert decision.factors is factors


# ─── ProtectedGroup model checks ─────────────────────────────────────────────

class TestProtectedGroupModel:
    """MVP v2 da qo'shilgan ProtectedGroup modelini tekshiradi."""

    def test_required_columns_exist(self):
        from database.models import ProtectedGroup
        cols = {c.key for c in ProtectedGroup.__table__.columns}
        required = {"id", "chat_id", "title", "is_active", "added_by", "bot_is_admin"}
        assert required.issubset(cols), f"Missing columns: {required - cols}"

    def test_chat_id_is_unique_indexed(self):
        from database.models import ProtectedGroup
        col = ProtectedGroup.__table__.columns["chat_id"]
        assert col.index or any(
            idx.unique for idx in ProtectedGroup.__table__.indexes
            if "chat_id" in [c.key for c in idx.columns]
        )

    def test_channel_alert_chat_id_nullable(self):
        from database.models import Channel
        col = Channel.__table__.columns["alert_chat_id"]
        assert col.nullable is True

    def test_channel_added_by_nullable(self):
        from database.models import Channel
        col = Channel.__table__.columns["added_by"]
        assert col.nullable is True

    def test_admin_has_username_field(self):
        from database.models import Admin
        cols = {c.key for c in Admin.__table__.columns}
        assert "username" in cols
        assert "full_name" in cols
        assert "added_by" in cols

    def test_new_action_types_exist(self):
        from database.models import ActionType
        assert ActionType.GROUP_ADDED.value == "GROUP_ADDED"
        assert ActionType.CHANNEL_ADDED.value == "CHANNEL_ADDED"
        assert ActionType.ADMIN_ADDED.value == "ADMIN_ADDED"


# ─── Retry utility ────────────────────────────────────────────────────────────

class TestRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        from utils.retry import retry

        call_count = 0

        @retry(max_attempts=3)
        async def always_succeeds():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await always_succeeds()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        from utils.retry import retry

        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, exceptions=(ConnectionError,))
        async def fails_twice():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("bağlantı hatası")
            return "ok"

        result = await fails_twice()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_attempts(self):
        from utils.retry import retry

        @retry(max_attempts=2, base_delay=0.01, exceptions=(ConnectionError,))
        async def always_fails():
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError):
            await always_fails()

    @pytest.mark.asyncio
    async def test_non_retryable_raises_immediately(self):
        from utils.retry import retry

        call_count = 0

        @retry(max_attempts=3, base_delay=0.01, exceptions=(ConnectionError,))
        async def raises_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not retryable")

        with pytest.raises(ValueError):
            await raises_value_error()
        assert call_count == 1  # faqat bir marta urinildi


# ─── CloneDetector (unit, DB'siz) ────────────────────────────────────────────

class TestCloneDetectorResult:
    def test_clone_result_is_dataclass(self):
        from core.clone_detector import CloneResult
        r = CloneResult(
            is_clone=True,
            similarity_score=0.95,
            match_type="phash",
            matched_post_id=42,
        )
        assert r.is_clone is True
        assert r.similarity_score == 0.95
        assert r.evidence == {}

    def test_clone_result_evidence_default(self):
        from core.clone_detector import CloneResult
        r1 = CloneResult(False, 0.0, "none", None)
        r2 = CloneResult(False, 0.0, "none", None)
        # Har bir instance o'z evidence dict'iga ega (mutable default muammosi yo'q)
        r1.evidence["key"] = "val"
        assert "key" not in r2.evidence


# ─── BehaviorEngine (unit, Redis'siz) ────────────────────────────────────────

class TestBehaviorEngineScoring:
    def setup_method(self):
        from unittest.mock import MagicMock
        from core.behavior_engine import BehaviorEngine
        self.engine = BehaviorEngine(MagicMock())

    def test_new_account_high_score(self):
        score = self.engine.score_account_age(0)
        assert score == 1.0

    def test_old_account_low_score(self):
        score = self.engine.score_account_age(365)
        assert score <= 0.1

    def test_forward_rate_zero(self):
        assert self.engine.score_forward_rate(0) == 0.0

    def test_forward_rate_max(self):
        assert self.engine.score_forward_rate(1000) == 1.0

    def test_forward_rate_at_limit(self):
        """Limit atrofida 0..1 oralig'ida bo'lishi kerak."""
        from config import settings
        score = self.engine.score_forward_rate(settings.RATE_LIMIT_FORWARDS)
        assert 0.0 < score <= 1.0
