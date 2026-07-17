"""
Admin Panel — MVP v2
======================
Barcha admin komandalar. RBAC middlewares/role_check.py orqali tekshiriladi.

Komandalar:
  /stats            — umumiy statistika (banlar, tekshiruvlar, kanallar, guruhlar)
  /banned [sahifa]  — ban ro'yxati (sahifali)
  /unban <id> <cid> — blokdan chiqarish
  /whitelist        — ko'rish | add <id> | remove <id>
  /scan_history     — so'nggi tekshiruvlar
  /export_logs      — JSON fayl sifatida audit log
  /settings         — joriy sozlamalar
  /channels         — himoyalangan kanallar ro'yxati
  /groups           — himoyalangan guruhlar ro'yxati
  /admins           — adminlar ro'yxati (Super Admin ko'ra oladi)
"""
from __future__ import annotations

import json
from datetime import datetime

from aiogram import Router, Bot, F
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    BufferedInputFile, CallbackQuery,
    InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select, func, desc

from config import settings
from core.ban_manager import BanManager
from database.db import get_session
from database.models import (
    ActionType, Admin, AdminRole, AuditLog,
    BannedUser, Channel, ProtectedGroup, User, Whitelist,
)
from middlewares.role_check import get_admin_role
from utils.logger import logger

router = Router(name="admin")

PAGE_SIZE = 10


async def _db_error(message: Message, err: Exception) -> None:
    await message.answer(f"❌ <b>DB xato:</b> <code>{err}</code>")
    logger.error(f"DB xato: {err}")


# ─── /stats ──────────────────────────────────────────────────────────────────

@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    try:
        async with get_session() as s:
            total_users    = (await s.execute(select(func.count(User.id)))).scalar_one()
            total_banned   = (await s.execute(select(func.count(BannedUser.id)))).scalar_one()
            total_channels = (await s.execute(
                select(func.count(Channel.id)).where(Channel.is_active == True)  # noqa
            )).scalar_one()
            total_groups   = (await s.execute(
                select(func.count(ProtectedGroup.id)).where(ProtectedGroup.is_active == True)  # noqa
            )).scalar_one()
            total_admins   = (await s.execute(select(func.count(Admin.id)))).scalar_one()
            total_scans    = (await s.execute(
                select(func.count(AuditLog.id)).where(AuditLog.action == ActionType.SCAN)
            )).scalar_one()
            total_bans_log = (await s.execute(
                select(func.count(AuditLog.id)).where(AuditLog.action == ActionType.BAN)
            )).scalar_one()
            total_clones   = (await s.execute(
                select(func.count(AuditLog.id)).where(AuditLog.action == ActionType.CLONE_DETECTED)
            )).scalar_one()
    except Exception as exc:
        await _db_error(message, exc)
        return

    await message.answer(
        "📊 <b>GuardBot statistikasi</b>\n\n"
        f"🔒 Himoyalangan kanallar:  <b>{total_channels}</b>\n"
        f"👥 Himoyalangan guruhlar:  <b>{total_groups}</b>\n"
        f"👤 Adminlar:               <b>{total_admins}</b>\n\n"
        f"👁 Kuzatilgan foydalanuvchilar: <b>{total_users}</b>\n"
        f"🔍 Tekshiruvlar (scan):    <b>{total_scans}</b>\n"
        f"🚫 Banlar (jami):          <b>{total_bans_log}</b>  (hozir aktiv: {total_banned})\n"
        f"🔴 Klon hodisalari:        <b>{total_clones}</b>"
    )


# ─── /banned ─────────────────────────────────────────────────────────────────

