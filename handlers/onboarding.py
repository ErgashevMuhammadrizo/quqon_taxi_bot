"""
Onboarding FSM — MVP v2
========================
Uchta to'liq FSM oqimi (hech qanday ID qo'lda kiritilmaydi):

  1. /add_admin  — forward/kontakt → avtomatik ID → rol tugmasi → DB → xabar
  2. /add_channel — kanal postini forward → avtomatik ID → bot admin tekshiruvi → DB
  3. /add_group  — guruh xabari forward yoki guruhdan bevosita → bot admin tekshiruvi → DB

Har bir oqim:
  - Xato holatlarda aniq tushuntirishli xabar beradi
  - Muvaffaqiyatda yangi adminga/a'zoga shaxsiy xabar yuboradi
  - Barcha Super Adminlarga bildirishnoma jo'natadi
  - Rolga mos /commands menyusini o'rnatadi
"""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand, BotCommandScopeAllChatAdministrators, BotCommandScopeChat,
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message,
)
from sqlalchemy import select

from config import settings
from database.db import get_session
from database.models import ActionType, Admin, AdminRole, AuditLog, Channel, ProtectedGroup
from middlewares.role_check import get_admin_role
from utils.logger import logger

router = Router(name="onboarding")


# ─── FSM States ───────────────────────────────────────────────────────────────

class AddAdminFSM(StatesGroup):
    waiting_for_user   = State()   # forward yoki kontakt kutish
    waiting_for_role   = State()   # rol tanlash tugmasi kutish


class AddChannelFSM(StatesGroup):
    waiting_for_post   = State()   # kanal postini forward kutish


class AddGroupFSM(StatesGroup):
    waiting_for_msg    = State()   # guruh xabarini forward yoki link kutish


# ─── Rol buyruqlari xaritasi ─────────────────────────────────────────────────

_COMMANDS: dict[AdminRole, list[BotCommand]] = {
    AdminRole.SUPER_ADMIN: [
        BotCommand(command="start",        description="🏠 Bosh menyu"),
        BotCommand(command="stats",        description="📊 Statistika"),
        BotCommand(command="banned",       description="🚫 Ban ro'yxati"),
        BotCommand(command="unban",        description="✅ Blokdan chiqarish"),
        BotCommand(command="whitelist",    description="📝 Whitelist"),
        BotCommand(command="scan_history", description="🔍 Tekshiruv tarixi"),
        BotCommand(command="export_logs",  description="📤 Log eksport"),
        BotCommand(command="settings",     description="⚙️ Sozlamalar"),
        BotCommand(command="add_admin",    description="👤 Admin qo'shish"),
        BotCommand(command="admins",       description="👤 Adminlar ro'yxati"),
        BotCommand(command="add_channel",  description="📢 Kanal qo'shish"),
        BotCommand(command="channels",     description="📢 Kanallar ro'yxati"),
        BotCommand(command="add_group",    description="👥 Guruh qo'shish"),
        BotCommand(command="groups",       description="👥 Guruhlar ro'yxati"),
    ],
    AdminRole.MODERATOR: [
        BotCommand(command="start",        description="🏠 Bosh menyu"),
        BotCommand(command="stats",        description="📊 Statistika"),
        BotCommand(command="banned",       description="🚫 Ban ro'yxati"),
        BotCommand(command="unban",        description="✅ Blokdan chiqarish"),
        BotCommand(command="whitelist",    description="📝 Whitelist"),
        BotCommand(command="scan_history", description="🔍 Tekshiruv tarixi"),
        BotCommand(command="export_logs",  description="📤 Log eksport"),
        BotCommand(command="add_channel",  description="📢 Kanal qo'shish"),
        BotCommand(command="channels",     description="📢 Kanallar ro'yxati"),
        BotCommand(command="add_group",    description="👥 Guruh qo'shish"),
        BotCommand(command="groups",       description="👥 Guruhlar ro'yxati"),
    ],
    AdminRole.VIEWER: [
        BotCommand(command="start",        description="🏠 Bosh menyu"),
        BotCommand(command="stats",        description="📊 Statistika"),
        BotCommand(command="banned",       description="🚫 Ban ro'yxati"),
        BotCommand(command="scan_history", description="🔍 Tekshiruv tarixi"),
    ],
}

