"""
Watermark Module (9-band) — "Kelajak uchun modul"
=====================================================
Hozirgi `core/content_analyzer.py` allaqachon kanal postlari uchun
zero-width watermark qo'yadi (klon/leak aniqlash uchun). Bu modul esa
undan farqli, KENGROQ maqsad uchun API: kelajakda "secret content"
(masalan bitta userga maxsus yuborilgan eksklyuziv material) yuborilganda,
HAR BIR OLUVCHI uchun ALOHIDA, ko'rinmas watermark joylash — shunday qilib
kontent qayerdan (kimdan) sizib chiqqani keyinroq aniq isbotlanadi.

Hozircha faqat API (interfeys) yozilgan — chaqiruvchi kod (handler'lar)
hali ulanmagan. Amalga oshirish zero-width unicode kodlash orqali,
`core/content_analyzer.py`dagi mavjud primitivlar bilan bir xil texnikada
ishlaydi, lekin bu yerda watermark tokeni "post ID" emas, balki
"(chat_id, user_id, timestamp)" uchligidan hosil qilinadi — shu orqali
aniq QAYSI foydalanuvchiga yuborilgan nusxa ekanligi bilinadi.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

# Zero-width belgilar — ko'zga ko'rinmaydi, lekin matn ichida saqlanadi.
_ZW_MAP = {"0": "\u200b", "1": "\u200c"}  # zero-width space / zero-width non-joiner
_ZW_REV = {v: k for k, v in _ZW_MAP.items()}
_ZW_MARKER = "\u2060"  # word joiner — watermark boshlanishi/tugashi belgisi


@dataclass
class RecipientWatermark:
    token: str          # qisqartirilgan, odam o'qiy oladigan token (loglash uchun)
    encoded_suffix: str  # matn oxiriga qo'shiladigan ko'rinmas qator


class WatermarkService:
    """
    Kelajakdagi "Secret Content Protection" uchun API.

    Ishlatilishi (kelajakda):
        wm = watermark_service.generate_for_recipient(chat_id, user_id)
        protected_text = watermark_service.embed(original_text, wm)
        # ... keyinroq, boshqa joyda topilgan matndan:
        found_token = watermark_service.extract(leaked_text)
        # found_token orqali qaysi (chat_id, user_id) ekanligini DB'dan topish mumkin
    """

    def generate_for_recipient(self, chat_id: int, user_id: int) -> RecipientWatermark:
        raw = f"{chat_id}:{user_id}:{time.time_ns()}"
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        binary = bin(int(digest, 16))[2:].zfill(64)
        encoded = _ZW_MARKER + "".join(_ZW_MAP[b] for b in binary) + _ZW_MARKER
        return RecipientWatermark(token=digest, encoded_suffix=encoded)

    def embed(self, text: str, watermark: RecipientWatermark) -> str:
        """Matn oxiriga ko'rinmas watermark qo'shadi. Foydalanuvchiga hech narsa ko'rinmaydi."""
        return text + watermark.encoded_suffix

    def extract(self, text: str) -> str | None:
        """Matndan watermark tokenini ajratib oladi (topilmasa None)."""
        if _ZW_MARKER not in text:
            return None
        try:
            start = text.index(_ZW_MARKER) + 1
            end = text.index(_ZW_MARKER, start)
        except ValueError:
            return None

        bits = ""
        for ch in text[start:end]:
            if ch in _ZW_REV:
                bits += _ZW_REV[ch]
        if not bits:
            return None

        try:
            value = int(bits, 2)
            return hex(value)[2:].zfill(16)
        except ValueError:
            return None


watermark_service = WatermarkService()
