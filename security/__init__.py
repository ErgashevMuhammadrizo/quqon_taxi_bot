"""
GuardBot Security Engine (v3)
==============================
Professional Telegram Security System — mustaqil modul.

Tarkib:
    engine.py             — SecurityEngine: barcha modullarni bog'lovchi facade
    trust_score.py         — TrustScoreManager: har user uchun 0-100 Trust Score
    risk_analyzer.py       — RiskAnalyzer: har action (join/message/...) uchun risk
    raid_detector.py       — RaidDetector: Anti-Raid Engine (5s/10+ join)
    captcha.py              — CaptchaManager: button/emoji/math/sequence captcha
    suspicious_monitor.py   — SuspiciousUserMonitor: xulq-atvor anomaliyalari
    audit.py                — AuditRecorder: security_logs + har-user audit tarixi
    watermark.py            — WatermarkService: kelajakdagi "secret content" himoyasi (API)
    dashboard.py            — SecurityDashboard: /statistics uchun agregatsiya
    config_schema.py        — GroupSecurityConfig: har guruh uchun sozlamalar dataclass

Dizayn tamoyillari (15-band, "Code Quality"):
    - Har modul mustaqil sinf, faqat kerakli interfeyslarga bog'liq (SOLID).
    - Barcha funksiyalar async va typed.
    - `RiskAnalyzer` va boshqa "signal" beruvchi sinflar oddiy funksiyalarni
      qaytaradi — kelajakda AI Risk Engine / LLM Detection / Behavior
      Detection osongina RiskAnalyzer ichiga "plugin" sifatida qo'shilishi
      mumkin (`register_signal_provider`, quyida `risk_analyzer.py`ga qarang).
"""
from __future__ import annotations

from security.engine import SecurityEngine

__all__ = ["SecurityEngine"]