# ─── Guruh ichida ko'rinadigan komandalar (barcha guruh adminlariga) ──────────
# BotCommandScopeAllChatAdministrators — guruhda bot menyusini ochganda
# faqat admin bo'lgan foydalanuvchilarga ko'rsatiladi.

_GROUP_ADMIN_COMMANDS: list[BotCommand] = [
    BotCommand(command="gban",    description="🚫 Guruhdan ban qilish"),
    BotCommand(command="gunban",  description="✅ Guruhda blokdan chiqarish"),
    BotCommand(command="gmute",   description="🔇 Vaqtincha sukut qilish"),
    BotCommand(command="gunmute", description="🔊 Sukutdan chiqarish"),
    BotCommand(command="gwarn",   description="⚠️ Ogohlantirish (3 ta = ban)"),
    BotCommand(command="ginfo",   description="👤 Foydalanuvchi ma'lumoti"),
    BotCommand(command="gstatus", description="🛡 Guruh himoya holati"),
    BotCommand(command="gclean",  description="🧹 So'nggi xabarlarni tozalash"),
]


# ─── Yordamchi funksiyalar ────────────────────────────────────────────────────

async def set_role_commands(bot: Bot, chat_id: int, role: AdminRole) -> None:
    """Foydalanuvchiga rolga mos komandalar menyusini o'rnatadi (private chat)."""
    cmds = _COMMANDS.get(role, [])
    try:
        await bot.set_my_commands(cmds, scope=BotCommandScopeChat(chat_id=chat_id))
    except TelegramAPIError:
        pass


async def set_group_admin_commands(bot: Bot) -> None:
    """
    Barcha guruh adminlariga ko'rinadigan komandalar menyusini o'rnatadi.
    BotCommandScopeAllChatAdministrators — guruhda /komanda bosganda
    faqat admin foydalanuvchilarga ko'rinadi.
    """
    try:
        await bot.set_my_commands(
            _GROUP_ADMIN_COMMANDS,
            scope=BotCommandScopeAllChatAdministrators(),
        )
        logger.info("Guruh admin komandalar menyusi o'rnatildi.")
    except TelegramAPIError as exc:
        logger.warning(f"Guruh admin komandalar o'rnatilmadi: {exc}")


async def notify_super_admins(bot: Bot, text: str, exclude: int | None = None) -> None:
    """Barcha Super Adminlarga (config + DB) xabar yuboradi."""
    sent: set[int] = set()
    for uid in settings.super_admins:
        if uid == exclude:
            continue
        try:
            await bot.send_message(uid, text)
            sent.add(uid)
        except TelegramAPIError:
            pass
    try:
        async with get_session() as s:
            rows = (await s.execute(
                select(Admin).where(Admin.role == AdminRole.SUPER_ADMIN)
            )).scalars().all()
        for a in rows:
            if a.telegram_id in sent or a.telegram_id == exclude:
                continue
            try:
                await bot.send_message(a.telegram_id, text)
            except TelegramAPIError:
                pass
    except Exception:
        pass


def _resolve_user_from_message(msg: Message) -> tuple[int | None, str | None, str | None]:
    """
    Forward yoki kontaktdan telegram_id, first_name, username ajratib oladi.
    Qaytadi: (user_id, first_name, username)  —  user_id None bo'lsa topilmadi.
    """
    # 1. Kontakt
    if msg.contact:
        c = msg.contact
        return c.user_id, c.first_name, None

    # 2. forward_origin (aiogram 3.x yangi API)
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        user = getattr(origin, "sender_user", None)
        if user:
            return user.id, user.first_name, user.username

    # 3. Eski forward_from (backward compat)
    if msg.forward_from:
        u = msg.forward_from
        return u.id, u.first_name, u.username

    return None, None, None


