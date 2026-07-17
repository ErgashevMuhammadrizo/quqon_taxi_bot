# GuardBot — Telegram Anti-Leak Protection System

Telegram kanal/guruhlardagi eksklyuziv kontentni forward, screenshot,
scraper-bot va klon-kanallardan himoya qiluvchi professional bot.

Arxitektura va xususiyatlar `index.html` taqdimot sahifasida tasvirlangan
spetsifikatsiyaga asoslanib qurilgan. Batafsil holat uchun **tz.md** ga qarang.

## Tezkor ishga tushirish (Docker bilan, tavsiya etiladi)

```bash
cp .env.example .env
# .env faylni oching va BOT_TOKEN, SUPER_ADMIN_IDS ni to'ldiring
docker-compose up -d
docker-compose logs -f bot
```

## Lokal ishga tushirish (Docker'siz)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# .env ni to'ldiring, DATABASE_URL va REDIS_URL ni lokal serverga moslang

python bot.py
```

## Testlar

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

## Loyiha strukturasi

```
guardbot/
├── bot.py                  # Entrypoint (polling/webhook)
├── config.py               # Barcha sozlamalar (.env orqali)
├── database/
│   ├── models.py           # SQLAlchemy modellari
│   └── db.py                # Async engine/session
├── core/
│   ├── content_analyzer.py # Hash, pHash, OCR, watermark
│   ├── behavior_engine.py  # Rate limiting, anomaliya skoring
│   ├── decision_matrix.py  # Risk score va action routing
│   └── ban_manager.py      # Avtomatik ban + audit + xabarnoma
├── handlers/
│   ├── channel_events.py   # Kanaldagi yangi postlarni himoyaga olish
│   ├── group_events.py     # Forward/leak aniqlash
│   └── admin.py            # Admin komandalari
├── middlewares/
│   ├── rate_limit.py       # Umumiy flood-control
│   └── role_check.py       # RBAC (Super Admin/Moderator/Viewer)
├── utils/
│   ├── redis_client.py
│   └── logger.py
└── tests/
    └── test_core.py
```

## Admin komandalari

| Komanda | Ruxsat darajasi | Vazifasi |
|---|---|---|
| `/stats` | Viewer | Umumiy statistika |
| `/scan_history [n]` | Viewer | So'nggi tekshiruvlar |
| `/banned [page]` | Viewer | Bloklanganlar ro'yxati |
| `/unban <user_id> <chat_id>` | Moderator | Blokdan chiqarish |
| `/whitelist [add\|remove] <id>` | Moderator | Whitelist boshqaruvi |
| `/export_logs` | Moderator | Audit log JSON eksport |
| `/settings` | Super Admin | Joriy risk sozlamalari |
| `/add_admin <id> <role>` | Super Admin | Yangi admin qo'shish |

## Muhim eslatmalar

- **OCR va perceptual-hash**: `Pillow`, `imagehash`, `pytesseract`
  `requirements.txt`da bor va Dockerfile ichida tesseract-ocr o'rnatilgan.
  Agar ular o'rnatilmagan bo'lsa, kod xatosiz ishlashda davom etadi, faqat
  OCR/pHash tekshiruvlari `analyze_image`da fallback qiladi.
- **Birinchi ishga tushirishda** jadvallar `init_models()` orqali avtomatik
  yaratiladi (dev uchun qulay). Productionda **Alembic** migratsiyalariga
  o'tish tavsiya etiladi (pastga qarang, tz.md "Qolgan ishlar" bo'limi).
- Botni kanalga **admin** qilib qo'shish shart (post o'qish/o'chirish huquqi bilan).
