"""
GuardBot — Entry Point (MVP v2)
================================
Ishga tushirish:
  polling : python3 bot.py
  webhook : BOT_USE_WEBHOOK=true python3 bot.py
"""
from __future__ import annotations

import asyncio
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

from config import settings
from database.db import init_models
from handlers import admin, channel_events, group_events, group_commands, security_events
from handlers.start import router as start_router
from handlers.onboarding import router as onboarding_router, set_role_commands
from middlewares.rate_limit import ThrottlingMiddleware
from middlewares.role_check import AdminOnlyMiddleware
from utils.logger import logger
from utils.metrics import metrics_http_handler, health_http_handler


# ─── FSM Storage ─────────────────────────────────────────────────────────────

async def _make_storage():
    """
    Redis mavjud bo'lsa RedisStorage, aks holda MemoryStorage.
    Ulanishni startup'da tekshiradi — xato bo'lsa jim o'tadi.
    """
    try:
        import redis.asyncio as aioredis
        from aiogram.fsm.storage.redis import RedisStorage

        r = aioredis.from_url(
            settings.REDIS_URL,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=False,
        )
        await r.ping()  # real ulanishni tekshirish
        logger.info("FSM storage: RedisStorage")
        return RedisStorage(redis=r)
    except Exception:
        logger.warning(
            "Redis ulanmadi — FSM uchun MemoryStorage ishlatiladi. "
            "Bot to'liq ishlaydi, lekin restart bo'lsa FSM holatlari yo'qoladi."
        )
        return MemoryStorage()


# ─── Dispatcher ──────────────────────────────────────────────────────────────

async def create_dispatcher() -> Dispatcher:
    storage = await _make_storage()
    dp = Dispatcher(storage=storage)

    dp.message.middleware(ThrottlingMiddleware())
    dp.message.middleware(AdminOnlyMiddleware())

    # Router tartibi: yuqoridan pastga qidiradi
    dp.include_router(start_router)           # /start, /help
    dp.include_router(onboarding_router)      # FSM: add_admin, add_channel, add_group
    dp.include_router(channel_events.router)  # kanal postlari + my_chat_member
    dp.include_router(group_commands.router)  # guruh ichida admin komandalar (/gban, /gwarn...)
    dp.include_router(group_events.router)    # guruh xabarlari + my_chat_member (+ Security Engine risk-check)
    dp.include_router(security_events.router) # join / captcha / raid (Security Engine v3)
    dp.include_router(admin.router)           # barcha admin komandalar

    return dp


# ─── Startup ─────────────────────────────────────────────────────────────────

async def on_startup(bot: Bot) -> None:
    logger.info("GuardBot ishga tushmoqda (MVP v2)...")

    # DB — mavjud jadvallar + ustun migratsiyalar
    try:
        await init_models()
        logger.info("DB tayyor.")
    except Exception as exc:
        logger.error(f"DB xato: {exc} — bot davom etadi.")

    # Har bir adminga rolga mos komandalar menyusi
    await _setup_admin_commands(bot)

    # Webhook (agar yoqilgan bo'lsa)
    if settings.BOT_USE_WEBHOOK:
        url = f"{settings.WEBHOOK_URL}{settings.WEBHOOK_PATH}"
        await bot.set_webhook(
            url=url,
            secret_token=settings.WEBHOOK_SECRET,
            drop_pending_updates=True,
        )
        logger.info(f"Webhook: {url}")

    logger.info("GuardBot ishga tushdi ✅")


async def _setup_admin_commands(bot: Bot) -> None:
    """Barcha adminlarga (config + DB) rolga mos /commands menyusini o'rnatadi."""
    from sqlalchemy import select as sa_select
    from database.db import get_session
    from database.models import Admin, AdminRole

    processed: set[int] = set()

    # 1. config super_admins
    for uid in settings.super_admins:
        try:
            await set_role_commands(bot, uid, AdminRole.SUPER_ADMIN)
            processed.add(uid)
        except Exception as exc:
            logger.warning(f"Komanda o'rnatilmadi {uid}: {exc}")

    # 2. DB adminlar
    try:
        async with get_session() as s:
            admins = (await s.execute(sa_select(Admin))).scalars().all()
        for a in admins:
            if a.telegram_id in processed:
                continue
            try:
                await set_role_commands(bot, a.telegram_id, a.role)
                processed.add(a.telegram_id)
            except Exception as exc:
                logger.warning(f"Komanda o'rnatilmadi {a.telegram_id}: {exc}")
    except Exception as exc:
        logger.warning(f"DB adminlar komandalar o'rnatilmadi: {exc}")

    logger.info(f"Komandalar o'rnatildi: {len(processed)} ta admin.")


# ─── Shutdown ────────────────────────────────────────────────────────────────

