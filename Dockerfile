FROM python:3.12-slim

# ─── Tizim kutubxonalari ───────────────────────────────────────────────────────
# tesseract-ocr — OCR uchun (ixtiyoriy, lekin o'rnatilsa avtomatik faollashadi)
# libgl1        — Pillow/imagehash uchun OpenGL dependency
# curl          — healthcheck uchun (ixtiyoriy)
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-uzb \
    tesseract-ocr-rus \
    libgl1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ─── Working directory ────────────────────────────────────────────────────────
WORKDIR /app

# ─── Python dependencies ──────────────────────────────────────────────────────
# requirements.txt ni alohida ko'chirib, cache dan foydalanish
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# ─── Application kodi ─────────────────────────────────────────────────────────
COPY . .

# ─── Log papkasi ──────────────────────────────────────────────────────────────
RUN mkdir -p logs

# ─── Non-root foydalanuvchi (xavfsizlik) ──────────────────────────────────────
RUN useradd -m -u 1000 guardbot \
    && chown -R guardbot:guardbot /app
USER guardbot

# ─── Ishga tushirish ──────────────────────────────────────────────────────────
CMD ["python3", "bot.py"]