def _resolve_channel_from_message(msg: Message) -> tuple[int | None, str | None, str | None]:
    """
    Forward qilingan kanal postidan chat_id, title, username ajratadi.
    Qaytadi: (chat_id, title, username)
    """
    # forward_origin.MessageOriginChannel
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None:
            return chat.id, getattr(chat, "title", None), getattr(chat, "username", None)

    # Eski forward_from_chat
    if msg.forward_from_chat:
        c = msg.forward_from_chat
        return c.id, getattr(c, "title", None), getattr(c, "username", None)

    return None, None, None


def _resolve_group_from_message(msg: Message) -> tuple[int | None, str | None, str | None]:
    """
    Guruh xabarini forward qilinganda chat_id, title, username ajratadi.
    Qaytadi: (chat_id, title, username)
    """
    # Agar guruh xabari forward qilingan bo'lsa — forward_from_chat ham ishlaydi
    origin = getattr(msg, "forward_origin", None)
    if origin is not None:
        chat = getattr(origin, "chat", None)
        if chat is not None and getattr(chat, "type", "") in ("group", "supergroup"):
            return chat.id, getattr(chat, "title", None), getattr(chat, "username", None)

    if msg.forward_from_chat and msg.forward_from_chat.type in ("group", "supergroup"):
        c = msg.forward_from_chat
        return c.id, getattr(c, "title", None), getattr(c, "username", None)

    # Guruhning o'zidan yuborilgan xabar (bot guruhda admin)
    if msg.chat.type in ("group", "supergroup"):
        return msg.chat.id, msg.chat.title, getattr(msg.chat, "username", None)

    return None, None, None


# ═══════════════════════════════════════════════════════════════════════════════
#  1. /add_admin  FSM oqimi
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("add_admin"))
async def cmd_add_admin(message: Message, state: FSMContext) -> None:
    """Faqat Super Admin chaqira oladi. FSM oqimini boshlaydi."""
    if await get_admin_role(message.from_user.id) != AdminRole.SUPER_ADMIN:
        return

    await state.set_state(AddAdminFSM.waiting_for_user)
    await message.answer(
        "👤 <b>Yangi admin qo'shish</b>\n\n"
        "Quyidagilardan birini bajaring:\n"
        "• Adminlamoqchi bo'lgan odamning <b>istalgan xabarini forward qiling</b>\n"
        "• Yoki uning <b>telefon kontaktini yuboring</b> (📎 → Kontakt)\n\n"
        "<i>Bekor qilish: /cancel</i>",
        parse_mode="HTML",
    )


@router.message(AddAdminFSM.waiting_for_user)
async def fsm_admin_got_user(message: Message, state: FSMContext) -> None:
    """Forward yoki kontakt keldi — foydalanuvchini aniqlaydi."""
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("➖ Bekor qilindi.")
        return

    user_id, first_name, username = _resolve_user_from_message(message)

    if not user_id:
        await message.answer(
            "⚠️ Foydalanuvchi aniqlanmadi.\n\n"
            "Iltimos, uning <b>xabarini forward qiling</b> yoki "
            "<b>kontaktini yuboring</b>.\n\n"
            "Ba'zi foydalanuvchilar forwardni bloklagan bo'lishi mumkin — "
            "bunday holda kontakt usulini ishlating.",
        )
        return

    display = first_name or username or str(user_id)
    await state.update_data(
        target_id=user_id,
        target_name=display,
        target_username=username,
    )
    await state.set_state(AddAdminFSM.waiting_for_role)

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👑 Super Admin", callback_data="admin_role:super_admin")],
        [InlineKeyboardButton(text="🛡 Moderator",   callback_data="admin_role:moderator")],
        [InlineKeyboardButton(text="👁 Viewer",       callback_data="admin_role:viewer")],
        [InlineKeyboardButton(text="❌ Bekor qilish", callback_data="admin_role:cancel")],
    ])
    await message.answer(
        f"✅ <b>{display}</b> topildi (ID: <code>{user_id}</code>)\n\n"
        "Qaysi rolni berasiz?",
        reply_markup=kb,
    )


