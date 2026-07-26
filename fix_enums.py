"""
Enum Repair Utility
====================
PostgreSQL enum turlarini Python enum'lari bilan moslashtiradi.

MUAMMO:
  Alembic migratsiyalari enum turlarini QIYMATLAR bilan yaratgan
  ('moderator', 'button'), lekin SQLAlchemy standart holatda enum
  NOMINI yuboradi ('MODERATOR', 'BUTTON') → INSERT xato beradi:

    invalid input value for enum adminrole: "MODERATOR"
    invalid input value for enum captchatype: "BUTTON"

YECHIM:
  1. models.py da `values_callable` qo'shildi (kod endi qiymat yuboradi)
  2. Bu skript DB dagi enum turlariga yetishmayotgan yorliqlarni qo'shadi
  3. Eski (katta harfli) yozuvlarni kichik harfli qiymatga o'tkazadi

ISHLATISH (serverda, bot to'xtatilgan holda):
    cd /root/bot
    python3 fix_enums.py
"""
from __future__ import annotations

import asyncio

from sqlalchemy import text

from database.db import engine
from database.models import (
    ActionType, AdminRole, CaptchaStatus, CaptchaType,
    SecurityActionType, SecurityDecision, SecurityEventType,
)

# enum_turi -> (Python enum klassi, [(jadval, ustun), ...])
ENUM_MAP: dict[str, tuple[type, list[tuple[str, str]]]] = {
    "adminrole":          (AdminRole,          [("admins", "role")]),
    "actiontype":         (ActionType,         [("audit_logs", "action")]),
    "securityactiontype": (SecurityActionType, [("risk_history", "action_type")]),
    "securitydecision":   (SecurityDecision,   [("risk_history", "decision")]),
    "securityeventtype":  (SecurityEventType,  [("security_logs", "event_type")]),
    "captchatype":        (CaptchaType,        [("captcha_sessions", "captcha_type")]),
    "captchastatus":      (CaptchaStatus,      [("captcha_sessions", "status")]),
}


async def _existing_labels(conn, type_name: str) -> set[str]:
    """DB dagi enum turining joriy yorliqlarini qaytaradi."""
    rows = await conn.execute(
        text(
            "SELECT e.enumlabel FROM pg_enum e "
            "JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = :tname"
        ),
        {"tname": type_name},
    )
    return {r[0] for r in rows}


async def _table_exists(conn, table: str) -> bool:
    row = await conn.execute(
        text("SELECT to_regclass(:t)"), {"t": f"public.{table}"}
    )
    return row.scalar() is not None


async def repair() -> None:
    added_total = 0
    fixed_rows_total = 0

    # ── 1-qadam: yetishmayotgan yorliqlarni qo'shish ──────────────────────────
    # ALTER TYPE ... ADD VALUE tranzaksiya ichida ishlamaydi → AUTOCOMMIT
    autocommit_engine = engine.execution_options(isolation_level="AUTOCOMMIT")

    async with autocommit_engine.connect() as conn:
        for type_name, (enum_cls, _) in ENUM_MAP.items():
            labels = await _existing_labels(conn, type_name)
            if not labels:
                print(f"  ⏭  {type_name}: DB da bu enum turi yo'q — o'tkazildi")
                continue

            missing = []
            for member in enum_cls:
                # Kod endi QIYMAT yuboradi — u albatta mavjud bo'lishi kerak
                if member.value not in labels:
                    missing.append(member.value)

            for label in missing:
                # DDL bind-parametr qabul qilmaydi → literal kerak.
                # Qiymatlar bizning Python enum'larimizdan (tashqi kiritish yo'q),
                # lekin xavfsizlik uchun apostrofni ekranlaymiz.
                safe = label.replace("'", "''")
                await conn.execute(
                    text(f"ALTER TYPE {type_name} ADD VALUE IF NOT EXISTS '{safe}'")
                )
                added_total += 1
                print(f"  ➕ {type_name}: '{label}' qo'shildi")

            if not missing:
                print(f"  ✅ {type_name}: barcha yorliqlar joyida ({len(labels)} ta)")

    # ── 2-qadam: eski katta harfli yozuvlarni qiymatga o'tkazish ──────────────
    async with engine.begin() as conn:
        for type_name, (enum_cls, targets) in ENUM_MAP.items():
            for table, column in targets:
                if not await _table_exists(conn, table):
                    continue
                for member in enum_cls:
                    if member.name == member.value:
                        continue  # o'zgartirish kerak emas
                    res = await conn.execute(
                        text(
                            f"UPDATE {table} SET {column} = :val::{type_name} "
                            f"WHERE {column}::text = :nm"
                        ),
                        {"val": member.value, "nm": member.name},
                    )
                    if res.rowcount:
                        fixed_rows_total += res.rowcount
                        print(
                            f"  🔄 {table}.{column}: {res.rowcount} yozuv "
                            f"'{member.name}' → '{member.value}'"
                        )

    print()
    print(f"Yakun: {added_total} yorliq qo'shildi, {fixed_rows_total} yozuv tuzatildi.")


async def verify() -> None:
    """Tuzatishdan keyin admins jadvalini tekshiradi."""
    async with engine.connect() as conn:
        if not await _table_exists(conn, "admins"):
            print("⚠️  admins jadvali topilmadi.")
            return
        rows = await conn.execute(
            text("SELECT telegram_id, full_name, role::text FROM admins ORDER BY id")
        )
        data = rows.all()
        if not data:
            print("ℹ️  admins jadvali bo'sh — /add_admin orqali admin qo'shing.")
            return
        print(f"👤 Adminlar ({len(data)} ta):")
        for tid, name, role in data:
            print(f"   • {tid} — {name or '—'} [{role}]")


async def main() -> None:
    print("═" * 60)
    print("  GuardBot — Enum Repair")
    print("═" * 60)
    print()
    await repair()
    print()
    await verify()
    print()
    await engine.dispose()
    print("✅ Tugadi. Endi botni qayta ishga tushiring.")


if __name__ == "__main__":
    asyncio.run(main())