@router.message(Command("banned"))
async def cmd_banned(message: Message, command: CommandObject) -> None:
    page = int(command.args) if command.args and command.args.isdigit() else 1
    try:
        async with get_session() as s:
            total = (await s.execute(select(func.count(BannedUser.id)))).scalar_one()
            rows  = (await s.execute(
                select(BannedUser)
                .order_by(desc(BannedUser.banned_at))
                .offset((page - 1) * PAGE_SIZE)
                .limit(PAGE_SIZE)
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    if not rows:
        await message.answer("🟢 Bloklangan foydalanuvchilar yo'q.")
        return

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    lines = [f"🚫 <b>Bloklanganlar</b> — {page}/{total_pages} sahifa ({total} ta)\n"]
    for r in rows:
        lines.append(
            f"• <code>{r.user_id}</code> | chat <code>{r.chat_id}</code>\n"
            f"  📅 {r.banned_at:%d.%m.%Y %H:%M} | {r.reason[:60]}"
        )
    nav = []
    if page > 1:
        nav.append(f"/banned {page - 1}")
    if page < total_pages:
        nav.append(f"/banned {page + 1}")
    if nav:
        lines.append("\n" + " | ".join(nav))

    await message.answer("\n".join(lines))


# ─── /unban ──────────────────────────────────────────────────────────────────

@router.message(Command("unban"))
async def cmd_unban(message: Message, command: CommandObject, bot: Bot) -> None:
    if not command.args:
        await message.answer(
            "ℹ️ Foydalanish:\n"
            "<code>/unban &lt;user_id&gt; &lt;chat_id&gt;</code>\n\n"
            "Misol: <code>/unban 123456789 -1001234567890</code>"
        )
        return
    parts = command.args.split()
    if len(parts) != 2 or not all(p.lstrip("-").isdigit() for p in parts):
        await message.answer("❌ Format: <code>/unban &lt;user_id&gt; &lt;chat_id&gt;</code>")
        return

    user_id, chat_id = int(parts[0]), int(parts[1])
    bm = BanManager(bot)
    try:
        ok = await bm.unban(user_id, chat_id, unbanned_by=message.from_user.id)
    except Exception as exc:
        await message.answer(f"❌ Xato: <code>{exc}</code>")
        return

    if ok:
        await message.answer(
            f"✅ <code>{user_id}</code> blokdan chiqarildi.\n"
            f"Guruh/kanal: <code>{chat_id}</code>"
        )
        logger.info(f"[UNBAN] user={user_id} chat={chat_id} by={message.from_user.id}")
    else:
        await message.answer(f"⚠️ <code>{user_id}</code> bloklangan emas yoki xatolik yuz berdi.")


# ─── /whitelist ───────────────────────────────────────────────────────────────

@router.message(Command("whitelist"))
async def cmd_whitelist(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    action = args[0].lower() if args else ""

    if action not in ("add", "remove"):
        # Ro'yxatni ko'rsatish
        try:
            async with get_session() as s:
                rows = (await s.execute(
                    select(Whitelist).order_by(desc(Whitelist.added_at)).limit(50)
                )).scalars().all()
        except Exception as exc:
            await _db_error(message, exc)
            return

        if not rows:
            await message.answer(
                "📝 <b>Whitelist bo'sh</b>\n\n"
                "Qo'shish: <code>/whitelist add &lt;user_id&gt; [izoh]</code>"
            )
            return
        lines = [f"📝 <b>Whitelist</b> ({len(rows)} ta)\n"]
        for r in rows:
            lines.append(
                f"• <code>{r.user_id}</code> — {r.note or '—'} "
                f"({r.added_at:%d.%m.%Y})"
            )
        await message.answer("\n".join(lines))
        return

    if len(args) < 2 or not args[1].lstrip("-").isdigit():
        await message.answer(
            "❌ Foydalanish:\n"
            "<code>/whitelist add &lt;user_id&gt; [izoh]</code>\n"
            "<code>/whitelist remove &lt;user_id&gt;</code>"
        )
        return

    uid  = int(args[1])
    note = " ".join(args[2:]) if len(args) > 2 else None

    try:
        async with get_session() as s:
            if action == "add":
                existing = (await s.execute(
                    select(Whitelist).where(Whitelist.user_id == uid)
                )).scalar_one_or_none()
                if existing:
                    await message.answer(f"ℹ️ <code>{uid}</code> allaqachon whitelist'da.")
                    return
                s.add(Whitelist(user_id=uid, added_by=message.from_user.id, note=note))
                s.add(AuditLog(
                    user_id=uid, action=ActionType.WHITELIST_ADD,
                    reason=f"Admin {message.from_user.id} tomonidan qo'shildi",
                ))
                await message.answer(f"✅ <code>{uid}</code> whitelist'ga qo'shildi.")
            else:
                entry = (await s.execute(
                    select(Whitelist).where(Whitelist.user_id == uid)
                )).scalar_one_or_none()
                if not entry:
                    await message.answer(f"⚠️ <code>{uid}</code> whitelist'da topilmadi.")
                    return
                await s.delete(entry)
                s.add(AuditLog(
                    user_id=uid, action=ActionType.WHITELIST_REMOVE,
                    reason=f"Admin {message.from_user.id} tomonidan olib tashlandi",
                ))
                await message.answer(f"✅ <code>{uid}</code> whitelist'dan olib tashlandi.")
    except Exception as exc:
        await _db_error(message, exc)


# ─── /scan_history ────────────────────────────────────────────────────────────

@router.message(Command("scan_history"))
async def cmd_scan_history(message: Message, command: CommandObject) -> None:
    limit = min(int(command.args), 50) if command.args and command.args.isdigit() else 15
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(AuditLog)
                .where(AuditLog.action == ActionType.SCAN)
                .order_by(desc(AuditLog.created_at))
                .limit(limit)
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    if not rows:
        await message.answer("🔍 Tekshiruv tarixi bo'sh.")
        return

    lines = [f"🔍 <b>So'nggi {len(rows)} ta tekshiruv</b>\n"]
    for r in rows:
        risk = r.risk_score or 0
        icon = "🔴" if risk >= 80 else "🟡" if risk >= 50 else "🟢"
        lines.append(
            f"{icon} {r.created_at:%d.%m %H:%M} | "
            f"user <code>{r.user_id or '—'}</code> | "
            f"risk <b>{risk:.0f}%</b>"
        )
    await message.answer("\n".join(lines))


# ─── /export_logs ─────────────────────────────────────────────────────────────

@router.message(Command("export_logs"))
async def cmd_export_logs(message: Message, command: CommandObject) -> None:
    limit = min(int(command.args), 5000) if command.args and command.args.isdigit() else 1000
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(AuditLog).order_by(desc(AuditLog.created_at)).limit(limit)
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    data = [
        {
            "id":         r.id,
            "user_id":    r.user_id,
            "chat_id":    r.chat_id,
            "action":     r.action.value,
            "reason":     r.reason,
            "risk_score": r.risk_score,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    fname   = f"guardbot_logs_{datetime.utcnow():%Y%m%d_%H%M%S}.json"
    await message.answer_document(
        BufferedInputFile(payload, filename=fname),
        caption=f"📤 <b>{len(data)}</b> ta log yozuvi eksport qilindi.",
    )


# ─── /settings ────────────────────────────────────────────────────────────────

@router.message(Command("settings"))
async def cmd_settings(message: Message) -> None:
    role = await get_admin_role(message.from_user.id)
    if role != AdminRole.SUPER_ADMIN:
        await message.answer("⛔️ Bu komanda faqat Super Admin uchun.")
        return
    await message.answer(
        "⚙️ <b>Joriy sozlamalar</b>\n\n"
        f"🚫 Auto-ban chegarasi:      <b>{settings.AUTO_BAN_RISK_THRESHOLD}%</b>\n"
        f"⚠️ Admin tasdiqi chegarasi: <b>{settings.ADMIN_CONFIRM_RISK_THRESHOLD}%</b>\n"
        f"⏱ Forward limit:            <b>{settings.RATE_LIMIT_FORWARDS}</b> / "
        f"{settings.RATE_LIMIT_WINDOW_SECONDS}s\n"
        f"🔢 Hash threshold:          <b>{settings.HASH_SIMILARITY_THRESHOLD}</b>\n"
        f"🔤 OCR threshold:           <b>{settings.OCR_SIMILARITY_THRESHOLD}</b>\n"
        f"📡 Clone scan interval:     <b>{settings.CLONE_SCAN_INTERVAL_SECONDS}s</b>\n\n"
        "📝 O'zgartirish uchun <code>.env</code> faylini tahrirlang va botni qayta ishga tushiring."
    )


# ─── /channels ────────────────────────────────────────────────────────────────

@router.message(Command("channels"))
async def cmd_channels(message: Message) -> None:
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(Channel).order_by(desc(Channel.added_at))
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    if not rows:
        await message.answer(
            "📢 Himoyalangan kanallar yo'q.\n\n"
            "/add_channel orqali qo'shing."
        )
        return

    active   = [r for r in rows if r.is_active]
    inactive = [r for r in rows if not r.is_active]

    lines = [f"📢 <b>Himoyalangan kanallar</b> ({len(rows)} ta)\n"]
    for r in active:
        link = f"@{r.username}" if r.username else f"<code>{r.chat_id}</code>"
        lines.append(f"✅ {r.title or '—'} {link}\n   🆔 <code>{r.chat_id}</code>")
    if inactive:
        lines.append(f"\n⛔ <b>Nofaol ({len(inactive)} ta):</b>")
        for r in inactive:
            lines.append(f"   — {r.title or r.chat_id} <code>{r.chat_id}</code>")

    lines.append(f"\n➕ Yangi kanal qo'shish: /add_channel")
    await message.answer("\n".join(lines))


# ─── /groups ──────────────────────────────────────────────────────────────────

@router.message(Command("groups"))
async def cmd_groups(message: Message) -> None:
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(ProtectedGroup).order_by(desc(ProtectedGroup.added_at))
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    if not rows:
        await message.answer(
            "👥 Himoyalangan guruhlar yo'q.\n\n"
            "/add_group orqali qo'shing."
        )
        return

    active   = [r for r in rows if r.is_active]
    inactive = [r for r in rows if not r.is_active]

    lines = [f"👥 <b>Himoyalangan guruhlar</b> ({len(rows)} ta)\n"]
    for r in active:
        link        = f"@{r.username}" if r.username else f"<code>{r.chat_id}</code>"
        admin_badge = "🔑" if r.bot_is_admin else "⚠️ admin emas"
        lines.append(
            f"✅ {r.title or '—'} {link} {admin_badge}\n"
            f"   🆔 <code>{r.chat_id}</code>"
        )
    if inactive:
        lines.append(f"\n⛔ <b>Nofaol ({len(inactive)} ta):</b>")
        for r in inactive:
            lines.append(f"   — {r.title or r.chat_id} <code>{r.chat_id}</code>")

    lines.append(f"\n➕ Yangi guruh qo'shish: /add_group")
    await message.answer("\n".join(lines))


# ─── /admins ──────────────────────────────────────────────────────────────────

@router.message(Command("admins"))
async def cmd_admins(message: Message) -> None:
    role = await get_admin_role(message.from_user.id)
    if role != AdminRole.SUPER_ADMIN:
        await message.answer("⛔️ Bu komanda faqat Super Admin uchun.")
        return
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(Admin).order_by(Admin.role, Admin.added_at)
            )).scalars().all()
    except Exception as exc:
        await _db_error(message, exc)
        return

    role_icons = {
        AdminRole.SUPER_ADMIN: "👑",
        AdminRole.MODERATOR:   "🛡",
        AdminRole.VIEWER:      "👁",
    }
    if not rows:
        await message.answer("👤 Hech qanday admin DB da yo'q.\n/add_admin orqali qo'shing.")
        return

    lines = [f"👤 <b>Adminlar ro'yxati</b> ({len(rows)} ta)\n"]
    for a in rows:
        icon = role_icons.get(a.role, "")
        name = f"@{a.username}" if a.username else (a.full_name or f"<code>{a.telegram_id}</code>")
        lines.append(
            f"{icon} {name} — <b>{a.role.value}</b>\n"
            f"   ID: <code>{a.telegram_id}</code> | "
            f"Qo'shildi: {a.added_at:%d.%m.%Y}"
        )

    # Config dan super adminlar (DB da bo'lmasligi mumkin)
    lines.append("\n<i>Config super admins:</i>")
    for uid in settings.super_admins:
        lines.append(f"👑 <code>{uid}</code> (config)")

    await message.answer("\n".join(lines))


# ─── /remove_admin ────────────────────────────────────────────────────────────

@router.message(Command("remove_admin"))
async def cmd_remove_admin(message: Message, command: CommandObject) -> None:
    role = await get_admin_role(message.from_user.id)
    if role != AdminRole.SUPER_ADMIN:
        return
    if not command.args or not command.args.strip().isdigit():
        await message.answer(
            "ℹ️ Foydalanish: <code>/remove_admin &lt;user_id&gt;</code>"
        )
        return
    uid = int(command.args.strip())
    if uid in settings.super_admins:
        await message.answer(
            "⛔️ Config'dagi Super Admin o'chirib bo'lmaydi.\n"
            ".env faylidan SUPER_ADMIN_IDS dan olib tashlang."
        )
        return
    try:
        async with get_session() as s:
            admin = (await s.execute(
                select(Admin).where(Admin.telegram_id == uid)
            )).scalar_one_or_none()
            if not admin:
                await message.answer(f"⚠️ <code>{uid}</code> adminlar ro'yxatida topilmadi.")
                return
            await s.delete(admin)
    except Exception as exc:
        await _db_error(message, exc)
        return

    await message.answer(f"✅ <code>{uid}</code> adminlar ro'yxatidan o'chirildi.")
    logger.info(f"[ADMIN] remove_admin: uid={uid} by={message.from_user.id}")


# ─── confirm_ban / ignore_ban callback'lari ──────────────────────────────────
# (group_events.py da ham ishlaydi, lekin admin.py da ham ro'yxatga olamiz
#  agar admin private chatda tugmani bosgan bo'lsa)

@router.callback_query(F.data.startswith("confirm_ban:"))
async def cb_confirm_ban(cb: CallbackQuery, bot: Bot) -> None:
    role = await get_admin_role(cb.from_user.id)
    if role is None:
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()
    parts   = cb.data.split(":")
    user_id = int(parts[1])
    chat_id = int(parts[2])
    msg_id  = int(parts[3])

    bm = BanManager(bot)
    await bm.execute_ban(
        user_id=user_id, chat_id=chat_id,
        reason=f"Admin {cb.from_user.id} tomonidan tasdiqlab ban qilindi",
        evidence={"type": "admin_confirmed_private", "original_msg": msg_id},
        risk_score=100.0, banned_by=cb.from_user.id,
    )
    try:
        await bot.delete_message(chat_id=chat_id, message_id=msg_id)
    except TelegramAPIError:
        pass
    await cb.message.edit_text(
        f"✅ <code>{user_id}</code> ban qilindi.\n"
        f"Admin: @{cb.from_user.username or cb.from_user.id}"
    )


@router.callback_query(F.data.startswith("ignore_ban:"))
async def cb_ignore_ban(cb: CallbackQuery) -> None:
    role = await get_admin_role(cb.from_user.id)
    if role is None:
        await cb.answer("⛔️ Ruxsat yo'q.", show_alert=True)
        return
    await cb.answer()
    parts   = cb.data.split(":")
    user_id = parts[1]
    await cb.message.edit_text(
        f"➖ <code>{user_id}</code> — e'tiborsiz qoldirildi.\n"
        f"Admin: @{cb.from_user.username or cb.from_user.id}"
    )
