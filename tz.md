# GuardBot — TZ va Progress hujjati

Manba: yuklangan `index.html` taqdimot sahifasidagi arxitektura/xususiyatlar
spetsifikatsiyasi (Python 3.12+, Aiogram 3.x, PostgreSQL + Redis, Docker).

Sana: 2026-07-06

---

## ✅ QILINGAN ISHLAR

### 1. Arxitektura va loyiha strukturasi
- To'liq modulli papka strukturasi yaratildi (`database/`, `core/`, `handlers/`,
  `middlewares/`, `utils/`, `tests/`) — spetsifikatsiyadagi "Entry Layer / Core
  Engine / Decision / Action" qatlamlariga mos.
- `config.py` — barcha sozlamalar `.env` orqali (`pydantic-settings`), jumladan
  risk-skoring vaznlari, threshold'lar, rate-limit parametrlari.

### 2. Ma'lumotlar bazasi (PostgreSQL + SQLAlchemy async)
- Modellar: `User`, `Channel`, `ProtectedPost`, `AuditLog`, `BannedUser`,
  `Whitelist`, `Admin` (`database/models.py`).
- Async engine/session (`database/db.py`), `init_models()` bilan avtomatik
  jadval yaratish.
- ✅ SQLite bilan test qilindi — jadvallar muvaffaqiyatli yaratildi.

