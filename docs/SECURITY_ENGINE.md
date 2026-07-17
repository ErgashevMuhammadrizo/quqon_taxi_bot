# GuardBot — Security Engine (v3)

Ushbu hujjat "GuardBot Security Update (v3)" texnik topshirig'i asosida
qo'shilgan **Professional Telegram Security System** qatlamini tavsiflaydi.

## Modul tuzilishi

```
security/
    engine.py             # SecurityEngine — barcha modullarni bog'lovchi facade
    trust_score.py         # Trust Score (0-100), o'zgarish tarixi bilan
    risk_analyzer.py       # Har action uchun 0-100 risk + AI-ready plugin arxitektura
    raid_detector.py       # Anti-Raid Engine (5s / 10+ join -> Raid Mode)
    captcha.py              # Button / Emoji / Math / Sequence captcha, 60s timeout
    suspicious_monitor.py   # Xulq-atvor anomaliyalari (flood, duplicate, silent watcher...)
    audit.py                # security_logs jurnali + har-user audit tarixi
    watermark.py            # Kelajakdagi "secret content" himoyasi uchun API (hozircha stub)
    dashboard.py            # /statistics uchun agregatsiya
    config_schema.py        # Har guruh uchun sozlanadigan GroupSecurityConfig
```

## Integratsiya nuqtalari

- **`handlers/security_events.py`** — yangi a'zo qo'shilishi (`new_chat_members`),
  captcha javob callback'lari (`sec_captcha:*`) va `/raid_off` komandasi.
- **`handlers/group_events.py`** — mavjud leak/spam-himoya handlerlari
  (`on_group_text`, `on_group_media`) ichiga `_run_security_risk_check()`
  qo'shildi. **Muhim:** bu ataylab alohida router emas — aiogram'da bitta
  update'ni birinchi mos handler "yutib oladi", shu sabab kontent-sizish
  tekshiruvi va xulq-atvor riski BITTA handler ichida ketma-ket ishlaydi.
- **`handlers/admin.py`** — `/statistics` (Security Dashboard) va
  `/security_settings <chat_id>` (guruh sozlamalari, inline tugmalar bilan).
- **`bot.py`** — `security_events.router` ro'yxatdan o'tkazildi; captcha
  timeout'lari uchun fon vazifasi (`_run_captcha_expiry_worker`) har 10
  soniyada muddati o'tgan sessiyalarni tekshiradi va kick qiladi.

## Ma'lumotlar bazasi

Yangi jadvallar: `risk_history`, `security_logs`, `captcha_sessions`,
`trust_scores`, `raid_logs`. Mavjud `users` va `protected_groups`
jadvallariga yangi ustunlar qo'shildi (Trust Score, warnings, mute/ban
count, per-guruh sozlamalar va h.k.).

Migratsiya ikki yo'l bilan qo'llab-quvvatlanadi:
1. **Dev:** `database/db.py::init_models()` — `CREATE TABLE IF NOT EXISTS`
   + `ALTER TABLE ADD COLUMN IF NOT EXISTS` (bot ishga tushganda avtomatik).
2. **Production:** `alembic upgrade head` —
   `alembic/versions/2024_01_04_0004_v4_security_engine.py`.

## Chegaralar (spetsifikatsiya bo'yicha)

| Risk Score | Harakat              |
|-----------:|-----------------------|
| 70+        | Admin Alert            |
| 90+        | Temporary Restrict     |
| 100+       | Auto Ban               |

| Sozlama                | Default |
|-------------------------|---------|
| Raid: N sekund           | 5       |
| Raid: M+ join             | 10      |
| Captcha timeout          | 60s     |
| Captcha max urinish       | 3       |

Barchasi `config.py`da (`SECURITY_*`, `RAID_*`, `CAPTCHA_*`,
`SUSPICIOUS_*`, `TRUST_*`) sozlanadi; har guruh uchun ON/OFF darajasidagi
qism (`raid_protection`, `captcha`, `forward_block`, `link_block`,
`media_block`, `ai_detection`, `risk_threshold`, `trust_threshold`) esa
`/security_settings` orqali DB'da (`ProtectedGroup`) o'zgartiriladi.

## AI Ready (11-band)

`RiskAnalyzer` "rule-based" signal provider'lar bilan ishlaydi
(`SignalProvider` abstract sinf). Kelajakda AI Risk Engine / LLM
Detection / Behavior Detection qo'shish uchun yangi provider yozib,
`risk_analyzer.register_signal_provider(provider, weight)` chaqirish
kifoya — asosiy `analyze()` logikasi o'zgarmaydi. Bitta signal xato
bersa ham (`try/except` bilan izolatsiya), qolgan signal'lar ishlab
turadi — bu production barqarorligi uchun muhim.

## Ma'lum cheklovlar / keyingi qadamlar

- `edit` va `delete` action'lari uchun Telegram Bot API "delete" hodisasi
  yubormaydi (faqat kanal/guruh o'zgarishlarini kuzatish orqali bilvosita
  aniqlanadi) — `RiskAnalyzer` bu action turlarini qabul qiladi, lekin
  hozircha handler ulanmagan (kelajakda `edited_message`/admin log orqali).
- `watermark.py` — faqat API; hali hech qanday handler uni chaqirmaydi
  (topshiriqda ham "hozircha API yozilsin" deyilgan).
- Yangi userlar uchun "account yaratilgan sana" Telegram Bot API orqali
  to'g'ridan-to'g'ri berilmaydi — `account_age_days` hisoblash hozircha
  chaqiruvchi tomonidan ixtiyoriy parametr sifatida uzatiladi (masalan,
  kelajakda taxminiy baholovchi orqali to'ldirilishi mumkin).