@router.callback_query(F.data.startswith("admin_role:") | F.data.startswith("role:"))
async def fsm_admin_choose_role(cb: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Rol tanlandi — DB ga yozadi, bildirishnoma yuboradi."""
    await cb.answer("⏳ Bajarilmoqda...")

    # Ikkala prefiks bilan ham ishlaydi (eski/yangi tugmalar)
    raw = cb.data
    if raw.startswith("admin_role:"):
        choice = raw.split(":", 1)[1]
    else:
        choice = raw.split(":", 1)[1]

    if choice == "cancel":
        await state.clear()
        try:
            await cb.message.edit_text("➖ Admin qo'shish bekor qilindi.")
        except Exception:
            pass
        return

    # FSM data dan target ma'lumotlarini olish
    data = await state.get_data()
    target_id = data.get("target_id")

    # Agar state yo'qolgan bo'lsa (bot restart yoki timeout)
    if not target_id:
        try:
            await cb.message.edit_text(
                "⚠️ Sessiya muddati tugadi.\n\n"
                "Qaytadan /add_admin bosing va xabarni forward qiling."
            )
        except Exception:
            pass
        await state.clear()
        return

    display: str = data.get("target_name", str(target_id))

    # Rol validatsiyasi
    try:
        role = AdminRole(choice)
    except ValueError:
        try:
            await cb.message.edit_text(f"❌ Noto'g'ri rol: {choice}")
        except Exception:
            pass
        return

    try:
        async with get_session() as s:
            existing = (await s.execute(
                select(Admin).where(Admin.telegram_id == target_id)
            )).scalar_one_or_none()
            if existing:
                old_role = existing.role
                existing.role     = role
                existing.added_by = cb.from_user.id
            else:
                old_role = None
                s.add(Admin(
                    telegram_id=target_id,
                    username=data.get("target_username"),
                    full_name=display,
                    role=role,
                    added_by=cb.from_user.id,
                ))
            s.add(AuditLog(
                user_id=target_id,
                action=ActionType.ADMIN_ADDED,
                reason=f"Rol: {role.value} | Kim tomonidan: {cb.from_user.id}",
            ))
    except Exception as exc:
        await cb.message.answer(f"❌ DB xato: <code>{exc}</code>")
        await state.clear()
        return

    await state.clear()

    # Yangi adminga shaxsiy xabar + komandalar
    action_word = "yangilandi" if old_role else "tayinlandi"
    try:
        await bot.send_message(
            target_id,
            f"🎉 Siz <b>GuardBot</b> administratori etib {action_word}!\n\n"
            f"Rolingiz: <b>{role.value}</b>\n\n"
            "Barcha imkoniyatlarni ko'rish uchun /start bosing.",
        )
        await set_role_commands(bot, target_id, role)
    except (TelegramForbiddenError, TelegramAPIError):
        await cb.message.answer(
            "⚠️ <b>Diqqat:</b> Bu foydalanuvchi botga hali /start bermagan.\n"
            "DB ga yozildi, lekin unga xabar yuborib bo'lmadi.\n"
            "Avval unga botni ulashing: uning chatiga botni link qiling.",
        )

    # Super Adminlarga bildirishnoma
    by_name = cb.from_user.username or cb.from_user.first_name or str(cb.from_user.id)
    await notify_super_admins(
        bot,
        f"👤 <b>Yangi admin {action_word}</b>\n\n"
        f"Kim: <b>{display}</b> (<code>{target_id}</code>)\n"
        f"Rol: <b>{role.value}</b>\n"
        f"Kim tomonidan: @{by_name}",
        exclude=cb.from_user.id,
    )

    role_icons = {
        AdminRole.SUPER_ADMIN: "👑",
        AdminRole.MODERATOR:   "🛡",
        AdminRole.VIEWER:      "👁",
    }
    await cb.message.edit_text(
        f"✅ <b>{display}</b> {action_word}!\n\n"
        f"{role_icons.get(role, '')} Rol: <b>{role.value}</b>\n"
        f"ID: <code>{target_id}</code>",
    )
    logger.info(f"Admin {action_word}: {target_id} role={role.value} by={cb.from_user.id}")


# ═══════════════════════════════════════════════════════════════════════════════
#  2. /add_channel  FSM oqimi
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("add_channel"))
@router.message(Command("protect_channel"))
async def cmd_add_channel(message: Message, state: FSMContext) -> None:
    """Super Admin yoki Moderator chaqira oladi."""
    role = await get_admin_role(message.from_user.id)
    if role not in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
        return

    await state.set_state(AddChannelFSM.waiting_for_post)
    await message.answer(
        "📢 <b>Kanal himoyaga olish</b>\n\n"
        "<b>Shartlar:</b>\n"
        "• Bot shu kanalga allaqachon <b>ADMIN</b> qilib qo'shilgan bo'lishi kerak\n"
        "• Bot uchun <b>xabarlarni o'chirish</b> huquqi berilgan bo'lishi shart\n\n"
        "Endi shu <b>kanaldan istalgan bitta postni forward qiling</b> 👇\n\n"
        "<i>Bekor qilish: /cancel</i>",
    )


@router.message(AddChannelFSM.waiting_for_post)
async def fsm_channel_got_post(message: Message, state: FSMContext, bot: Bot) -> None:
    """Forward qilingan kanal postidan chat_id oladi, bot admin ekanligini tekshiradi."""
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("➖ Bekor qilindi.")
        return

    channel_id, title, username = _resolve_channel_from_message(message)

    if not channel_id:
        await message.answer(
            "⚠️ Kanal aniqlanmadi.\n\n"
            "Iltimos, shu <b>kanaldan biror postni forward qiling</b>.\n"
            "Guruh xabari emas — aynan <b>kanal</b> postini forward qiling.",
        )
        return

    # Bot kanalda admin ekanligini tekshirish
    try:
        member = await bot.get_chat_member(chat_id=channel_id, user_id=(await bot.get_me()).id)
        is_admin = member.status in ("administrator", "creator")
    except TelegramAPIError:
        is_admin = False

    if not is_admin:
        await message.answer(
            f"❌ <b>Bot «{title or channel_id}» kanalida admin emas!</b>\n\n"
            "Avval botni kanalga admin qilib qo'shing:\n"
            "1. Kanal sozlamalariga kiring\n"
            "2. Adminlar → Admin qo'shish → botni toping\n"
            "3. <b>Xabarlar o'chirish</b> huquqini bering\n\n"
            "Shundan so'ng qayta urinib ko'ring: /add_channel",
        )
        await state.clear()
        return

    # DB ga yozish
    try:
        async with get_session() as s:
            existing = (await s.execute(
                select(Channel).where(Channel.chat_id == channel_id)
            )).scalar_one_or_none()

            if existing:
                existing.is_active = True
                existing.title     = title or existing.title
                existing.username  = username or existing.username
                existing.added_by  = message.from_user.id
                status_text = "yangilandi va qayta faollashtirildi"
            else:
                s.add(Channel(
                    chat_id=channel_id,
                    title=title,
                    username=username,
                    is_active=True,
                    added_by=message.from_user.id,
                ))
                status_text = "himoyaga olindi"

            s.add(AuditLog(
                user_id=message.from_user.id,
                chat_id=channel_id,
                action=ActionType.CHANNEL_ADDED,
                reason=f"Kanal {status_text}: {title}",
            ))
    except Exception as exc:
        await message.answer(f"❌ DB xato: <code>{exc}</code>")
        await state.clear()
        return

    await state.clear()

    channel_link = f"@{username}" if username else f"<code>{channel_id}</code>"
    await message.answer(
        f"✅ <b>«{title or channel_id}»</b> {status_text}!\n\n"
        f"📢 Kanal: {channel_link}\n"
        f"🆔 ID: <code>{channel_id}</code>\n\n"
        "<b>Endi nima bo'ladi:</b>\n"
        "├ Kanalga kelgan <b>har bir post</b> avtomatik saqlanadi\n"
        "├ Bot a'zo bo'lgan <b>barcha guruhlarda</b> shu kontent forward/nusxa qilinsa — <b>darhol ban</b>\n"
        "└ Screenshot yoki qayta yozilgan matn ham aniqlanadi (OCR + hash)",
    )

    # Super Adminlarga bildirishnoma
    by_name = message.from_user.username or message.from_user.first_name or str(message.from_user.id)
    await notify_super_admins(
        bot,
        f"📢 <b>Yangi kanal himoyaga olindi</b>\n\n"
        f"Kanal: <b>{title or channel_id}</b> (<code>{channel_id}</code>)\n"
        f"Kim qo'shdi: @{by_name}",
        exclude=message.from_user.id,
    )
    logger.info(f"Kanal {status_text}: {channel_id} ({title}) by={message.from_user.id}")


# ═══════════════════════════════════════════════════════════════════════════════
#  3. /add_group  FSM oqimi
# ═══════════════════════════════════════════════════════════════════════════════

@router.message(Command("add_group"))
async def cmd_add_group(message: Message, state: FSMContext) -> None:
    """Super Admin yoki Moderator chaqira oladi."""
    role = await get_admin_role(message.from_user.id)
    if role not in (AdminRole.SUPER_ADMIN, AdminRole.MODERATOR):
        return

    await state.set_state(AddGroupFSM.waiting_for_msg)
    await message.answer(
        "👥 <b>Guruh himoyaga olish</b>\n\n"
        "<b>Shartlar:</b>\n"
        "• Bot shu guruhga allaqachon <b>ADMIN</b> qilib qo'shilgan bo'lishi kerak\n"
        "• Bot uchun <b>xabarlarni o'chirish</b> va <b>foydalanuvchilarni ban qilish</b> huquqi kerak\n\n"
        "Quyidagilardan birini bajaring:\n"
        "1. Shu <b>guruhdan istalgan xabarni forward qiling</b> 👇\n"
        "2. Yoki /add_group ni to'g'ridan-to'g'ri <b>guruh ichida</b> yozing\n\n"
        "<i>Bekor qilish: /cancel</i>",
    )


@router.message(AddGroupFSM.waiting_for_msg)
async def fsm_group_got_msg(message: Message, state: FSMContext, bot: Bot) -> None:
    """Guruh xabaridan yoki guruhning o'zidan chat_id oladi, bot admin ekanligini tekshiradi."""
    if message.text and message.text.startswith("/cancel"):
        await state.clear()
        await message.answer("➖ Bekor qilindi.")
        return

    group_id, title, username = _resolve_group_from_message(message)

    if not group_id:
        await message.answer(
            "⚠️ Guruh aniqlanmadi.\n\n"
            "Iltimos, <b>guruhdan xabarni forward qiling</b> yoki "
            "/add_group komandasini <b>guruhning o'zida</b> yozing.",
        )
        return

    # Bot guruhda admin ekanligini tekshirish
    bot_me = await bot.get_me()
    try:
        member = await bot.get_chat_member(chat_id=group_id, user_id=bot_me.id)
        is_admin = member.status in ("administrator", "creator")
        can_delete = getattr(getattr(member, "can_delete_messages", None), "__bool__", lambda: is_admin)()
        can_ban    = getattr(getattr(member, "can_restrict_members", None), "__bool__", lambda: is_admin)()
    except TelegramAPIError:
        is_admin = False
        can_delete = False
        can_ban    = False

    if not is_admin:
        await message.answer(
            f"❌ <b>Bot «{title or group_id}» guruhida admin emas!</b>\n\n"
            "Avval botni guruhga admin qilib qo'shing:\n"
            "1. Guruh sozlamalariga kiring\n"
            "2. Adminlar → Admin qo'shish → botni toping\n"
            "3. <b>Xabar o'chirish</b> va <b>A'zolarni cheklash</b> huquqlarini bering\n\n"
            "Shundan so'ng qayta urinib ko'ring: /add_group",
        )
        await state.clear()
        return

    # Huquqlar haqida ogohlantirish (admin, lekin to'liq huquqsiz)
    warnings: list[str] = []
    if not can_delete:
        warnings.append("⚠️ Xabarlarni o'chirish huquqi yo'q — leak xabarlar o'chirilmaydi")
    if not can_ban:
        warnings.append("⚠️ Ban qilish huquqi yo'q — foydalanuvchilar banlanmaydi")

    # DB ga yozish
    try:
        async with get_session() as s:
            existing = (await s.execute(
                select(ProtectedGroup).where(ProtectedGroup.chat_id == group_id)
            )).scalar_one_or_none()

            if existing:
                existing.is_active     = True
                existing.title         = title or existing.title
                existing.username      = username or existing.username
                existing.added_by      = message.from_user.id
                existing.bot_is_admin  = True
                status_text = "yangilandi va qayta faollashtirildi"
            else:
                s.add(ProtectedGroup(
                    chat_id=group_id,
                    title=title,
                    username=username,
                    is_active=True,
                    added_by=message.from_user.id,
                    bot_is_admin=True,
                ))
                status_text = "himoyaga olindi"

            s.add(AuditLog(
                user_id=message.from_user.id,
                chat_id=group_id,
                action=ActionType.GROUP_ADDED,
                reason=f"Guruh {status_text}: {title}",
            ))
    except Exception as exc:
        await message.answer(f"❌ DB xato: <code>{exc}</code>")
        await state.clear()
        return

    await state.clear()

    group_link = f"@{username}" if username else f"<code>{group_id}</code>"
    warn_text  = ("\n\n" + "\n".join(warnings)) if warnings else ""

    await message.answer(
        f"✅ <b>«{title or group_id}»</b> {status_text}!\n\n"
        f"👥 Guruh: {group_link}\n"
        f"🆔 ID: <code>{group_id}</code>\n\n"
        "<b>Endi nima bo'ladi:</b>\n"
        "├ Guruhda <b>reklama/spam</b> xabar topilsa — darhol ban\n"
        "├ Himoyalangan kanal kontenti screenshot/nusxa bo'lib kelsa — ban\n"
        "├ Bot relay orqali kelgan spam/kontent ham — ban\n"
        "├ Banlangan user qaytib kirsa — darhol qayta ban\n"
        "└ Har bir harakat audit logga yoziladi" + warn_text,
    )

    # Super Adminlarga bildirishnoma
    by_name = message.from_user.username or message.from_user.first_name or str(message.from_user.id)
    await notify_super_admins(
        bot,
        f"👥 <b>Yangi guruh himoyaga olindi</b>\n\n"
        f"Guruh: <b>{title or group_id}</b> (<code>{group_id}</code>)\n"
        f"Kim qo'shdi: @{by_name}",
        exclude=message.from_user.id,
    )
    logger.info(f"Guruh {status_text}: {group_id} ({title}) by={message.from_user.id}")


# ─── /cancel — istalgan FSM dan chiqish ──────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    current = await state.get_state()
    if current is None:
        await message.answer("ℹ️ Faol jarayon yo'q.")
        return
    await state.clear()
    await message.answer("➖ Jarayon bekor qilindi.")
