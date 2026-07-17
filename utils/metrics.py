"""
Prometheus Metrics
===================
GuardBot barcha asosiy ko'rsatkichlarini Prometheus formatida eksport qiladi.

Metrikalar:
  guardbot_messages_processed_total   — qayta ishlangan xabarlar (label: type)
  guardbot_scans_total                — o'tkazilgan tekshiruvlar (label: result)
  guardbot_bans_total                 — ban qilinganlar (label: trigger)
  guardbot_clone_incidents_total      — klon hodisalari
  guardbot_risk_score_histogram       — risk score taqsimoti
  guardbot_media_jobs_total           — background media job'lar (label: status)
  guardbot_db_query_duration_seconds  — DB so'rovlari davomiyligi (histogram)
  guardbot_active_subscriptions       — aktiv obunalar soni (gauge, label: plan)
  guardbot_uptime_seconds             — bot ishlagan vaqt (gauge)
  guardbot_webhook_requests_total     — to'lov webhook'lari (label: provider, status)

Ishlatish:
  from utils.metrics import metrics
  metrics.messages_processed.labels(type="forward").inc()

Prometheus scrape: http://host:9090/metrics
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

try:
    from prometheus_client import (
        Counter, Histogram, Gauge, Summary,
        CollectorRegistry, generate_latest, CONTENT_TYPE_LATEST,
        multiprocess, REGISTRY,
    )
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False

from utils.logger import logger


# ─── No-op stub (prometheus_client o'rnatilmagan bo'lsa) ─────────────────────

class _Noop:
    """Prometheus o'rnatilmagan bo'lganda xatosiz ishlash uchun stub."""
    def labels(self, **_): return self
    def inc(self, amount=1): pass
    def dec(self, amount=1): pass
    def set(self, value): pass
    def observe(self, value): pass
    def time(self): return _NoopCtx()


class _NoopCtx:
    def __enter__(self): return self
    def __exit__(self, *_): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_): pass


def _counter(name, doc, labels=None):
    if not _HAS_PROMETHEUS:
        return _Noop()
    return Counter(name, doc, labels or [])


def _histogram(name, doc, labels=None, buckets=None):
    if not _HAS_PROMETHEUS:
        return _Noop()
    kwargs = {"labelnames": labels or []}
    if buckets:
        kwargs["buckets"] = buckets
    return Histogram(name, doc, **kwargs)


def _gauge(name, doc, labels=None):
    if not _HAS_PROMETHEUS:
        return _Noop()
    return Gauge(name, doc, labels or [])


# ─── Metrika ta'riflari ───────────────────────────────────────────────────────

class GuardBotMetrics:
    def __init__(self):
        self._start_time = time.time()

        # Xabarlar
        self.messages_processed = _counter(
            "guardbot_messages_processed_total",
            "Qayta ishlangan xabarlar soni",
            ["type"],  # "forward" | "media" | "text" | "channel_post"
        )

        # Tekshiruvlar
        self.scans_total = _counter(
            "guardbot_scans_total",
            "O'tkazilgan tekshiruvlar soni",
            ["result"],  # "clean" | "warn" | "ban" | "admin_confirm"
        )

        # Ban/unban
        self.bans_total = _counter(
            "guardbot_bans_total",
            "Ban qilingan foydalanuvchilar soni",
            ["trigger"],  # "auto" | "admin" | "media_job"
        )
        self.unbans_total = _counter(
            "guardbot_unbans_total",
            "Unban qilinganlar soni",
            [],
        )

        # Clone
        self.clone_incidents_total = _counter(
            "guardbot_clone_incidents_total",
            "Aniqlangan klon hodisalari soni",
            ["match_type"],  # "hash" | "phash" | "text" | "ocr"
        )

        # Risk score taqsimoti
        self.risk_score_histogram = _histogram(
            "guardbot_risk_score",
            "Risk score taqsimoti",
            [],
            buckets=[10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        )

        # Media jobs
        self.media_jobs_total = _counter(
            "guardbot_media_jobs_total",
            "Background media job'lar soni",
            ["status"],  # "enqueued" | "completed" | "failed"
        )

        # DB so'rovlari
        self.db_query_duration = _histogram(
            "guardbot_db_query_duration_seconds",
            "DB so'rovlari davomiyligi (soniya)",
            ["operation"],
            buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
        )

        # Aktiv obunalar
        self.active_subscriptions = _gauge(
            "guardbot_active_subscriptions",
            "Aktiv obunalar soni",
            ["plan"],  # "trial" | "basic" | "pro" | "enterprise"
        )

        # Uptime
        self.uptime_seconds = _gauge(
            "guardbot_uptime_seconds",
            "Bot ishlagan vaqt (soniya)",
            [],
        )

        # Webhook
        self.webhook_requests_total = _counter(
            "guardbot_webhook_requests_total",
            "To'lov webhook so'rovlari soni",
            ["provider", "status"],  # provider: "payme"|"click", status: "ok"|"error"
        )

        # Telegram API xatolari
        self.telegram_errors_total = _counter(
            "guardbot_telegram_errors_total",
            "Telegram API xatolari soni",
            ["error_type"],
        )

        # Watermark aniqlash
        self.watermarks_detected_total = _counter(
            "guardbot_watermarks_detected_total",
            "Watermark aniqlangan xabarlar soni",
            [],
        )

        if not _HAS_PROMETHEUS:
            logger.info("prometheus_client o'rnatilmagan — metrikalar o'chirilgan.")
        else:
            logger.info("Prometheus metrikalar ishga tushdi.")

    def update_uptime(self) -> None:
        self.uptime_seconds.set(time.time() - self._start_time)

    @asynccontextmanager
    async def measure_db(self, operation: str):
        """DB so'rovlari vaqtini o'lchash uchun context manager."""
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - start
            self.db_query_duration.labels(operation=operation).observe(elapsed)


# ─── Global singleton ─────────────────────────────────────────────────────────
metrics = GuardBotMetrics()


# ─── HTTP /metrics endpoint ───────────────────────────────────────────────────

async def metrics_http_handler(request) -> "aiohttp.web.Response":
    """
    Prometheus scraper uchun /metrics HTTP endpoint.
    bot.py'dagi web.Application ga qo'shiladi.
    """
    from aiohttp import web

    if not _HAS_PROMETHEUS:
        return web.Response(
            text="# prometheus_client o'rnatilmagan\n",
            content_type="text/plain",
        )

    metrics.update_uptime()

    # Aktiv obunalar sonini yangilaymiz
    try:
        await _refresh_subscription_gauges()
    except Exception as e:
        logger.debug(f"Subscription gauge yangilanmadi: {e}")

    output = generate_latest(REGISTRY)
    return web.Response(
        body=output,
        content_type=CONTENT_TYPE_LATEST,
    )


async def health_http_handler(request) -> "aiohttp.web.Response":
    """
    Kubernetes/Docker health check uchun /health endpoint.
    DB va Redis ulanishlarini tekshiradi.
    """
    from aiohttp import web
    from datetime import datetime

    checks: dict[str, str] = {}
    overall_ok = True

    # ── DB tekshiruvi ─────────────────────────────────────────────────────────
    try:
        from database.db import engine
        async with engine.connect() as conn:
            await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        overall_ok = False

    # ── Redis tekshiruvi ──────────────────────────────────────────────────────
    try:
        from utils.redis_client import redis_client
        await redis_client.ping()
        checks["redis"] = "ok"
    except Exception as e:
        checks["redis"] = f"error: {e}"
        overall_ok = False

    payload = {
        "status": "ok" if overall_ok else "degraded",
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "uptime_seconds": round(time.time() - metrics._start_time),
        "checks": checks,
        "version": "1.0.0",
    }

    import json
    return web.Response(
        text=json.dumps(payload, indent=2),
        content_type="application/json",
        status=200 if overall_ok else 503,
    )


async def _refresh_subscription_gauges() -> None:
    """Obuna gauge'larini DB dan yangilaydi."""
    from sqlalchemy import select, func
    from database.db import get_session
    from database.models import Subscription, SubscriptionStatus, SubscriptionPlan
    from datetime import datetime

    async with get_session() as session:
        result = await session.execute(
            select(Subscription.plan, func.count(Subscription.id))
            .where(
                Subscription.status == SubscriptionStatus.ACTIVE,
                (Subscription.expires_at == None)  # noqa: E711
                | (Subscription.expires_at > datetime.utcnow()),
            )
            .group_by(Subscription.plan)
        )
        counts = result.all()

    # Avval hammasini 0 ga tushiramiz
    for plan in SubscriptionPlan:
        metrics.active_subscriptions.labels(plan=plan.value).set(0)
    # Keyin haqiqiy qiymatlarni qo'yamiz
    for plan, count in counts:
        metrics.active_subscriptions.labels(plan=plan.value).set(count)
