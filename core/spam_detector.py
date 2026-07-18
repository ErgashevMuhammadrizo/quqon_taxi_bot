"""
Spam / Reklama Detector — Kuchaytirilgan versiya
==================================================
Guruh xavfsizligini ta'minlash uchun quyidagi holatlarni aniqlaydi:

  1. Reklama urinishi
     - Telegram kanal/guruh linklari (t.me/..., @username invites)
     - Ko'p sonli URL (≥2 ta xabar ichida)
     - Reklama kalit so'zlari (sotamiz, chegirma, obuna, join va h.k.)
     - @mention + reklama kombinatsiyasi

  2. Bot (avtomatlashtirilgan akkaunt) belgisi
     - from_user.is_bot = True
     - Akkaunt nomi shubhali pattern

  3. Shubhali media + link kombinatsiyasi
     - Rasm/video + URL → ban

  4. Emoji spam
     - Ko'p pul/reklama emoji (💰🔥💎🚀) + matn

Har bir funksiya SpamResult qaytaradi:
  is_spam      : bool
  spam_type    : str
  confidence   : float (0.0 – 1.0)
  matched      : list[str]
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
    (?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/[\w+\-]+
    """,
    re.VERBOSE | re.IGNORECASE,
)

# Umumiy URL pattern
_URL_RE = re.compile(
    r"https?://[^\s<>\"']{4,}|www\.[^\s<>\"']{4,}",
    re.IGNORECASE,
)

# Faqat mention (@username) pattern — link emas
_MENTION_RE = re.compile(r"@[a-zA-Z]\w{3,}", re.IGNORECASE)

# Reklama emoji pattern — 3 va undan ko'p reklama emoji
_AD_EMOJI_RE = re.compile(
    r"[💰💎🔥🚀🤑💵💸🎁🎯🏆🥇⭐🌟✨🎉🎊💥🔔📢📣🆓]"
)

# ─── Reklama kalit so'zlari (UZ / RU / EN) — kengaytirilgan ──────────────────

_AD_KEYWORDS: tuple[str, ...] = (
    # ── O'zbekcha ─────────────────────────────────────────────────────────────
    "sotamiz", "sotiladi", "sotib oling", "xarid qiling",
    "chegirma", "aksiya", "maxsus taklif", "foydali taklif",
    "obuna bo'ling", "obuna", "kanalga qo'shiling", "guruhga qo'shiling",
    "a'zo bo'ling", "kuzatib boring", "follow qiling",
    "foiz chegirma", "arzon narx", "eng arzon",
    "reklama", "hamkorlik", "biznes taklif", "sheriklik",
    "daromad", "pul ishlash", "topish mumkin", "ishlash imkoniyati",
    "oyiga", "kuniga", "soatiga", "daromad olish",
    "kurs", "trening", "seminar", "konsultatsiya", "konsultatsiya bepul",
    "telegram kanal", "telegram guruh", "yangi kanal", "yangi guruh",
    "link qoldirdim", "link bio", "havola", "havola bio",
    "bepul", "tekin", "sovg'a", "prize", "yutuq",
    "cashback", "bonus", "referal", "referral",
    "crypto", "nft", "token", "coin", "bitcoin", "usdt",
    "invest", "investitsiya", "moliyaviy",
    "kiyim", "ayollar", "erkaklar", "mahsulot", "tovar",
    "ulgurji", "chakana", "optom",
    # ── Ruscha ────────────────────────────────────────────────────────────────
    "купить", "продаётся", "продам", "скидка", "акция",
    "подписывайтесь", "подпишись", "вступайте", "присоединяйтесь",
    "реклама", "заработок", "доход", "заработать",
    "бесплатно", "выгодное предложение", "специальное предложение",
    "курс", "тренинг", "бизнес предложение", "партнёрство",
    "ссылка в bio", "ссылка в шапке", "переходите по ссылке",
    "пишите в личку", "пишите в лс", "пиши в лс",
    "крипто", "биткоин", "инвестиции", "вложения",
    "оптом", "розница", "товар", "одежда",
    # ── Inglizcha ─────────────────────────────────────────────────────────────
    "buy now", "click here", "subscribe", "join our", "join now",
    "free offer", "discount", "limited offer", "special offer",
    "earn money", "work from home", "make money", "passive income",
    "crypto", "investment", "forex", "promo", "promotion",
    "affiliate", "referral link", "dm me", "link in bio",
    "check my bio", "follow me", "follow us",
    "get rich", "financial freedom",
)

# ─── Yuqori xavf kalit so'zlari — bitta so'z = 0.95 ishonch ─────────────────
_HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    "sotamiz", "sotiladi", "купить", "продаётся", "продам",
    "buy now", "earn money", "make money", "get rich",
    "pul ishlash", "daromad olish", "криптo", "crypto",
    "forex", "bitcoin", "usdt", "nft", "token",
)


# ─── Asosiy tekshiruvchi klass ────────────────────────────────────────────────

