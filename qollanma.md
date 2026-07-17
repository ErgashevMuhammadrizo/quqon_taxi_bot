# GuardBot — To'liq Qo'llanma

> **GuardBot** — Telegram kanal va guruhlarini ruxsatsiz kontent tarqalishidan
> himoya qiluvchi avtomatik moderatsiya boti (MVP v2).

---

## Mundarija

1. [Bot nima qiladi](#1-bot-nima-qiladi)
2. [Texnik stek](#2-texnik-stek)
3. [O'rnatish va ishga tushirish](#3-ornatish-va-ishga-tushirish)
4. [Konfiguratsiya (.env)](#4-konfiguratsiya-env)
5. [Ma'lumotlar bazasi modellari](#5-malumotlar-bazasi-modellari)
6. [Admin rollari (RBAC)](#6-admin-rollari-rbac)
7. [Barcha komandalar](#7-barcha-komandalar)
8. [Bot qanday ishlaydi — to'liq oqim](#8-bot-qanday-ishlaydi--toliq-oqim)
9. [Spam va reklama aniqlash](#9-spam-va-reklama-aniqlash)
10. [Kontent himoyasi va watermark](#10-kontent-himoyasi-va-watermark)
11. [Risk skoring tizimi](#11-risk-skoring-tizimi)
12. [Fon ish jarayonlari (Background Jobs)](#12-fon-ish-jarayonlari-background-jobs)
13. [Monitoring va health check](#13-monitoring-va-health-check)
14. [Ma'lum muammolar va tuzatishlar](#14-malum-muammolar-va-tuzatishlar)
15. [Loyiha fayl tuzilmasi](#15-loyiha-fayl-tuzilmasi)

---

## 1. Bot nima qiladi

GuardBot uchta asosiy vazifani bajaradi:

**Kanal himoyasi**
- Bot admin sifatida qo'shilgan kanalga har bir yangi post kelganda uni darhol
  bazaga yozib qo'yadi: SHA-256 hash, ko'rinmas watermark tokeni, media
  uchun perceptual hash (pHash) va OCR matni.
- Kelajakda shu kontent boshqa joyda paydo bo'lsa, kimning ekanligini
  aniqlash va zudlik bilan ban qilish mumkin bo'ladi.

**Guruh himoyasi**
- Himoyalangan guruh (`/add_group` orqali qo'shilgan) da har bir xabarga
  quyidagi tekshiruvlar ketma-ket o'tkaziladi:
  1. Spam/reklama aniqlash (Telegram havolalar, kalit so'zlar, ko'p URL)
  2. Bot-relay tekshiruvi (`is_bot = True`)
  3. Watermark topilishi (kontent o'g'irlangan bo'lsa)
  4. Hash/fuzzy matn o'xshashlik solishtirishi
  5. Media uchun background pHash + OCR tahlil (arq queue)
- Natijaga qarab: **darhol ban**, **admin tasdiqi**, yoki **e'tiborsiz**.

**Admin panel**
- Rollarga asoslangan (RBAC) to'liq boshqaruv paneli.
- Statistika, ban/unban, whitelist, log eksport, sozlamalar.

---

## 2. Texnik stek

| Komponent        | Texnologiya                          |
|------------------|--------------------------------------|
| Bot framework    | aiogram 3.15                         |
| Dasturlash tili  | Python 3.11+                         |
| Ma'lumotlar bazasi | PostgreSQL 15+ (asyncpg drayver)   |
| Cache / Rate limit | Redis 7+ (redis.asyncio)           |
| ORM              | SQLAlchemy 2.0 (async)               |
| Migratsiyalar    | Alembic 1.14                         |
| Background jobs  | arq 0.26 (Redis-based queue)         |
| HTTP server      | aiohttp 3.10 (webhook + metrics)     |
| Monitoring       | Prometheus + /health endpoint        |
| Media tahlil     | Pillow, imagehash, pytesseract (OCR) |
| Logging          | colorlog + python-json-logger        |

---

## 3. O'rnatish va ishga tushirish

### Talablar

- Python 3.11 yoki undan yuqori
- PostgreSQL 15+
- Redis 7+
- (Ixtiyoriy) `tesseract-ocr` — OCR funksiyasi uchun

### Qadamlar

```bash
# 1. Loyihani klonlash
git clone <repo-url>
cd guardbot

# 2. Virtual muhit yaratish
python3 -m venv venv
source venv/bin/activate

# 3. Kutubxonalarni o'rnatish
pip install -r requirements.txt

# 4. OCR uchun tizim kutubxonasi (ixtiyoriy)
sudo apt install tesseract-ocr tesseract-ocr-uzb tesseract-ocr-rus

# 5. .env faylini sozlash
cp .env .env.local   # yoki to'g'ridan to'g'ri .env ni tahrirlang

# 6. Ma'lumotlar bazasini tayyorlash
alembic upgrade head

# 7. Botni ishga tushirish (polling rejimi)
python3 bot.py
```

### Docker Compose orqali

```bash
docker-compose up -d
```

`docker-compose.yml` PostgreSQL, Redis va bot konteynerlarini birga ishga tushiradi.

### Webhook rejimi

`.env` da quyidagilarni o'rnating:

```env
BOT_USE_WEBHOOK=true
WEBHOOK_URL=https://yourdomain.com
WEBHOOK_PATH=/webhook
WEBHOOK_SECRET=your-secret-token
WEBAPP_PORT=8080
```

Keyin: `python3 bot.py`

---

## 4. Konfiguratsiya (.env)

Barcha sozlamalar `.env` fayl orqali boshqariladi. `config.py` ularni
`pydantic-settings` yordamida yuklaydi.

### Majburiy sozlamalar

```env
BOT_TOKEN=<BotFather dan olingan token>
SUPER_ADMIN_IDS=123456789,987654321   # vergul bilan ajratilgan Telegram ID lar
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/guardbot
REDIS_URL=redis://localhost:6379/0
```

> Telegram ID ni bilish uchun: `@userinfobot` yoki `@getidsbot` ga `/start` yuboring.

### Himoya chegaralari

```env
# 0–100 oralig'ida risk ball bo'yicha harakat
AUTO_BAN_RISK_THRESHOLD=80        # 80+ ball → darhol ban
ADMIN_CONFIRM_RISK_THRESHOLD=50   # 50–79 → admin tasdiqi so'raladi

# Kontent solishtirish aniqligi (0.0–1.0)
HASH_SIMILARITY_THRESHOLD=0.92    # hash o'xshashligi chegarasi
OCR_SIMILARITY_THRESHOLD=0.85     # OCR matn o'xshashligi chegarasi
```

### Risk skoring vaznlari (jami 1.0 bo'lishi shart)

```env
WEIGHT_FORWARD_HASH=0.30    # hash mos kelishi — 30%
WEIGHT_OCR_SIMILARITY=0.25  # OCR matn o'xshashligi — 25%
WEIGHT_WATERMARK=0.20       # watermark topilishi — 20%
WEIGHT_BEHAVIOR=0.15        # forward tezligi — 15%
WEIGHT_ACCOUNT_AGE=0.10     # yangi akkount — 10%
```

### Spam aniqlash chegaralari

```env
SPAM_TG_LINK_CONFIDENCE=0.95      # Telegram havola → 95% ishonch → ban
SPAM_AD_KEYWORD_CONFIDENCE=0.85   # reklama kalit so'z → 85%
SPAM_MULTI_URL_THRESHOLD=2        # 2 va undan ko'p URL → shubhali
SPAM_AUTO_BAN_CONFIDENCE=0.80     # 80%+ → darhol ban
SPAM_CONFIRM_CONFIDENCE=0.60      # 60–79% → admin tasdiqi
```

### Rate limit

```env
RATE_LIMIT_FORWARDS=5             # 1 daqiqada max 5 ta forward
RATE_LIMIT_WINDOW_SECONDS=60
NEW_ACCOUNT_DAYS_SUSPICIOUS=3     # 3 kundan yangi akkount — shubhali
```

---

## 5. Ma'lumotlar bazasi modellari

Alembic migratsiyalari: `alembic/versions/` papkasida 3 ta fayl bor.

| Jadval               | Vazifasi                                              |
|----------------------|-------------------------------------------------------|
| `users`              | Kuzatilgan barcha foydalanuvchilar (ID, risk, ban)    |
| `admins`             | Bot administratorlari (RBAC rollari bilan)            |
| `channels`           | Himoyalangan kanallar                                 |
| `protected_groups`   | Bot admin bo'lgan guruhlar                            |
| `protected_posts`    | Original kontent fingerprinti (hash, pHash, OCR)      |
| `audit_logs`         | Barcha harakatlar tarixi (BAN, SCAN, UNBAN va h.k.)   |
| `banned_users`       | Hozirda bloklangan foydalanuvchilar                   |
| `whitelist`          | Ban dan ozod foydalanuvchilar                         |
| `monitored_channels` | Klon kuzatuv juftliklari (manba → shubhali)           |
| `clone_incidents`    | Aniqlangan klon hodisalari                            |

### Muhim maydonlar — `protected_posts`

- `source_chat_id` — kontent QAYERDAN kelganligi (kanal yoki guruh)
- `content_hash` — SHA-256 (exact match)
- `phash` — perceptual hash (vizual o'xshashlik)
- `ocr_text` — rasmdan chiqarilgan matn
- `watermark_token` — ko'rinmas watermark tokeni

### `audit_logs` harakat turlari

`SCAN`, `WARN`, `BAN`, `UNBAN`, `WHITELIST_ADD`, `WHITELIST_REMOVE`,
`SETTINGS_CHANGE`, `CLONE_DETECTED`, `GROUP_ADDED`, `CHANNEL_ADDED`, `ADMIN_ADDED`

---

## 6. Admin rollari (RBAC)

Uchta rol mavjud. Rol `middlewares/role_check.py` da tekshiriladi.

| Rol           | Belgisi | Imkoniyatlar                                              |
|---------------|---------|-----------------------------------------------------------|
| `super_admin` | 👑      | Barcha komandalar + admin qo'shish/o'chirish + sozlamalar |
| `moderator`   | 🛡      | Ban/unban, whitelist, kanal/guruh qo'shish, log eksport   |
| `viewer`      | 👁      | Faqat ko'rish: statistika, ban ro'yxati, tarix            |

**Super Admin** lar ikkita yo'l bilan aniqlanadi:
1. `.env` dagi `SUPER_ADMIN_IDS` — DB ga bormay ham ishlaydi
2. `admins` jadvali — `/add_admin` orqali qo'shilganlar

> Agar `.env` dagi super admin DB da bo'lmasa ham `SUPER_ADMIN` huquqi bor.

---

## 7. Barcha komandalar

### Umumiy (barcha adminlar)

| Komanda                    | Rol       | Ta'rifi                                         |
|----------------------------|-----------|-------------------------------------------------|
| `/start`                   | Viewer+   | Bosh menyu (rolga qarab dinamik tugmalar)        |
| `/help`                    | Viewer+   | Komandalar ro'yxati (rolga mos)                  |
| `/stats`                   | Viewer+   | Umumiy statistika                               |
| `/banned [sahifa]`         | Viewer+   | Bloklangan foydalanuvchilar ro'yxati (sahifali) |
| `/scan_history [limit]`    | Viewer+   | So'nggi tekshiruvlar tarixi                     |

### Moderator komandalar

| Komanda                          | Rol         | Ta'rifi                                     |
|----------------------------------|-------------|---------------------------------------------|
| `/unban <user_id> <chat_id>`     | Moderator+  | Foydalanuvchini blokdan chiqarish           |
| `/whitelist`                     | Moderator+  | Whitelist ro'yxatini ko'rish                |
| `/whitelist add <id> [izoh]`     | Moderator+  | Whitelist ga qo'shish                       |
| `/whitelist remove <id>`         | Moderator+  | Whitelist dan o'chirish                     |
| `/export_logs [limit]`           | Moderator+  | Audit logni JSON fayl sifatida yuklab olish |
| `/add_channel`                   | Moderator+  | Kanal himoyaga olish (FSM oqimi)            |
| `/protect_channel`               | Moderator+  | `/add_channel` bilan bir xil               |
| `/add_group`                     | Moderator+  | Guruh himoyaga olish (FSM oqimi)            |
| `/channels`                      | Moderator+  | Himoyalangan kanallar ro'yxati              |
| `/groups`                        | Moderator+  | Himoyalangan guruhlar ro'yxati              |

### Super Admin komandalar

| Komanda                     | Rol          | Ta'rifi                                      |
|-----------------------------|--------------|----------------------------------------------|
| `/add_admin`                | Super Admin  | Yangi admin qo'shish (FSM, forward/kontakt)  |
| `/admins`                   | Super Admin  | Barcha adminlar ro'yxati                     |
| `/remove_admin <user_id>`   | Super Admin  | Adminni o'chirish                            |
| `/settings`                 | Super Admin  | Joriy konfiguratsiya ko'rsatish              |

---

## 8. Bot qanday ishlaydi — to'liq oqim

### 8.1 Kanal posti kelganda (`channel_events.py`)

```
Kanal → yangi post
    │
    ├─ Bu kanal ro'yxatda (/add_channel orqali qo'shilganmi)?
    │       Yo'q → e'tiborsiz o'tamiz
    │
    └─ Ha:
         ├─ SHA-256 hash hisoblash
         ├─ Watermark tokeni yaratish (ko'rinmas zero-width belgilar)
         ├─ Media bo'lsa → background job queue'ga (pHash + OCR uchun)
         └─ Hammasi ProtectedPost jadvaliga yoziladi
```

### 8.2 Guruh xabari kelganda (`group_events.py`)

```
Guruh xabari
    │
    ├─ Bu guruh himoyalangan (ProtectedGroup jadvalida)?
    │       Yo'q → e'tiborsiz
    │
    ├─ Yuboruvchi bot ekanmi (is_bot=True)?
    │       Ha → xabarni o'chir + adminlarga ogohlantirish
    │
    ├─ SPAM tekshiruvi (spam_detector.py):
    │   ├─ Telegram havola/@mention → 95% ishonch → AUTO BAN
    │   ├─ Reklama kalit so'z → 55–90% → AUTO BAN yoki CONFIRM
    │   ├─ 2+ URL → 70% → ADMIN CONFIRM
    │   └─ Media + URL caption → 65% → ADMIN CONFIRM
    │
    ├─ WATERMARK tekshiruvi:
    │   └─ Zero-width belgilar topilsa → manba aniqlanadi → BAN
    │
    ├─ MATN HASH tekshiruvi:
    │   ├─ Exact SHA-256 mos kelsa → BAN
    │   └─ Fuzzy o'xshashlik ≥ 85% → risk scoring
    │
    ├─ MEDIA bo'lsa → background job queue'ga
    │
    └─ Risk score 0–100 hisoblanadi:
         ≥ 80 → AUTO BAN
         50–79 → ADMIN CONFIRM (tugmali xabar)
         25–49 → WARN (hozircha faqat log)
         < 25  → IGNORE
```

### 8.3 Admin tasdiqi (`confirm_ban` / `ignore_ban` callback)

Admin "Ban qilish" tugmasini bosganda:
1. `BanManager.execute_ban()` chaqiriladi
2. Whitelist tekshiruvi (agar whitelist da bo'lsa — ban bekor)
3. Telegram API: `ban_chat_member(revoke_messages=True)`
4. `AuditLog` + `BannedUser` jadvallarga yoziladi
5. Barcha adminlarga notification

---

## 9. Spam va reklama aniqlash

`core/spam_detector.py` quyidagi holatlarga e'tibor beradi:

### Telegram havolalar (`ad_link`)
Quyidagi pattern lar darhol 95% ishonch bilan ban sababi hisoblanadi:
- `t.me/kanal_nomi`
- `telegram.me/guruh`
- `@username` shaklidagi mention lar

### Ko'p URL (`multi_url`)
Bir xabarda 2 yoki undan ko'p URL (`http://`, `https://`, `www.`) — 70% ishonch.

### Reklama kalit so'zlari (`ad_keyword`)
O'zbek, rus va ingliz tillarida yuzdan ortiq kalit so'z ro'yxati:

**O'zbekcha:** `sotamiz`, `chegirma`, `aksiya`, `obuna bo'ling`, `kurs`, `daromad`...

**Ruscha:** `купить`, `скидка`, `подписывайтесь`, `заработать`, `реклама`...

**Inglizcha:** `buy now`, `subscribe`, `earn money`, `crypto`, `forex`, `referral link`...

Bir so'z topilsa 55%, har qo'shimcha so'z uchun +10% (max 90%).

### Media + URL kombinatsiyasi (`media_link`)
Rasm/video bilan birga caption da havola bo'lsa — 65% ishonch (admin confirm).

### Bot akkaunt (`bot_account`)
Foydalanuvchi nomi `bot` bilan tugasa yoki ko'p raqam bo'lsa — 60% ishonch.

---

## 10. Kontent himoyasi va watermark

### Watermark qanday ishlaydi

1. Kanal postiga `generate_watermark_token()` — tasodifiy 8 baytli hex token yaratiladi
2. `embed_watermark()` — token ikkilik songa aylantirilip, **Zero-Width belgilar**
   (`\u200b` = 0, `\u200c` = 1) sifatida matn oxiriga qo'shiladi
3. Kontent kimga yuborilib, o'sha joyda paydo bo'lganda `extract_watermark()`
   original tokenni topib oladi → qaysi post ekanligini aniqlaydi → **BAN**

> Watermark ko'rinmas — foydalanuvchi uni ko'ra olmaydi, lekin forward/copy-paste
> qilganda ham saqlanib qoladi.

### Hash solishtirish turlari

| Tur          | Funksiya               | Vaziyat                              |
|--------------|------------------------|--------------------------------------|
| Exact hash   | SHA-256 (matn/bayt)    | Aynan bir xil kontent                |
| pHash        | Perceptual hash        | Rasm siqilgan, watermark bosilgan    |
| OCR          | Tesseract + similarity | Skrinshotdan olingan matn            |
| Fuzzy text   | SequenceMatcher ratio  | Matn qisman o'zgartirilgan           |

### Rasm tahlili (ixtiyoriy kutubxonalar)

- `Pillow` + `imagehash` — pHash hisoblash
- `pytesseract` — rasmdan matn chiqarish

Agar o'rnatilmagan bo'lsa bot ishlashda davom etadi — faqat shu imkoniyatlar
ishlamaydi, qolgan barcha himoya ishlayveradi.

---

## 11. Risk skoring tizimi

`core/decision_matrix.py` beshta omilni vaznli qo'shib 0–100 ball hisoblaydi.

### Formula

```
risk_score = (
  hash_match_score    × 0.30  +   # forward/hash mos kelishi
  ocr_similarity      × 0.25  +   # OCR matn o'xshashligi
  watermark_verified  × 0.20  +   # watermark topilishi (0 yoki 1)
  behavior_score      × 0.15  +   # forward tezligi (sliding window)
  account_age_score   × 0.10      # yangi akkount bo'lsa yuqori
) × 100
```

### Harakat chegaralari

| Ball       | Harakat        | Nima sodir bo'ladi                              |
|------------|----------------|-------------------------------------------------|
| 80–100     | `AUTO_BAN`     | Darhol Telegram ban + xabarni o'chirish         |
| 50–79      | `ADMIN_CONFIRM`| Adminlarga tugmali xabar ("Ban / E'tiborsiz")   |
| 25–49      | `WARN`         | Faqat log yoziladi (hozircha harakat yo'q)      |
| 0–24       | `IGNORE`       | E'tiborsiz o'tiladi                             |

### Behavior Engine (`core/behavior_engine.py`)

Redis `sorted-set` asosida **sliding window** algoritmi:

- Foydalanuvchi har forward qilganda Redis'ga vaqt belgisi qo'shiladi
- 1 daqiqa ichida `RATE_LIMIT_FORWARDS` dan oshsa — yuqori behavior_score
- `score_account_age()` — `first_seen_at` ga qarab yangi akkount belgisi

---

## 12. Fon ish jarayonlari (Background Jobs)

`core/jobs.py` — `arq` (async Redis queue) asosida og'ir media tahlilni
Telegram event loop'dan ajratib bajaradi.

### Qanday ishlaydi

```
1. Handler (group_events / channel_events) → enqueue_*_media_analysis()
2. Redis queue'ga tushadi
3. arq worker → analyze_channel_media_job() yoki analyze_group_media_job()
4. Media yuklab olinadi, pHash + OCR hisoblanadi
5. Natija ProtectedPost jadvaliga yoziladi
6. Agar leak topilsa → BAN yoki ADMIN_CONFIRM
```

### arq worker ishga tushirish

```bash
arq core.jobs.WorkerSettings
```

### arq o'rnatilmagan holat

`arq` import qilib bo'lmasa — bot davom etadi, lekin media tahlil
background'da ishlamaydi. Xabar log'ga yoziladi:
> `arq o'rnatilmagan — background jobs o'chirilgan, sinxron rejimda ishlaydi.`

### Clone detector (`core/clone_detector.py`)

`MonitoredChannel` jadvalidagi juftliklar (manba → klon shubhali kanal) uchun
vaqti-vaqti bilan skanerlash logikasi tayyor — lekin `periodic_clone_scan()`
hali `bot.py` da ishga tushirilmagan (kelajak versiya uchun tayyorlangan).

---

## 13. Monitoring va health check

### Prometheus metrikalar

Bot `METRICS_PORT` (default: 9090) portida `/metrics` endpointini ochadi.

```bash
# Prometheus scrape config
- job_name: guardbot
  static_configs:
    - targets: ["localhost:9090"]
```

Asosiy metrikalar:

| Metrika                              | Tur       | Ma'nosi                           |
|--------------------------------------|-----------|-----------------------------------|
| `guardbot_messages_processed_total`  | Counter   | Qayta ishlangan xabarlar          |
| `guardbot_scans_total`               | Counter   | Tekshiruvlar (clean/warn/ban)     |
| `guardbot_bans_total`                | Counter   | Banlar (auto/admin/media_job)     |
| `guardbot_clone_incidents_total`     | Counter   | Klon hodisalari                   |
| `guardbot_risk_score`                | Histogram | Risk ball taqsimoti               |
| `guardbot_media_jobs_total`          | Counter   | Background job holati             |
| `guardbot_db_query_duration_seconds` | Histogram | DB so'rov vaqti                   |
| `guardbot_watermarks_detected_total` | Counter   | Watermark topilgan xabarlar       |
| `guardbot_uptime_seconds`            | Gauge     | Bot ishlagan vaqt                 |

`METRICS_ENABLED=false` qilib o'chirsa bo'ladi.

### Health check

```bash
curl http://localhost:9090/health
```

Javob (ishlayotganda):
```json
{
  "status": "ok",
  "timestamp": "2026-07-17T10:00:00Z",
  "uptime_seconds": 3600,
  "checks": {
    "database": "ok",
    "redis": "ok"
  },
  "version": "1.0.0"
}
```

DB yoki Redis ishlamasa `"status": "degraded"` va HTTP 503 qaytaradi.

### Logging

- Fayl: `logs/guardbot.log` (rotating — eski loglar siqiladi)
- Konsol: rangli chiqish (colorlog)
- `LOG_LEVEL=DEBUG` — batafsil debug log
- Produksiyada JSON structured logging (`python-json-logger`)

---

## 14. Ma'lum muammolar va tuzatishlar

### Tuzatilgan xatolar

#### `utils/metrics.py` — `Subscription` modeli yo'qligi
**Muammo:** `_refresh_subscription_gauges()` funksiyasi `models.py` da
mavjud bo'lmagan `Subscription`, `SubscriptionStatus`, `SubscriptionPlan`
modellarini import qilishga harakat qilardi — bu `/metrics` endpointini
har chaqirilganda exception bilan to'xtatardi.

**Tuzatish:** `ImportError` ni silent ushlab, model yo'q bo'lsa
funksiya shunchaki o'tib ketadigan qilib tuzatildi (`qollanma.md` yozilgan
sessiyada bajarildi).

---

### Hali ishlamagan / cheklangan qismlar

#### 1. Periodic clone scanner ishga tushmaydi
`core/clone_detector.py` da `periodic_clone_scan()` to'liq yozilgan,
lekin `bot.py` da `asyncio.create_task()` orqali ishga tushirilmagan.

**Yechim:** `bot.py` dagi `_run_polling()` funksiyasiga qo'shish:
```python
# bot.py — _run_polling() ichida tasks ro'yxatiga qo'shing
if settings.CLONE_SCAN_INTERVAL_SECONDS > 0:
    from core.clone_detector import periodic_clone_scan
    tasks.append(asyncio.create_task(
        periodic_clone_scan(bot, settings.CLONE_SCAN_INTERVAL_SECONDS),
        name="clone_scanner"
    ))
```

#### 2. `MonitoredChannel` qo'shish uchun handler yo'q
`monitored_channels` jadvali mavjud, lekin uni to'ldiruvchi
`/add_monitored_channel` komandasi hali yozilmagan.
Jadvalga to'g'ridan to'g'ri DB orqali yozish mumkin.

#### 3. Clone scanner real xabarlarni olmaydi
`_scan_single_monitor()` hozircha faqat `last_checked_at` ni yangilab
chiqadi — target kanaldan xabarlarni olish logikasi stub holida.
Bot target kanalda admin bo'lishi va u yerdan keladigan xabarlar
`group_events.py` orqali o'tishi kerak (bu qisman ishlaydi).

#### 4. Rate limit middleware xabar bermaydi
`middlewares/rate_limit.py` throttling xabarlari oddiy foydalanuvchiga
ko'rinmaydi — xabar shunchaki tashlanadi. Kerak bo'lsa handler'ga
"Iltimos kutib turing" xabari qo'shish mumkin.

#### 5. `arq` worker alohida ishga tushirilishi kerak
Bot boshlanganida arq worker **avtomatik** ishga tushmaydi.
Media background tahlil uchun alohida terminalda:
```bash
arq core.jobs.WorkerSettings
```
Bo'lmasa media tahlil shu sessiyada o'tkazib yuboriladi.

---

### Eslatmalar

- **Forward ban yo'q** — xabar forward qilinsa avtomatik ban **bo'lmaydi**.
  Faqat kontent bazaga mos kelsa yoki spam/reklama aniqlansa ban qilinadi.
- **Bot relay** — boshqa botlar xabar yuborsа, ularni ban qilib bo'lmaydi
  (Telegram API cheklovi), lekin xabar o'chiriladi va adminlarga ogohlantirish
  yuboriladi.
- **Himoyasiz guruhlar** — `/add_group` orqali qo'shilmagan guruhlarda
  bot hech narsa qilmaydi, xabarlarga e'tibor bermaydi.

---

## 15. Loyiha fayl tuzilmasi

```
guardbot/
│
├── bot.py                      # Entry point — dispatcher, startup/shutdown
├── config.py                   # Barcha sozlamalar (.env → pydantic)
├── requirements.txt            # Python kutubxonalari
├── Dockerfile                  # Konteyner tasviri
├── docker-compose.yml          # PostgreSQL + Redis + Bot
├── alembic.ini                 # Alembic konfiguratsiyasi
│
├── alembic/
│   └── versions/
│       ├── 0001_initial_schema.py       # Boshlang'ich jadvallar
│       ├── 0002_v2_groups_admin_meta.py # Guruhlar + admin meta
│       └── 0003_v3_leak_source_routing.py # source_chat_id ustuni
│
├── handlers/                   # Telegram event handlerlari
│   ├── start.py                # /start, /help, menyu callbacklari
│   ├── admin.py                # Barcha admin komandalar (11 ta)
│   ├── onboarding.py           # FSM: add_admin, add_channel, add_group
│   ├── channel_events.py       # Kanal postlari saqlash
│   └── group_events.py         # Guruh himoyasi (asosiy logika)
│
├── core/                       # Biznes logika
│   ├── ban_manager.py          # Ban/unban + DB + admin notify
│   ├── behavior_engine.py      # Redis sliding-window forward rate
│   ├── clone_detector.py       # Klon kanal aniqlash
│   ├── command_parsing.py      # Legacy argument parser yordamchilari
│   ├── content_analyzer.py     # SHA-256, pHash, OCR, watermark
│   ├── decision_matrix.py      # Risk skoring (5 omil → 0-100 ball)
│   ├── jobs.py                 # arq background jobs (media tahlil)
│   ├── media_processor.py      # Media yuklab olish + pHash/OCR
│   └── spam_detector.py        # TG havola, URL, kalit so'z aniqlash
│
├── database/
│   ├── db.py                   # Async SQLAlchemy engine + session
│   └── models.py               # 10 ta jadval modeli
│
├── middlewares/
│   ├── rate_limit.py           # 0.5s throttling (Redis)
│   └── role_check.py           # RBAC + get_admin_role()
│
├── utils/
│   ├── logger.py               # Rotating file + konsol + JSON log
│   ├── metrics.py              # Prometheus metrikalar + /health
│   ├── redis_client.py         # Redis ulanish pool
│   └── retry.py                # Exponential backoff decorator
│
├── tests/
│   ├── conftest.py             # Mock fixture'lar
│   ├── test_core.py            # ContentAnalyzer, DecisionMatrix, BehaviorEngine
│   └── test_command_parsing.py # Argument parser testlari
│
└── logs/
    └── guardbot.log            # Rotating log fayli
```

---

## Tezkor boshlash eslatmasi

```bash
# 1. .env ni to'ldiring (BOT_TOKEN, SUPER_ADMIN_IDS, DATABASE_URL)
# 2. DB migratsiya
alembic upgrade head
# 3. Botni ishga tushiring
python3 bot.py
# 4. (Ixtiyoriy) Media background tahlil uchun
arq core.jobs.WorkerSettings
# 5. Botga /start yuboring va himoyalangan kanal/guruhlarni qo'shing:
#    /add_channel → kanal postini forward qiling
#    /add_group   → guruh xabarini forward qiling
```

---

*Hujjat GuardBot MVP v2 versiyasi asosida yozilgan. Muallif: Kiro AI, 2026.*