async def on_shutdown(bot: Bot) -> None:
    logger.info("GuardBot to'xtatilmoqda...")
    if settings.BOT_USE_WEBHOOK:
        await bot.delete_webhook()
    await bot.session.close()
    try:
        from database.db import close_engine
        from utils.redis_client import close_redis
        await close_engine()
        await close_redis()
    except Exception:
        pass
    logger.info("GuardBot to'xtatildi.")


# ─── Metrics server ──────────────────────────────────────────────────────────

async def _run_metrics_server() -> None:
    app = web.Application()
    app.router.add_get("/metrics", metrics_http_handler)
    app.router.add_get(settings.HEALTH_PATH, health_http_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", settings.METRICS_PORT)
    await site.start()
    logger.info(f"Metrics: http://0.0.0.0:{settings.METRICS_PORT}/metrics")
    while True:
        await asyncio.sleep(3600)


# ─── Captcha expiry worker (Security Engine v3) ───────────────────────────────

async def _run_captcha_expiry_worker(bot: Bot) -> None:
    """
    Har 10 soniyada muddati o'tgan (PENDING, expires_at < now) captcha
    sessiyalarini FAILED qiladi va foydalanuvchini kick qiladi (6-band:
    "Timeout 60 sec, Fail -> Kick").
    """
    from aiogram.exceptions import TelegramAPIError
    from security.captcha import captcha_manager
    from security.engine import SecurityEngine
    from utils.redis_client import redis_client

    engine = SecurityEngine(redis_client)

    while True:
        try:
            expired = await captcha_manager.expire_stale_sessions()
            for row in expired:
                try:
                    await bot.ban_chat_member(row.chat_id, row.user_id)
                    await bot.unban_chat_member(row.chat_id, row.user_id, only_if_banned=True)
                except TelegramAPIError as exc:
                    logger.warning(f"[captcha_worker] kick xato: {exc}")
                try:
                    await bot.delete_message(row.chat_id, row.message_id) if row.message_id else None
                except TelegramAPIError:
                    pass
                await engine.on_captcha_result(row.chat_id, row.user_id, False)
        except Exception as exc:  # pragma: no cover — worker o'zi to'xtamasin
            logger.error(f"[captcha_worker] xato: {exc}")

        await asyncio.sleep(10)


# ─── Graceful stop ────────────────────────────────────────────────────────────

async def _graceful_stop(dp: Dispatcher, bot: Bot) -> None:
    logger.info("Stop signali olindi...")
    await dp.stop_polling()


# ─── Polling ─────────────────────────────────────────────────────────────────

async def _run_polling(bot: Bot, dp: Dispatcher) -> None:
    dp.startup.register(on_startup)
    dp.shutdown.register(on_shutdown)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                sig, lambda: asyncio.create_task(_graceful_stop(dp, bot))
            )
        except (NotImplementedError, RuntimeError):
            pass

    tasks: list[asyncio.Task] = [
        asyncio.create_task(
            dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types()),
            name="polling",
        ),
        asyncio.create_task(_run_captcha_expiry_worker(bot), name="captcha_expiry"),
    ]
    if settings.METRICS_ENABLED:
        tasks.append(asyncio.create_task(_run_metrics_server(), name="metrics"))

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        pass
    finally:
        for t in tasks:
            t.cancel()


def run_polling() -> None:
    async def _main() -> None:
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = await create_dispatcher()
        await _run_polling(bot, dp)

    asyncio.run(_main())


# ─── Webhook ─────────────────────────────────────────────────────────────────

def run_webhook() -> None:
    async def _main() -> None:
        bot = Bot(
            token=settings.BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        dp = await create_dispatcher()
        dp.startup.register(on_startup)
        dp.shutdown.register(on_shutdown)

        app = web.Application()
        SimpleRequestHandler(
            dispatcher=dp, bot=bot, secret_token=settings.WEBHOOK_SECRET
        ).register(app, path=settings.WEBHOOK_PATH)
        setup_application(app, dp, bot=bot)
        app.router.add_get(settings.HEALTH_PATH, health_http_handler)
        if settings.METRICS_ENABLED:
            app.router.add_get("/metrics", metrics_http_handler)

        async def _start_captcha_worker(_app: web.Application) -> None:
            _app["captcha_worker_task"] = asyncio.create_task(
                _run_captcha_expiry_worker(bot), name="captcha_expiry"
            )

        app.on_startup.append(_start_captcha_worker)

        web.run_app(app, host=settings.WEBAPP_HOST, port=settings.WEBAPP_PORT)

    asyncio.run(_main())


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mode = "webhook" if settings.BOT_USE_WEBHOOK else "polling"
    logger.info(f"Rejim: {mode}")
    if settings.BOT_USE_WEBHOOK:
        run_webhook()
    else:
        run_polling()
