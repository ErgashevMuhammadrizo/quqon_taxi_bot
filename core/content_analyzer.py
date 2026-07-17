"""
Content Analyzer
=================
Kanaldagi original postlar bilan guruh/boshqa joyda paydo bo'lgan
kontentni solishtirib, "leak" (o'g'irlangan) yoki "clone" ekanligini aniqlaydi.

Tekshiruv turlari:
  1. content_hash   - matn/media uchun tezkor exact/near-exact hash solishtirish
  2. perceptual_hash - rasm/video uchun vizual o'xshashlik (screenshot ham ushlaydi)
  3. ocr_similarity - rasm ichidagi matnni o'qib, original matn bilan solishtirish
  4. watermark      - postga yashirin token/watermark joylab, keyin uni qidirish

Eslatma: OCR va perceptual-hash uchun productionda `imagehash`, `Pillow`,
`pytesseract` yoki bulut OCR (Google Vision, AWS Textract) kutubxonalari
ishlatiladi. Bu yerda ular uchun aniq interfeys va ishlaydigan fallback
(hashlib asosida) yozilgan - kutubxonalar o'rnatilganda avtomatik faollashadi.
"""
from __future__ import annotations

import hashlib
import io
import re
import secrets
from dataclasses import dataclass
from difflib import SequenceMatcher

try:
    from PIL import Image
    import imagehash
    _HAS_IMAGEHASH = True
except ImportError:  # kutubxona o'rnatilmagan bo'lsa ham bot ishlashda davom etadi
    _HAS_IMAGEHASH = False

try:
    import pytesseract
    _HAS_OCR = True
except ImportError:
    _HAS_OCR = False


@dataclass
class AnalysisResult:
    is_match: bool
    match_type: str          # "hash" | "phash" | "ocr" | "watermark" | "none"
    similarity: float        # 0.0 - 1.0
    matched_post_id: int | None = None


def normalize_text(text: str) -> str:
    """Solishtirish oldidan matnni tozalaydi: bo'shliqlar, tinish belgilari, register."""
    text = text.lower().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return text


def compute_text_hash(text: str) -> str:
    """Matn uchun SHA-256 hash (normalizatsiyadan keyin)."""
    normalized = normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def compute_bytes_hash(data: bytes) -> str:
    """Media fayl baytlari uchun exact-match hash."""
    return hashlib.sha256(data).hexdigest()


def compute_perceptual_hash(image_bytes: bytes) -> str | None:
    """
    Rasm uchun perceptual hash (pHash). Screenshotlar, qayta siqilgan yoki
    watermark bosilgan rasmlarni ham tanib olishga yordam beradi.
    Agar `imagehash`/`Pillow` o'rnatilmagan bo'lsa, None qaytaradi
    (chaqiruvchi kod bunda content_hash ga tayanadi).
    """
    if not _HAS_IMAGEHASH:
        return None
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return str(imagehash.phash(img))
    except Exception:
        return None


def phash_similarity(hash_a: str, hash_b: str) -> float:
    """Ikkita pHash orasidagi o'xshashlikni 0..1 oralig'ida qaytaradi (Hamming distance asosida)."""
    if not _HAS_IMAGEHASH:
        return 0.0
    try:
        h1 = imagehash.hex_to_hash(hash_a)
        h2 = imagehash.hex_to_hash(hash_b)
        max_bits = len(h1.hash) ** 2
        distance = h1 - h2
        return 1 - (distance / max_bits)
    except Exception:
        return 0.0


def extract_text_from_image(image_bytes: bytes, lang: str = "eng+uzb+rus") -> str:
    """Rasm ichidagi matnni OCR orqali chiqarib oladi. OCR mavjud bo'lmasa bo'sh qaytaradi."""
    if not _HAS_OCR:
        return ""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        return pytesseract.image_to_string(img, lang=lang)
    except Exception:
        return ""


def text_similarity(text_a: str, text_b: str) -> float:
    """Ikki matn orasidagi o'xshashlik darajasi (0..1), OCR natijalarini solishtirish uchun."""
    a, b = normalize_text(text_a), normalize_text(text_b)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def generate_watermark_token() -> str:
    """Har bir postga joylanadigan, foydalanuvchiga ko'rinmas noyob watermark tokeni."""
    return secrets.token_hex(8)


def embed_watermark(caption: str, token: str) -> str:
    """
    Zero-width belgilar yordamida matn ichiga ko'rinmas watermark joylaydi.
    Bu orqali forward/copy-paste qilingan matnda ham manba kuzatiladi.
    """
    zero_width_map = {"0": "\u200b", "1": "\u200c"}
    binary = "".join(zero_width_map[b] for b in bin(int(token, 16))[2:])
    return f"{caption}{binary}"


def extract_watermark(text: str) -> str | None:
    """Matndan zero-width watermarkni ajratib oladi (agar mavjud bo'lsa)."""
    zero_width_map = {"\u200b": "0", "\u200c": "1"}
    bits = "".join(zero_width_map[c] for c in text if c in zero_width_map)
    if not bits:
        return None
    try:
        return hex(int(bits, 2))[2:]
    except ValueError:
        return None


class ContentAnalyzer:
    """Yuqoridagi primitivlarni birlashtirib, to'liq tahlil natijasini qaytaruvchi asosiy klass."""

    def __init__(self, hash_threshold: float, ocr_threshold: float):
        self.hash_threshold = hash_threshold
        self.ocr_threshold = ocr_threshold

    def analyze_text(self, incoming_text: str, known_posts: list[tuple[int, str]]) -> AnalysisResult:
        """
        `known_posts`: [(post_id, original_text), ...] - himoyalangan kanaldagi postlar.
        Avval exact-hash, keyin fuzzy similarity tekshiriladi.
        """
        incoming_hash = compute_text_hash(incoming_text)
        for post_id, original_text in known_posts:
            if compute_text_hash(original_text) == incoming_hash:
                return AnalysisResult(True, "hash", 1.0, post_id)

        best_score, best_id = 0.0, None
        for post_id, original_text in known_posts:
            score = text_similarity(incoming_text, original_text)
            if score > best_score:
                best_score, best_id = score, post_id

        if best_score >= self.ocr_threshold:
            return AnalysisResult(True, "ocr", best_score, best_id)
        return AnalysisResult(False, "none", best_score, best_id)

    def analyze_image(self, image_bytes: bytes, known_posts: list[tuple[int, str, str]]) -> AnalysisResult:
        """
        `known_posts`: [(post_id, content_hash, phash_or_empty), ...]
        1) exact byte-hash, 2) perceptual hash, 3) OCR fallback orqali tekshiradi.
        """
        exact_hash = compute_bytes_hash(image_bytes)
        for post_id, content_hash, _phash in known_posts:
            if content_hash == exact_hash:
                return AnalysisResult(True, "hash", 1.0, post_id)

        incoming_phash = compute_perceptual_hash(image_bytes)
        if incoming_phash:
            best_score, best_id = 0.0, None
            for post_id, _hash, phash in known_posts:
                if not phash:
                    continue
                score = phash_similarity(incoming_phash, phash)
                if score > best_score:
                    best_score, best_id = score, post_id
            if best_score >= self.hash_threshold:
                return AnalysisResult(True, "phash", best_score, best_id)

        return AnalysisResult(False, "none", 0.0, None)
