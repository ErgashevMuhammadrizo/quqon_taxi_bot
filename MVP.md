# GuardBot MVP v2 — Deploy va Foydalanish Qo'llanmasi

> **Versiya:** 2.0.0 | **Sana:** 2026-07-07  
> **Maqsad:** Telegram guruh va kanallarni spam, reklama va kontent sızishidan himoya qiluvchi bot

---

## Mundarija

1. [Tizim talablari](#1-tizim-talablari)
2. [Boshlash — birinchi marta](#2-boshlash--birinchi-marta)
3. [Docker orqali deploy](#3-docker-orqali-deploy)
4. [Mahalliy (local) ishga tushirish](#4-mahalliy-local-ishga-tushirish)
5. [Bot sozlash — admin tomonidan](#5-bot-sozlash--admin-tomonidan)
6. [Guruh qo'shish](#6-guruh-qoshish)
7. [Kanal qo'shish](#7-kanal-qoshish)
8. [Admin qo'shish](#8-admin-qoshish)
9. [Komandalar ro'yxati](#9-komandalar-royxati)
10. [Himoya qoidalari](#10-himoya-qoidalari)
11. [Sozlamalar (.env)](#11-sozlamalar-env)
12. [Monitoring va loglar](#12-monitoring-va-loglar)
13. [Muammolarni bartaraf etish](#13-muammolarni-bartaraf-etish)

---

## 1. Tizim talablari

| Komponent | Minimum | Tavsiya |
|-----------|---------|---------|
| OS | Ubuntu 20.04+ / Debian 11+ | Ubuntu 22.04 LTS |
| CPU | 1 core | 2 core |
| RAM | 512 MB | 1 GB |
| Disk | 5 GB | 20 GB |
| Docker | 24+ | latest |
| Docker Compose | v2.0+ | latest |

> OCR (rasm ichidagi matnni o'qish) kerak bo'lmasa — RAM 256 MB yetadi.

---

## 2. Boshlash — birinchi marta

### Qadam 1 — Bot yaratish

1. Telegram'da [@BotFather](https://t.me/BotFather) ga yozing
2. `/newbot` → nom va username bering
3. **BOT_TOKEN** ni saqlang (keyinchalik `.env` ga kiritiladi)
4. `/setprivacy` → botni toping → **Disable** qiling  
   *(guruh xabarlarini ko'rishi uchun)*

### Qadam 2 — Super Admin ID aniqlash

[@userinfobot](https://t.me/userinfobot) yoki [@getidsbot](https://t.me/getidsbot) ga `/start` yuboring — sizning Telegram ID'ingiz chiqadi.

### Qadam 3 — Kodni yuklab olish

```bash
git clone <repo_url> guardbot
cd guardbot
cp .env .env.backup   # ehtiyot nusxa
```

---

## 3. Docker orqali deploy

### .env faylini to'ldirish

```bash
nano .env
```

Eng muhim qiymatlar:

```env
BOT_TOKEN=<BotFather dan olgan token>
SUPER_ADMIN_IDS=<sizning Telegram ID>
POSTGRES_PASSWORD=<kuchli parol o'ylab toping>
```

> `.env` faylida `DATABASE_URL` va `REDIS_URL` ni **o'zgartirmang** —  
> `docker-compose.yml` ularni avtomatik to'g'ri sozlaydi.

### Ishga tushirish

```bash
# Qurilish va ishga tushirish
docker compose up -d

# Loglarni kuzatish
docker compose logs -f bot

# Holat tekshirish
docker compose ps
```

Muvaffaqiyatli start log:

```
GuardBot ishga tushmoqda (MVP v2)...
DB tayyor.
Komandalar o'rnatildi: 1 ta admin.
GuardBot ishga tushdi ✅
```

### To'xtatish va qayta tushirish

```bash
docker compose down          # to'xtatish (ma'lumotlar saqlanadi)
docker compose down -v       # to'xtatish + ma'lumotlarni o'chirish (!)
docker compose restart bot   # faqat botni qayta tushirish
```

### Yangilash

```bash
git pull
docker compose build --no-cache bot
docker compose up -d bot
```

---

## 4. Mahalliy (local) ishga tushirish

```bash
# Virtual muhit
python3 -m venv venv
source venv/bin/activate

# Kutubxonalar
pip install -r requirements.txt

# PostgreSQL va Redis ishga tushirish (alohida terminal yoki Docker)
docker compose up -d postgres redis

# Bot ishga tushirish
python3 bot.py
```

---

## 5. Bot sozlash — admin tomonidan

Bot birinchi marta ishga tushgach, Super Admin sifatida `/start` bosing.  
Quyidagi tartibda sozlang:

```
1. Guruh/kanalga botni admin qilib qo'shing
2. /add_group  — guruhni himoyaga olish
3. /add_channel — kanalni himoyaga olish
4. /add_admin  — boshqa adminlar qo'shish (kerak bo'lsa)
```

---

## 6. Guruh qo'shish

**Maqsad:** Guruhda spam, reklama va bot relaydan himoya.

### Qadamlar

1. Botni guruhga **admin** qilib qo'shing:
   - Guruh sozlamalari → Adminlar → Admin qo'shish → botni toping
   - Kerakli huquqlar: **Xabarlarni o'chirish** + **A'zolarni cheklash**

2. Bot bilan private chatda:
   ```
   /add_group
   ```

3. Bot so'raydi:
   > "Guruhdan istalgan xabarni forward qiling yoki /add_group ni guruh ichida yozing"

4. Guruhdan xabar forward qiling → bot tekshiradi → tasdiqlaydi

### Guruhda nima kuzatiladi

| Holat | Harakat |
|-------|---------|
| `@kanal_username` yoki `t.me/...` link | 🚫 Darhol BAN |
| Reklama so'zlari (sotamiz, chegirma...) | 🚫 BAN (≥80%) yoki ⚠️ Confirm |
| 2+ URL bitta xabarda | ⚠️ Admin Confirm |
| Rasm + URL caption | ⚠️ Admin Confirm |
| Boshqa bot xabar yuborsa | 🗑 Xabar o'chiriladi + admin xabar |
| Himoyalangan kontent (watermark) | 🚫 Darhol BAN |

### Guruhlar ro'yxati

```
/groups
```

---

## 7. Kanal qo'shish

**Maqsad:** Kanal postlari saqlanadi → o'sha postlar istalgan joyda topilsa BAN.

### Qadamlar

1. Botni kanalga **admin** qilib qo'shing:
   - Kanal sozlamalari → Adminlar → Admin qo'shish
   - Kerakli huquq: **Xabarlarni o'chirish**

2. Bot bilan private chatda:
   ```
   /add_channel
   ```

3. Bot so'raydi:
   > "Kanaldan biror postni forward qiling"

4. Kanal postini forward qiling → bot admin ekanligini tekshiradi → qo'shadi

### Kanalda nima bo'ladi

- Har yangi post: hash + watermark hisoblaniib **protected_posts** ga saqlanadi
- O'sha post boshqa joyda forward/nusxa qilinsa → BAN
- Bot kanaldan chiqarilsa → Super Adminlarga darhol ogohlantirish

### Kanallar ro'yxati

```
/channels
```

---

## 8. Admin qo'shish

**Faqat Super Admin** yangi admin qo'sha oladi.

```
/add_admin
```

Bot ko'rsatma beradi:
> "Adminlamoqchi bo'lgan odamning xabarini forward qiling yoki kontaktini yuboring"

Keyin rol tanlaysiz:

| Rol | Imkoniyatlar |
|-----|-------------|
| 👑 Super Admin | Hammasi: kanal/guruh/admin qo'shish, sozlamalar |
| 🛡 Moderator | Ban/unban, whitelist, kanal/guruh qo'shish, log eksport |
| 👁 Viewer | Faqat ko'rish: statistika, ban ro'yxati, tekshiruv tarixi |

---

## 9. Komandalar ro'yxati

### Barcha adminlar

| Komanda | Tavsif |
|---------|--------|
| `/start` | Bosh menyu (rolga mos tugmalar) |
| `/stats` | Statistika |
| `/banned` | Ban ro'yxati |
| `/scan_history` | Tekshiruv tarixi |

### Moderator va yuqori

| Komanda | Tavsif |
|---------|--------|
| `/unban <user_id> <chat_id>` | Blokdan chiqarish |
| `/whitelist` | Ko'rish |
| `/whitelist add <id> [izoh]` | Qo'shish |
| `/whitelist remove <id>` | O'chirish |
| `/export_logs [limit]` | JSON log eksport |
| `/add_channel` | Kanal himoyaga olish (FSM) |
| `/channels` | Kanallar ro'yxati |
| `/add_group` | Guruh himoyaga olish (FSM) |
| `/groups` | Guruhlar ro'yxati |

### Faqat Super Admin

| Komanda | Tavsif |
|---------|--------|
| `/add_admin` | Yangi admin qo'shish (FSM) |
| `/admins` | Adminlar ro'yxati |
| `/remove_admin <id>` | Adminni o'chirish |
| `/settings` | Joriy sozlamalar |

---

## 10. Himoya qoidalari

### Guruh xavfsizligi (spam/reklama)

```
Telegram link/mention → BAN (95% ishonchlilik)
Reklama so'zlari      → BAN (≥80%) yoki Admin Confirm (60-79%)
2+ URL                → Admin Confirm (70%)
Media + URL           → Admin Confirm (65%)
Bot relay             → Xabar o'chiriladi + admin xabar
Watermark topilsa     → BAN (95%)
```

### Kontent himoyasi (kanal postlari)

```
Forward + exact hash  → BAN (100%)
Forward + fuzzy text  → Risk scoring → BAN yoki Confirm
Screenshot (pHash)    → Background job → BAN/Confirm
Watermark             → BAN (95%)
```

### Whitelist

Whitelist'dagi foydalanuvchilar **hech qachon** ban qilinmaydi:

```
/whitelist add 123456789 Ishonchli xodim
```

---

## 11. Sozlamalar (.env)

Asosiy o'zgartirilishi kerak bo'lgan qiymatlar:

```env
# === MAJBURIY ===
BOT_TOKEN=          # BotFather tokeni
SUPER_ADMIN_IDS=    # Telegram ID, vergul bilan: 111,222

# === TAVSIYA ETILADI ===
POSTGRES_PASSWORD=  # Kuchli parol (Docker uchun)

# === IXTIYORIY SOZLASH ===
AUTO_BAN_RISK_THRESHOLD=80    # Kontent risk: avtoban foizi
ADMIN_CONFIRM_RISK_THRESHOLD=50

SPAM_AUTO_BAN_CONFIDENCE=0.80  # Spam: avtoban chegarasi (0.0-1.0)
SPAM_CONFIRM_CONFIDENCE=0.60   # Spam: confirm chegarasi

SPAM_TG_LINK_CONFIDENCE=0.95   # TG link ban ishonchliligi
SPAM_AD_KEYWORD_CONFIDENCE=0.85 # Reklama so'z ishonchliligi
SPAM_MULTI_URL_THRESHOLD=2     # Nechta URL'dan shubhali
```

---

## 12. Monitoring va loglar

### Loglar

```bash
# Docker loglar
docker compose logs -f bot

# Fayl loglari
tail -f logs/guardbot.log
```

### Prometheus metrics

Bot ishlayotganda `http://localhost:9090/metrics` da mavjud.

### Health check

```bash
curl http://localhost:9090/health
```

Javob:
```json
{
  "status": "ok",
  "database": "ok",
  "redis": "ok",
  "uptime_seconds": 3600
}
```

---

## 13. Muammolarni bartaraf etish

### Bot start bermayapti

```bash
docker compose logs bot | tail -50
```

Keng tarqalgan sabablar:

| Xato | Yechim |
|------|--------|
| `BOT_TOKEN` noto'g'ri | `.env` da tokenni tekshiring |
| DB ulanmayapti | `docker compose ps postgres` — healthy bo'lishi kerak |
| Port band | `WEBAPP_PORT` ni `.env` da o'zgartiring |

### DB xatosi (ustun topilmadi)

```bash
docker compose restart bot
```

Bot start bo'lganda `init_models()` yangi ustunlarni avtomatik qo'shadi.

### Redis ulanmayapti

Redis bo'lmasa ham bot ishlaydi — MemoryStorage ishlatiladi.  
FSM holatlari bot restarti bilan yo'qoladi, lekin bu critical emas.

### Guruhda bot spam aniqlamayapti

1. Bot guruhda **admin** ekanligini tekshiring: `/groups`
2. Guruh `/add_group` orqali qo'shilganligini tekshiring
3. Bot'ning "Xabarlarni o'chirish" huquqi borligini tekshiring

### Kanal postlari saqlanmayapti

1. Bot kanalda **admin** ekanligini tekshiring: `/channels`
2. Kanal `/add_channel` orqali qo'shilganligini tekshiring

### "Bot hali /start bermagan" xatosi

Admin qo'shganda bu xabar chiqsa — yangi adminga botni link qiling:
```
https://t.me/<bot_username>?start=1
```
U `/start` bosgach, komandalar menyusi avtomatik o'rnatiladi.

---

## Tezkor boshlash (cheatsheet)

```bash
# 1. O'rnatish
git clone <repo> guardbot && cd guardbot

# 2. Sozlash
nano .env   # BOT_TOKEN va SUPER_ADMIN_IDS ni to'ldiring

# 3. Ishga tushirish
docker compose up -d

# 4. Loglarni kuzatish
docker compose logs -f bot

# 5. Bot sozlash (Telegram'da)
# /add_group  → guruh qo'shish
# /add_channel → kanal qo'shish
```

---

*GuardBot MVP v2 — Professional guruh va kanal himoya tizimi*
