"""
Start Handler — MVP v2
========================
Rolga qarab to'liq dinamik menyu:

  Super Admin — barcha tugmalar (statistika, banlar, kanal, guruh, admin, sozlamalar)
  Moderator   — statistika, banlar, unban, whitelist, kanal/guruh qo'shish, log export
  Viewer      — faqat statistika, banlar, tekshiruv tarixi (ko'rish huquqi)
  Oddiy user  — faqat salomlashuv (admin komandalar ko'rinmaydi, hech narsa ochilmaydi)
"""
from __future__ import annotations

from aiogram import F, Router, Bot
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from database.models import AdminRole
from middlewares.role_check import get_admin_role
from utils.logger import logger

router = Router(name="start")


# ─── Rolga qarab keyboard ─────────────────────────────────────────────────────

def _build_menu(role: AdminRole) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    # Barcha adminlar (Viewer+)
    if role in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR, AdminRole.VIEWER):
        rows.append([
            InlineKeyboardButton(text="📊 Statistika",       callback_data="cmd:stats"),
            InlineKeyboardButton(text="🚫 Ban ro'yxati",     callback_data="cmd:banned"),
        ])
        rows.append([
            InlineKeyboardButton(text="🔍 Tekshiruv tarixi", callback_data="cmd:scan_history"),
        ])

    # Moderator va yuqori
    if role in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
        rows.append([
            InlineKeyboardButton(text="✅ Unban",             callback_data="cmd:unban_hint"),
            InlineKeyboardButton(text="📝 Whitelist",        callback_data="cmd:whitelist"),
        ])
        rows.append([
            InlineKeyboardButton(text="📤 Log eksport",      callback_data="cmd:export_logs"),
        ])
        rows.append([
            InlineKeyboardButton(text="📢 Kanal qo'shish",   callback_data="cmd:add_channel"),
            InlineKeyboardButton(text="👥 Guruh qo'shish",   callback_data="cmd:add_group"),
        ])
        rows.append([
            InlineKeyboardButton(text="📢 Kanallar ro'yxati", callback_data="cmd:channels"),
            InlineKeyboardButton(text="👥 Guruhlar ro'yxati", callback_data="cmd:groups"),
        ])

    # Faqat Super Admin
    if role == AdminRole.SUPER_ADMIN:
        rows.append([
            InlineKeyboardButton(text="👤 Admin qo'shish",   callback_data="cmd:add_admin"),
            InlineKeyboardButton(text="👤 Adminlar ro'yxati",callback_data="cmd:admins"),
        ])
        rows.append([
            InlineKeyboardButton(text="⚙️ Sozlamalar",       callback_data="cmd:settings"),
        ])

    rows.append([
        InlineKeyboardButton(text="❓ Qo'llanma",            callback_data="cmd:help"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _role_badge(role: AdminRole) -> str:
    return {
        AdminRole.SUPER_ADMIN: "👑 Super Admin",
        AdminRole.MODERATOR:   "🛡 Moderator",
        AdminRole.VIEWER:      "👁 Viewer",
    }.get(role, "")


# ─── /start ───────────────────────────────────────────────────────────────────

@router.message(CommandStart())
async def cmd_start(message: Message, bot: Bot) -> None:
    user = message.from_user
    if user is None:
        return

    role = await get_admin_role(user.id)

    # Oddiy foydalanuvchi
    if role is None:
        await message.answer(
            "👋 <b>GuardBot</b> — Telegram kanal himoya tizimi.\n\n"
            "Bu bot faqat adminlar uchun mo'ljallangan.\n"
            "Agar admin bo'lsangiz, botga /start bering."
        )
        return

    bot_info = await bot.get_me()
    name = user.first_name or user.username or "Admin"

    welcome = (
        f"🛡 <b>GuardBot — Kontent Himoya Tizimi</b>\n\n"
        f"Salom, <b>{name}</b>! {_role_badge(role)}\n\n"
        f"Bot: @{bot_info.username}\n\n"
        "<b>Tizim ishlash tartibi:</b>\n"
        "├ Bot admin bo'lgan <b>kanallar</b> → postlar himoyaga olinadi\n"
        "├ Bot admin bo'lgan <b>guruhlarda</b> → forward/media ban ishlaydi\n"
        "├ Oddiy user forward qilsa → <b>darhol ban</b>\n"
        "├ Screenshot/nusxa → hash/OCR tahlil → <b>risk scoring</b>\n"
        "└ Barcha harakatlar <b>audit log</b>ga yoziladi\n\n"
        "Quyidagi menyudan foydalaning 👇"
    )
    await message.answer(welcome, reply_markup=_build_menu(role))
    logger.info(f"[START] user={user.id} (@{user.username}) role={role.value}")


# ─── /help ────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    role = await get_admin_role(message.from_user.id)
    if role is None:
        return

    base = (
        "📚 <b>GuardBot — Qo'llanma</b>\n\n"
        "<b>Asosiy komandalar:</b>\n"
        "/start — Bosh menyu\n"
        "/stats — Statistika\n"
        "/banned [sahifa] — Ban ro'yxati\n"
        "/scan_history [limit] — Tekshiruv tarixi\n"
    )
    mod_extra = (
        "\n<b>Moderator komandalar:</b>\n"
        "/unban &lt;user_id&gt; &lt;chat_id&gt; — Blokdan chiqarish\n"
        "/whitelist — Ko'rish\n"
        "/whitelist add &lt;id&gt; [izoh] — Qo'shish\n"
        "/whitelist remove &lt;id&gt; — O'chirish\n"
        "/export_logs [limit] — Log fayli\n"
        "/add_channel — Kanal himoyaga olish\n"
        "/add_group — Guruh himoyaga olish\n"
    )
    super_extra = (
        "\n<b>Super Admin komandalar:</b>\n"
        "/add_admin — Yangi admin qo'shish (FSM)\n"
        "/admins — Adminlar ro'yxati\n"
        "/remove_admin &lt;id&gt; — Adminni o'chirish\n"
        "/settings — Joriy sozlamalar\n"
        "/channels — Himoyalangan kanallar\n"
        "/groups — Himoyalangan guruhlar\n"
    )

    text = base
    if role in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
        text += mod_extra
    if role == AdminRole.SUPER_ADMIN:
        text += super_extra

    await message.answer(text)


# ─── Menyu tugmalari callback ─────────────────────────────────────────────────

@router.callback_query(F.data.startswith("cmd:"))
async def on_menu_callback(cb: CallbackQuery, bot: Bot) -> None:
    role = await get_admin_role(cb.from_user.id)
    if role is None:
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()

    action = cb.data[4:]  # "cmd:stats" → "stats"

    # ── FSM oqimlarini ishga tushiruvchi tugmalar ─────────────────────────────
    if action == "add_admin":
        if role != AdminRole.SUPER_ADMIN:
            await cb.message.answer("⛔️ Bu amal faqat Super Admin uchun.")
            return
        await cb.message.answer(
            "👤 /add_admin komandasini yozing yoki bosing — FSM oqimi boshlanadi."
        )
        return

    if action == "add_channel":
        if role not in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
            await cb.message.answer("⛔️ Ruxsat yo'q.")
            return
        await cb.message.answer(
            "📢 /add_channel komandasini yozing — kanal qo'shish bosqichlari boshlanadi."
        )
        return

    if action == "add_group":
        if role not in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
            await cb.message.answer("⛔️ Ruxsat yo'q.")
            return
        await cb.message.answer(
            "👥 /add_group komandasini yozing — guruh qo'shish bosqichlari boshlanadi."
        )
        return

    if action == "unban_hint":
        await cb.message.answer(
            "✅ <b>Blokdan chiqarish</b>\n\n"
            "Foydalanish: <code>/unban &lt;user_id&gt; &lt;chat_id&gt;</code>\n\n"
            "Misol: <code>/unban 123456789 -1001234567890</code>"
        )
        return

    # ── To'g'ridan-to'g'ri handler chaqirish ──────────────────────────────────
    await _dispatch_command(action, cb, role)


async def _dispatch_command(action: str, cb: CallbackQuery, role: AdminRole) -> None:
    """Tugmadan tegishli admin handler ni chaqiradi."""
    from handlers.admin import (
        cmd_stats, cmd_banned, cmd_scan_history,
        cmd_export_logs, cmd_settings, cmd_whitelist,
        cmd_groups, cmd_channels, cmd_admins,
    )

    class _FakeCmd:
        args = None

    fake = _FakeCmd()
    msg  = cb.message

    dispatch = {
        "stats":        lambda: cmd_stats(msg),
        "banned":       lambda: cmd_banned(msg, fake),
        "scan_history": lambda: cmd_scan_history(msg, fake),
        "export_logs":  lambda: cmd_export_logs(msg, fake),
        "settings":     lambda: cmd_settings(msg),
        "whitelist":    lambda: cmd_whitelist(msg, fake),
        "groups":       lambda: cmd_groups(msg),
        "channels":     lambda: cmd_channels(msg),
        "admins":       lambda: cmd_admins(msg),
        "help":         lambda: cmd_help(msg),
    }

    fn = dispatch.get(action)
    if fn:
        try:
            await fn()
        except Exception as exc:
            await msg.answer(f"❌ Xato: <code>{exc}</code>")
    else:
        await msg.answer(f"ℹ️ /{action} komandasini qo'lda yozing.")