### 3. Content Analyzer (`core/content_analyzer.py`)
- Matn uchun normalizatsiya + SHA-256 hash + fuzzy similarity (`difflib`).
- Rasm uchun exact-hash va **perceptual hash (pHash)** interfeysi
  (`imagehash`/`Pillow` — ixtiyoriy, o'rnatilmasa fallback ishlaydi).
- OCR interfeysi (`pytesseract` — ixtiyoriy, Dockerfile'da tesseract-ocr
  o'rnatilgan).
- **Ko'rinmas watermark** (zero-width unicode belgilar orqali) — joylash va
  o'qib olish funksiyalari yozildi va test qilindi.
- ✅ Unit testlar bilan tekshirildi (hash, similarity, watermark round-trip).

### 4. Behavior Engine (`core/behavior_engine.py`)
- Redis sorted-set asosida **sliding-window rate limiting** (necha marta
  forward qilingani).
- Forward tezligi va akkaunt yoshi bo'yicha 0..1 shubha skori.

### 5. Decision Matrix (`core/decision_matrix.py`)
- Spetsifikatsiyadagi vaznlar bilan (Hash 30%, OCR 25%, Watermark 20%,
  Behavior 15%, Account Age 10%) yagona 0-100 risk score hisoblanadi.
- 4 xil harakat: `IGNORE / WARN / ADMIN_CONFIRM / AUTO_BAN`.
- ✅ Parametrlashtirilgan unit testlar bilan tekshirildi.

### 6. Ban Manager (`core/ban_manager.py`)
- Whitelist tekshiruvi → `ban_chat_member(revoke_messages=True)` → AuditLog +
  BannedUser yozuvi → barcha adminlarga real-time xabar (evidence bilan).
- `unban()` funksiyasi ham mavjud.

### 7. Handlerlar
- `channel_events.py` — kanaldagi yangi postni avtomatik "himoyaga olish"
  (hash, watermark hisoblash va bazaga yozish).
- `group_events.py` — forward qilingan xabarlarni ushlab, Content Analyzer +
  Behavior Engine + Decision Matrix orqali risk hisoblab, harakat qiladi
  (auto-ban paytida xabar ham o'chiriladi).
- `admin.py` — **8 ta admin komandasi** to'liq ishlaydigan holda:
  `/stats`, `/banned`, `/unban`, `/whitelist`, `/settings`, `/scan_history`,
  `/export_logs` (JSON fayl sifatida yuboradi), `/add_admin`. Shu bilan birga
  inline tugma orqali "Ban qilish / E'tiborsiz" tasdiqlash oqimi.

### 8. Middleware'lar
- `RoleCheckMiddleware` — RBAC: Super Admin / Moderator / Viewer, komandalar
  bo'yicha minimal rol xaritasi bilan.
- `ThrottlingMiddleware` — umumiy flood-control (Redis asosida).

### 9. Infratuzilma
- `Dockerfile` (Python 3.12-slim + tesseract-ocr).
- `docker-compose.yml` — bot + PostgreSQL 16 + Redis 7, healthcheck'lar bilan.
- `.env.example` — barcha kerakli o'zgaruvchilar bilan.
- `requirements.txt` — barcha asosiy va ixtiyoriy kutubxonalar bilan.
- `.gitignore`, `README.md`.

### 10. Sifat nazorati (bajarilgan tekshiruvlar)
- ✅ Barcha `.py` fayllar `py_compile` orqali sintaksis xatosiz.
- ✅ `bot.py` to'liq import qilindi, `Dispatcher` va barcha routerlar
  (channel_events, group_events, admin) muvaffaqiyatli yig'ildi.
- ✅ `config.py` real `.env` bilan yuklandi (super_admins parsing tekshirildi).
- ✅ SQLite orqali `init_models()` ishga tushirilib, real jadvallar yaratildi.
- ✅ `pytest tests/test_core.py` — **11/11 test muvaffaqiyatli o'tdi**
  (hash, similarity, watermark, content analyzer, decision matrix).

---

## ⏳ QOLGAN ISHLAR (keyingi bosqich uchun)

Quyidagilar **kod skeletida interfeysi tayyor**, lekin production darajasida
to'liq ishlash uchun qo'shimcha ish talab qiladi:

1. **Rasm/video baytlarini yuklab olish** — hozir `channel_events.py` faqat
   `file_id` asosida hash hisoblaydi (tezkor, lekin haqiqiy piksel darajasida
   emas). To'liq pHash/OCR ishlashi uchun:
   - `bot.download(file_id)` orqali baytlarni olish,
   - background job/queue (masalan Celery yoki `arq`) orqali og'ir CPU
     ishlarini asosiy event loop'dan ajratish tavsiya etiladi.

2. **Guruh xabarlaridagi rasm/screenshotlarni tekshirish** — `group_events.py`
   hozircha faqat matnli forward'larni to'liq tahlil qiladi. Rasmli
   xabarlar uchun xuddi shu oqimni (`analyze_image`) ulash kerak.

3. **Alembic migratsiyalari** — hozir `init_models()` orqali sodda
   `create_all()` ishlatiladi (dev uchun yetarli). Productionda schema
   o'zgarishlarini boshqarish uchun Alembic sozlanishi kerak.

4. **Clone-channel detection** — boshqa (raqib) kanallarni monitoring qilib,
   ularda sizning kontentingiz paydo bo'lishini avtomatik skanerlash (hozirgi
   tizim faqat sizning botingiz a'zo bo'lgan chatlarni ko'radi). Bu alohida
   "userbot" (Telethon/Pyrogram user-session) yoki qo'lda kanal qo'shish
   funksiyasini talab qiladi.

5. **To'lov/obuna tizimi** (agar SaaS sifatida sotmoqchi bo'lsangiz) — bir
   nechta mijoz/kanal uchun multi-tenant qo'llab-quvvatlash, Stripe/Payme/Click
   integratsiyasi hozircha yo'q.

6. **Real Telegram Bot Token bilan end-to-end test** — kod sintaksis va
   unit-test darajasida tekshirilgan, lekin haqiqiy Telegram serveri bilan
   (webhook/polling) hali sinalmadi, chunki bot tokeni mavjud emas edi.

7. **Monitoring/metrics** (Prometheus/Grafana) — HTML taqdimotda "99.9%
   Uptime" ko'rsatkichi bor, buni haqiqiy qilish uchun metrics eksport
   qo'shilishi kerak.

---

---

## 🩹 MVP v3 — "Guruhdan chiqib ketgan kontent ban bermayapti" bugi tuzatildi

**Sana:** 2026-07-09

### Muammo edi:
Avvalgi versiyada bot faqat XABAR TUSHGAN chat (`destination`) "himoyalangan
guruh" bo'lsa ban qilar edi. Ya'ni: agar kimdir himoyalangan guruhdagi
kontentni bot ADMIN bo'lmagan boshqa guruhga tashlasa — bot u yerda ban
qila olmagani uchun HECH QANDAY harakat qilmasdi (na destination'da, na
manba guruhda).

### Nima tuzatildi:
1. `database/models.py` — `ProtectedPost` endi ham kanal, ham GURUH
   kontentini fingerprint qilib, har doim `source_chat_id` (aslida qaysi
   chatga tegishli ekani) bilan saqlaydi.
2. `handlers/group_events.py` — to'liq qayta qurildi: endi bot A'ZO BO'LGAN
   ISTALGAN chatda (faqat ro'yxatdagi guruhlar bilan cheklanmasdan) kelayotgan
   xabarni fingerprint bazasi bilan solishtiradi. Mos kelsa — ban ASOSIY
   ravishda MANBA (himoyalangan) guruhda amalga oshiriladi, chunki bot faqat
   o'zi admin bo'lgan joyda ban qila oladi. Destination'da ham (agar bot u
   yerda ham admin bo'lsa) qo'shimcha ban/delete urinib ko'riladi — bu bonus,
   asosiy emas.
3. `core/jobs.py` — rasm/screenshot tekshiruvi ham xuddi shunday: ban manba
   chatda, destination'da bonus urinish.
4. `core/clone_detector.py` — `save_clone_incident()` ichidagi ishlamaydigan
   SQL update tuzatildi (avval runtime xato berardi).
5. `database/db.py` — SQLite bilan lokal test/dev qilish uchun engine sozlamasi
   tuzatildi (avval faqat PostgreSQL pool argumentlari bilan ishlardi).
6. Yangi Alembic migratsiyasi qo'shildi: `2024_01_03_0003_v3_leak_source_routing.py`.

### MUHIM CHEKLOV (bu kod bilan aylanib o'tib bo'lmaydigan Telegram platforma cheklovi):
Agar kontent Telegram **"Saqlangan xabarlar"**ga yoki bot **UMUMAN A'ZO
BO'LMAGAN** biror chatga (guruh yoki shaxsiy) tashlansa — Telegram Bot API
bunday harakat haqida botga **hech qanday signal yubormaydi**. Bu — hech
qanday bot (pullik yoki bepul, GuardBot yoki boshqa) bunday harakatni
"jonli" ko'ra olmaydigan, Telegram tomonidan qo'yilgan qat'iy cheklov.

**Amaldagi yechim:** agar kontent keyinchalik (hattoki kunlar/haftalar
o'tib) bot ko'ra oladigan BIRON chatda qayta paydo bo'lsa (masalan, birov uni
boshqa guruhga forward qilsa yoki screenshot qilib joylasa) — fingerprint
orqali baribir ANIQLANADI va o'sha payt manba guruhda avtomatik ban qilinadi.
Ya'ni aniqlash kechikishi mumkin, lekin butunlay yo'qolmaydi. Bundan tashqari,
watermark (ko'rinmas belgi) har doim matnga singdirilgan bo'lib qoladi — u
orqali kontent qayerdan sizib chiqqani keyinroq ham isbotlanadi.

---

## 🚀 Ishga tushirish uchun keyingi qadamlar (sizga)

1. `.env.example` ni `.env` qilib nusxalang va `BOT_TOKEN` (BotFather'dan),
   `SUPER_ADMIN_IDS` (o'z Telegram ID'ingiz) ni kiriting.
2. `docker-compose up -d` — bot, PostgreSQL, Redis avtomatik ishga tushadi.
3. Botni himoyalanadigan kanalga **admin** qilib qo'shing.
4. Botni monitoring qilinadigan guruhga ham admin (kamida "ban" huquqi bilan)
   qilib qo'shing.
5. `/stats` orqali ishlab turganini tekshiring.

Savol yoki qo'shimcha modul (masalan clone-channel monitoring yoki to'lov
tizimi) kerak bo'lsa — keyingi bosqichda davom ettirish mumkin.