class SpamDetector:
    def __init__(
        self,
        ad_keyword_confidence: float = 0.85,
        tg_link_confidence:    float = 0.95,
        multi_url_threshold:   int   = 2,
        multi_url_confidence:  float = 0.80,
        media_link_confidence: float = 0.85,
        emoji_spam_threshold:  int   = 3,
        emoji_spam_confidence: float = 0.70,
    ) -> None:
        self.ad_kw_conf       = ad_keyword_confidence
        self.tg_link_conf     = tg_link_confidence
        self.multi_url_th     = multi_url_threshold
        self.multi_url_conf   = multi_url_confidence
        self.media_link_conf  = media_link_confidence
        self.emoji_thresh     = emoji_spam_threshold
        self.emoji_conf       = emoji_spam_confidence

    # ── Matn tekshiruvi ───────────────────────────────────────────────────────

    def check_text(self, text: str) -> SpamResult:
        if not text or not text.strip():
            return SpamResult.clean()

        results: list[SpamResult] = [
            self._check_tg_links(text),
            self._check_multi_url(text),
            self._check_ad_keywords(text),
            self._check_mention_with_ad(text),
            self._check_emoji_spam(text),
        ]

        best = max(results, key=lambda r: r.confidence)
        return best if best.is_spam else SpamResult.clean()

    def check_media_with_text(self, caption: str | None) -> SpamResult:
        if not caption:
            return SpamResult.clean()

        text_result = self.check_text(caption)
        if text_result.is_spam:
            return text_result

        # Caption ichida istalgan URL → media+link = ban
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
        name = (username or first_name or "").lower()
        bot_patterns = [
            r"bot$", r"_bot_", r"^bot",
            r"\d{4,}",
            r"[a-z]{1,3}\d+$",
        ]
        for pat in bot_patterns:
            if re.search(pat, name):
                return SpamResult(
                    is_spam=True, spam_type="bot_account",
                    confidence=0.60, matched=[name],
                )
        return SpamResult.clean()

    # ── Ichki metodlar ────────────────────────────────────────────────────────

    def _check_tg_links(self, text: str) -> SpamResult:
        """Faqat haqiqiy t.me/telegram.me havolalarini tekshiradi (bare @mention emas —
        bu alohida _check_mention_with_ad orqali, faqat reklama konteksti bilan tekshiriladi)."""
        matches = _TG_LINK_RE.findall(text)
        if not matches:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True, spam_type="ad_link",
            confidence=self.tg_link_conf, matched=matches[:5],
        )

    def _check_multi_url(self, text: str) -> SpamResult:
        """2 va undan ko'p URL → ban."""
        urls = _URL_RE.findall(text)
        if len(urls) < self.multi_url_th:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True, spam_type="multi_url",
            confidence=self.multi_url_conf, matched=urls[:5],
        )

    def _check_ad_keywords(self, text: str) -> SpamResult:
        """Reklama kalit so'zlarini tekshiradi."""
        lower = text.lower()

        # Yuqori xavf so'zlar — bitta = 0.95
        for kw in _HIGH_RISK_KEYWORDS:
            if kw in lower:
                return SpamResult(
                    is_spam=True, spam_type="ad_keyword_high",
                    confidence=0.95, matched=[kw],
                )

        # Oddiy kalit so'zlar
        found: list[str] = [kw for kw in _AD_KEYWORDS if kw in lower]
        if not found:
            return SpamResult.clean()

        # 1 so'z = 0.65, har qo'shimcha +0.10, max 0.95
        conf = min(0.65 + (len(found) - 1) * 0.10, 0.95)
        return SpamResult(
            is_spam=True, spam_type="ad_keyword",
            confidence=conf, matched=found[:5],
        )

    def _check_mention_with_ad(self, text: str) -> SpamResult:
        """
        @mention + reklama belgilari kombinatsiyasi.
        Faqat mention bo'lsa yetarli emas (oddiy gaplashish bo'lishi mumkin),
        lekin mention + URL yoki mention + reklama so'z = ban.
        """
        mentions = _MENTION_RE.findall(text)
        if not mentions:
            return SpamResult.clean()

        lower = text.lower()
        has_url = bool(_URL_RE.search(text))
        has_ad_kw = any(kw in lower for kw in _AD_KEYWORDS[:20])  # eng kuchli 20 ta

        if has_url and mentions:
            return SpamResult(
                is_spam=True, spam_type="mention_url",
                confidence=0.90, matched=mentions[:3],
            )
        if has_ad_kw and mentions:
            return SpamResult(
                is_spam=True, spam_type="mention_ad",
                confidence=0.80, matched=mentions[:3],
            )

        return SpamResult.clean()

    def _check_emoji_spam(self, text: str) -> SpamResult:
        """
        Ko'p reklama emoji (💰🔥💎🚀 va h.k.) + qisqa matn = spam belgisi.
        """
        emoji_matches = _AD_EMOJI_RE.findall(text)
        if len(emoji_matches) < self.emoji_thresh:
            return SpamResult.clean()

        # Matn qisqa va emoji ko'p bo'lsa ishonch yuqori
        word_count = len(text.split())
        emoji_ratio = len(emoji_matches) / max(word_count, 1)
        conf = min(self.emoji_conf + emoji_ratio * 0.1, 0.90)

        return SpamResult(
            is_spam=True, spam_type="emoji_spam",
            confidence=conf,
            matched=list(set(emoji_matches))[:5],
        )


# ─── Global singleton ─────────────────────────────────────────────────────────

spam_detector = SpamDetector()


# ─── Wrapper funksiyalar ──────────────────────────────────────────────────────

def detect_spam_in_text(text: str) -> SpamResult:
    """Matnni spam uchun tekshiradi."""
    return spam_detector.check_text(text)


def detect_spam_in_media(caption: str | None) -> SpamResult:
    """Media + caption ni spam uchun tekshiradi."""
    return spam_detector.check_media_with_text(caption)


def is_suspicious_account(username: str | None, first_name: str | None) -> SpamResult:
    """Akkauntni shubhali bot/spam akkaunt ekanligini tekshiradi."""
    return spam_detector.check_bot_account(username, first_name)
