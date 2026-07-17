"""
GuardBot — Konfiguratsiya.
Barcha sozlamalar .env fayl orqali boshqariladi.
"""
from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── Telegram ──────────────────────────────────────────────────────────────
    BOT_TOKEN: str
    BOT_USE_WEBHOOK: bool = False
    WEBHOOK_URL: str = ""
    WEBHOOK_PATH: str = "/webhook"
    WEBHOOK_SECRET: str = "change-me"
    WEBAPP_HOST: str = "0.0.0.0"
    WEBAPP_PORT: int = 8080

    # ── Adminlar ──────────────────────────────────────────────────────────────
    # .env da: SUPER_ADMIN_IDS=111111111,222222222
    SUPER_ADMIN_IDS: str = ""

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://guardbot:guardbot@localhost:5432/guardbot"

    # ── Redis ─────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Himoya sozlamalari ────────────────────────────────────────────────────
    RATE_LIMIT_FORWARDS: int = 5
    RATE_LIMIT_WINDOW_SECONDS: int = 60
    AUTO_BAN_RISK_THRESHOLD: int = 80
    ADMIN_CONFIRM_RISK_THRESHOLD: int = 50
    HASH_SIMILARITY_THRESHOLD: float = 0.92
    OCR_SIMILARITY_THRESHOLD: float = 0.85
    NEW_ACCOUNT_DAYS_SUSPICIOUS: int = 3

    # ── Risk vaznlari ─────────────────────────────────────────────────────────
    WEIGHT_FORWARD_HASH: float = 0.30
    WEIGHT_OCR_SIMILARITY: float = 0.25
    WEIGHT_WATERMARK: float = 0.20
    WEIGHT_BEHAVIOR: float = 0.15
    WEIGHT_ACCOUNT_AGE: float = 0.10

    # ── Spam / Reklama aniqlash ───────────────────────────────────────────────
    # Telegram link/mention topilsa → ban (0.0–1.0)
    SPAM_TG_LINK_CONFIDENCE:    float = 0.95
    # Reklama kalit so'z topilsa → ban chegarasi (0.0–1.0)
    SPAM_AD_KEYWORD_CONFIDENCE: float = 0.85
    # Bir xabarda nechta URL'dan boshlab shubhali (2 = ikkita URL → flag)
    SPAM_MULTI_URL_THRESHOLD:   int   = 2
    SPAM_MULTI_URL_CONFIDENCE:  float = 0.70
    # Media + URL caption → admin confirm chegarasi
    SPAM_MEDIA_LINK_CONFIDENCE: float = 0.65
    # Spam uchun avtoban chegarasi (bu va yuqori → darhol ban)
    SPAM_AUTO_BAN_CONFIDENCE:   float = 0.80
    # Admin confirm chegarasi (bu va yuqori, avtoban dan past → confirm)
    SPAM_CONFIRM_CONFIDENCE:    float = 0.60

    # ── Monitoring ────────────────────────────────────────────────────────────
    METRICS_ENABLED: bool = True
    METRICS_PORT: int = 9090
    HEALTH_PATH: str = "/health"
    CLONE_SCAN_INTERVAL_SECONDS: int = 3600

    # ── Logging ───────────────────────────────────────────────────────────────
    LOG_LEVEL: str = "INFO"
    LOG_FILE: str = "logs/guardbot.log"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def super_admins(self) -> List[int]:
        if not self.SUPER_ADMIN_IDS.strip():
            return []
        try:
            return [int(x.strip()) for x in self.SUPER_ADMIN_IDS.split(",") if x.strip()]
        except ValueError:
            return []


settings = Settings()
