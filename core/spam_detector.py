"""
Spam / Reklama Detector
========================
Guruh xavfsizligini ta'minlash uchun quyidagi holatlarni aniqlaydi:

  1. Reklama urinishi
     - Telegram kanal/guruh linklari (t.me/..., @username invites)
     - Ko'p sonli URL (≥2 ta xabar ichida)
     - Reklama kalit so'zlari (sotamiz, chegirma, obuna, join va h.k.)

  2. Bot (avtomatlashtirilgan akkaunt) belgisi
     - from_user.is_bot = True
     - Akkaunt nomi shubhali pattern (raqam+harflar, bot suffix)
     - Juda tez xabar yuborish (rate limit orqali — BehaviorEngine da)

  3. Shubhali media + link kombinatsiyasi
     - Rasm/video + URL → admin confirm

Har bir funksiya SpamResult qaytaradi:
  is_spam      : bool
  spam_type    : str   ("ad_link" | "ad_keyword" | "multi_url" | "bot_account"
                         | "media_link" | "none")
  confidence   : float (0.0 – 1.0)
  matched      : list[str]  # aniqlangan fragment/kalitso'zlar
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


# ─── Result ───────────────────────────────────────────────────────────────────

@dataclass
class SpamResult:
    is_spam:    bool
    spam_type:  str
    confidence: float
    matched:    list[str] = field(default_factory=list)

    @classmethod
    def clean(cls) -> "SpamResult":
        return cls(is_spam=False, spam_type="none", confidence=0.0)


# ─── Telegram link pattern ────────────────────────────────────────────────────

_TG_LINK_RE = re.compile(
    r"""
    (?:
        # t.me / telegram.me / telegram.dog linklari
        (?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/[\w+\-]+
        |
        # @username yoki @username_123 (a'zo bo'lish taklifi)
        @[a-zA-Z]\w{3,}
    )
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Umumiy URL pattern (t.me dan tashqari)
_URL_RE = re.compile(
    r"https?://[^\s<>\"']{4,}|www\.[^\s<>\"']{4,}",
    re.IGNORECASE,
)

# ─── Reklama kalit so'zlari (UZ / RU / EN) ───────────────────────────────────
# Kichik harflarda saqlangan — tekshirish oldidan lower() qilinadi

_AD_KEYWORDS: tuple[str, ...] = (
    # O'zbekcha
    "sotamiz", "sotiladi", "xarid qiling", "chegirma", "aksiya",
    "obuna bo'ling", "obuna", "kanalga qo'shiling", "guruhga qo'shiling",
    "foiz chegirma", "arzon", "foydali taklif", "maxsus taklif",
    "reklama", "hamkorlik", "biznes taklif",
    "daromad", "pul ishlash", "topish mumkin", "ishlash imkoniyati",
    "kurs", "trening", "seminar", "konsultatsiya bepul",
    "telegram kanal", "telegram guruh",
    # Ruscha
    "купить", "продаётся", "скидка", "акция", "подписывайтесь",
    "подпишись", "вступайте", "реклама", "заработок", "доход",
    "заработать", "бесплатно", "выгодное предложение",
    "курс", "тренинг", "бизнес предложение",
    # Inglizcha
    "buy now", "click here", "subscribe", "join our", "free offer",
    "discount", "limited offer", "earn money", "work from home",
    "make money", "crypto", "investment", "forex", "promo",
    "affiliate", "referral link",
)

# Oddiy mention'lar (bot komandasi emas, lekin kanal/guruh reklama)
_AD_MENTION_RE = re.compile(r"@[a-zA-Z]\w{3,}", re.IGNORECASE)


# ─── Asosiy tekshiruvchi klass ────────────────────────────────────────────────

class SpamDetector:
    """
    Guruh xabarlarini spam/reklama uchun tekshiradi.
    Stateless — har safar yangi instans yoki singleton sifatida ishlatiladi.
    """

    def __init__(
        self,
        ad_keyword_confidence: float = 0.85,
        tg_link_confidence:    float = 0.95,
        multi_url_threshold:   int   = 2,
        multi_url_confidence:  float = 0.70,
        media_link_confidence: float = 0.65,
    ) -> None:
        self.ad_kw_conf     = ad_keyword_confidence
        self.tg_link_conf   = tg_link_confidence
        self.multi_url_th   = multi_url_threshold
        self.multi_url_conf = multi_url_confidence
        self.media_link_conf = media_link_confidence

    # ── Matn tekshiruvi ───────────────────────────────────────────────────────

    def check_text(self, text: str) -> SpamResult:
        """
        Matnni spam/reklama uchun tekshiradi.
        Eng yuqori confidence'li natijani qaytaradi.
        """
        if not text or not text.strip():
            return SpamResult.clean()

        results: list[SpamResult] = [
            self._check_tg_links(text),
            self._check_multi_url(text),
            self._check_ad_keywords(text),
        ]

        # Eng yuqori ishonchlilikni tanlash
        best = max(results, key=lambda r: r.confidence)
        return best if best.is_spam else SpamResult.clean()

    def check_media_with_text(self, caption: str | None) -> SpamResult:
        """
        Media xabar + caption tekshiruvi.
        Kaption bo'lsa ham spam tekshiradi; bo'lmasa ham media+link bo'lishi mumkin.
        """
        if not caption:
            return SpamResult.clean()

        # Avval oddiy matn tekshiruvi
        text_result = self.check_text(caption)
        if text_result.is_spam:
            return text_result

        # Caption ichida URL bo'lsa — media+link holati
        urls = _URL_RE.findall(caption) + _TG_LINK_RE.findall(caption)
        if urls:
            return SpamResult(
                is_spam=True,
                spam_type="media_link",
                confidence=self.media_link_conf,
                matched=urls[:5],
            )

        return SpamResult.clean()

    def check_bot_account(self, username: str | None, first_name: str | None) -> SpamResult:
        """
        Foydalanuvchi akkauntini bot/spam akkaunt ekanligini aniqlaydi.
        from_user.is_bot = True holati bu yerda tekshirilmaydi (handler o'zi qiladi).
        """
        name = (username or first_name or "").lower()

        # Bot suffix pattern
        bot_patterns = [
            r"bot$", r"_bot_", r"^bot",
            r"\d{4,}",           # ko'p raqam (spam akkaunt)
            r"[a-z]{1,3}\d+$",   # harflar + raqam oxirida (auto-generated)
        ]
        for pat in bot_patterns:
            if re.search(pat, name):
                return SpamResult(
                    is_spam=True,
                    spam_type="bot_account",
                    confidence=0.60,
                    matched=[name],
                )

        return SpamResult.clean()

    # ── Ichki yordamchi metodlar ──────────────────────────────────────────────

    def _check_tg_links(self, text: str) -> SpamResult:
        """Telegram kanal/guruh linklari va @mention'larini tekshiradi."""
        matches = _TG_LINK_RE.findall(text)
        if not matches:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True,
            spam_type="ad_link",
            confidence=self.tg_link_conf,
            matched=matches[:5],
        )

    def _check_multi_url(self, text: str) -> SpamResult:
        """Ko'p sonli URL (≥ threshold) aniqlaydi."""
        urls = _URL_RE.findall(text)
        if len(urls) < self.multi_url_th:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True,
            spam_type="multi_url",
            confidence=self.multi_url_conf,
            matched=urls[:5],
        )

    def _check_ad_keywords(self, text: str) -> SpamResult:
        """Reklama kalit so'zlarini tekshiradi."""
        lower = text.lower()
        found: list[str] = []
        for kw in _AD_KEYWORDS:
            if kw in lower:
                found.append(kw)
        if not found:
            return SpamResult.clean()
        # Ko'p kalit so'z = yuqoriroq ishonchlilik
        conf = min(0.55 + len(found) * 0.10, 0.90)
        return SpamResult(
            is_spam=True,
            spam_type="ad_keyword",
            confidence=conf,
            matched=found[:5],
        )


# ─── Global singleton ─────────────────────────────────────────────────────────

# default sozlamalar bilan tayyor instans — import qilib ishlatsa bo'ladi
spam_detector = SpamDetector()


# ─── Qulay wrapper funksiyalar ────────────────────────────────────────────────

def detect_spam_in_text(text: str) -> SpamResult:
    """Matnni spam uchun tekshiradi (global instans orqali)."""
    return spam_detector.check_text(text)


def detect_spam_in_media(caption: str | None) -> SpamResult:
    """Media + caption ni spam uchun tekshiradi."""
    return spam_detector.check_media_with_text(caption)


def is_suspicious_account(username: str | None, first_name: str | None) -> SpamResult:
    """Akkauntni shubhali bot/spam akkaunt ekanligini tekshiradi."""
    return spam_detector.check_bot_account(username, first_name)
