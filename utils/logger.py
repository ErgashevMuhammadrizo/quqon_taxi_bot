"""
Markazlashtirilgan logging sozlamasi.
  - Konsolga rangli chiqish (colorlog mavjud bo'lsa)
  - Faylga aylanadigan log (RotatingFileHandler) — max 10MB × 5 fayl
  - JSON structured logging (python-json-logger mavjud bo'lsa) — prod uchun
  - Sensitive ma'lumotlar (token, password) avtomatik mask'lanadi
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
import sys

from config import settings

# ─── Sensitive ma'lumotlar filter ─────────────────────────────────────────────

class SensitiveDataFilter(logging.Filter):
    """Log yozuvlarida token, parol, secret kabi ma'lumotlarni yashiradi."""

    _PATTERNS = [
        (re.compile(r"(bot_token|BOT_TOKEN)[=: ]+\S+", re.IGNORECASE), r"\1=***"),
        (re.compile(r"(\d{8,10}:[A-Za-z0-9_-]{35})", re.IGNORECASE), r"BOT_TOKEN_HIDDEN"),
        (re.compile(r"(secret|password|secret_key)[=: ]+\S+", re.IGNORECASE), r"\1=***"),
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        for pattern, replacement in self._PATTERNS:
            msg = pattern.sub(replacement, msg)
        record.msg = msg
        record.args = ()
        return True


# ─── Setup ────────────────────────────────────────────────────────────────────

def setup_logging() -> logging.Logger:
    log_dir = os.path.dirname(settings.LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    root_logger = logging.getLogger("guardbot")
    if root_logger.handlers:
        return root_logger  # allaqachon sozlangan

    root_logger.setLevel(settings.LOG_LEVEL)
    root_logger.addFilter(SensitiveDataFilter())

    base_fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    date_fmt = "%Y-%m-%d %H:%M:%S"

    # ── Konsol handler ────────────────────────────────────────────────────────
    try:
        import colorlog  # type: ignore
        console_fmt = colorlog.ColoredFormatter(
            "%(log_color)s%(asctime)s | %(levelname)-7s%(reset)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
            log_colors={
                "DEBUG":    "cyan",
                "INFO":     "green",
                "WARNING":  "yellow",
                "ERROR":    "red",
                "CRITICAL": "bold_red",
            },
        )
    except ImportError:
        console_fmt = logging.Formatter(base_fmt, datefmt="%H:%M:%S")

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_fmt)
    console_handler.setLevel(logging.DEBUG)
    root_logger.addHandler(console_handler)

    # ── Fayl handler (rotating) ───────────────────────────────────────────────
    try:
        file_fmt: logging.Formatter

        try:
            from pythonjsonlogger import jsonlogger  # type: ignore
            file_fmt = jsonlogger.JsonFormatter(
                "%(asctime)s %(name)s %(levelname)s %(message)s",
                datefmt=date_fmt,
            )
        except ImportError:
            file_fmt = logging.Formatter(base_fmt, datefmt=date_fmt)

        file_handler = logging.handlers.RotatingFileHandler(
            settings.LOG_FILE,
            maxBytes=10 * 1024 * 1024,  # 10 MB
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(file_fmt)
        file_handler.setLevel(logging.INFO)
        root_logger.addHandler(file_handler)
    except Exception as e:
        root_logger.warning(f"Log fayliga yozib bo'lmadi: {e}")

    # ── 3rd-party kutubxonalarning shovqinli loglarini pasaytirish ────────────
    for noisy_lib in ("aiohttp", "asyncio", "sqlalchemy.engine", "aiogram"):
        logging.getLogger(noisy_lib).setLevel(logging.WARNING)

    return root_logger


logger = setup_logging()
