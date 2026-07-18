"""
Spam / Reklama Detector
========================
Guruh xavfsizligini ta'minlash uchun quyidagi holatlarni aniqlaydi:

  1. HTTP/HTTPS havolalar
     - http:// yoki https:// bilan boshlangan istalgan URL → darhol ban
     - www. bilan boshlangan URL → darhol ban

  2. Telegram havolalar
     - t.me/... yoki telegram.me/... → darhol ban
     - @username (bare mention) — yolg'iz ham ban

  3. Ko'p URL
     - 2 va undan ko'p URL bir xabarda → ban

  4. Reklama kalit so'zlari
     - UZ / RU / EN — 150+ kalit so'z
     - Yuqori xavf so'zlar: bitta = 0.95 ishonch

  5. Media + URL kombinatsiyasi
     - Caption'da istalgan URL → ban

  6. Emoji spam
     - 3+ reklama emoji (💰🔥💎🚀...) → ban

  SpamResult:
    is_spam    : bool
    spam_type  : str
    confidence : float  (0.0 – 1.0)
    matched    : list[str]
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


# ─── Regex patternlar ─────────────────────────────────────────────────────────

# Telegram havolalar: t.me/xxx yoki telegram.me/xxx
_TG_LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?:t|telegram)\.(?:me|dog)/[\w+\-]+",
    re.IGNORECASE,
)

# Istalgan HTTP/HTTPS/WWW URL
_URL_RE = re.compile(
    r"https?://[^\s<>\"']{2,}|www\.[^\s<>\"']{4,}",
    re.IGNORECASE,
)

# Bare @mention — yolg'iz kanal/guruh mention
# 4+ belgi bo'lishi kerak (qisqa @nick odatda reklama emas)
_MENTION_RE = re.compile(r"@[a-zA-Z]\w{3,}", re.IGNORECASE)

# Reklama emoji — 3+ ta bo'lsa spam
_AD_EMOJI_RE = re.compile(
    r"[💰💎🔥🚀🤑💵💸🎁🎯🏆🥇⭐🌟✨🎉🎊💥🔔📢📣🆓♻️🔗💲]"
)

# ─── Reklama kalit so'zlari ───────────────────────────────────────────────────

# Yuqori xavf — BITTA so'z = 0.95 ishonch → darhol ban
_HIGH_RISK_KEYWORDS: tuple[str, ...] = (
    # O'zbekcha
    "sotamiz", "sotiladi", "sotib oling", "xarid qiling",
    "pul ishlash", "daromad olish", "daromad", "oylik daromad",
    "topish mumkin", "ishlash imkoniyati",
    # Ruscha
    "купить", "продаётся", "продам", "продаю",
    "заработать", "заработок", "доход", "заработок в интернете",
    "пишите в личку", "пишите в лс", "пиши в лс",
    # Inglizcha
    "buy now", "earn money", "make money", "get rich",
    "passive income", "work from home", "financial freedom",
    # Kripto/moliya
    "bitcoin", "btc", "usdt", "ethereum", "eth",
    "crypto", "крипто", "forex", "nft", "token", "coin",
    "invest now", "инвестируй",
)

# Oddiy reklama so'zlari — kombinatsiyada ishlatiladi
_AD_KEYWORDS: tuple[str, ...] = (
    # O'zbekcha
    "chegirma", "aksiya", "maxsus taklif", "foydali taklif",
    "obuna bo'ling", "obuna", "kanalga qo'shiling", "guruhga qo'shiling",
    "a'zo bo'ling", "kuzatib boring", "follow qiling",
    "foiz chegirma", "arzon narx", "eng arzon",
    "reklama", "hamkorlik", "biznes taklif", "sheriklik",
    "oyiga", "kuniga", "soatiga",
    "kurs", "trening", "seminar", "konsultatsiya", "konsultatsiya bepul",
    "telegram kanal", "telegram guruh", "yangi kanal", "yangi guruh",
    "link qoldirdim", "link bio", "havola", "havola bio",
    "bepul", "tekin", "sovg'a", "yutuq",
    "cashback", "bonus", "referal", "referral",
    "invest", "investitsiya", "moliyaviy",
    "kiyim", "ayollar", "erkaklar", "mahsulot", "tovar",
    "ulgurji", "chakana", "optom",
    # Ruscha
    "скидка", "акция", "подписывайтесь", "подпишись",
    "вступайте", "присоединяйтесь", "реклама",
    "бесплатно", "выгодное предложение", "специальное предложение",
    "курс", "тренинг", "бизнес предложение", "партнёрство",
    "ссылка в bio", "ссылка в шапке", "переходите по ссылке",
    "крипто", "биткоин", "инвестиции", "вложения",
    "оптом", "розница", "товар", "одежда",
    # Inglizcha
    "click here", "subscribe", "join our", "join now",
    "free offer", "discount", "limited offer", "special offer",
    "crypto", "investment", "forex", "promo", "promotion",
    "affiliate", "referral link", "dm me", "link in bio",
    "check my bio", "follow me", "follow us",
)


# ─── SpamDetector ─────────────────────────────────────────────────────────────

class SpamDetector:
    """Stateless spam/reklama aniqlash mexanizmi."""

    def __init__(
        self,
        url_confidence:        float = 0.97,   # http:// → darhol ban
        tg_link_confidence:    float = 0.97,   # t.me/ → darhol ban
        bare_mention_confidence: float = 0.90, # @username yolg'iz → ban
        multi_url_threshold:   int   = 2,
        multi_url_confidence:  float = 0.90,
        media_link_confidence: float = 0.92,
        emoji_spam_threshold:  int   = 3,
        emoji_spam_confidence: float = 0.75,
        high_kw_confidence:    float = 0.95,
    ) -> None:
        self.url_conf         = url_confidence
        self.tg_link_conf     = tg_link_confidence
        self.bare_mention_conf = bare_mention_confidence
        self.multi_url_th     = multi_url_threshold
        self.multi_url_conf   = multi_url_confidence
        self.media_link_conf  = media_link_confidence
        self.emoji_thresh     = emoji_spam_threshold
        self.emoji_conf       = emoji_spam_confidence
        self.high_kw_conf     = high_kw_confidence

    # ── Matn tekshiruvi ───────────────────────────────────────────────────────

    def check_text(self, text: str) -> SpamResult:
        """
        Matnni to'liq spam tekshiruvidan o'tkazadi.
        Eng yuqori ishonchli natijani qaytaradi.
        """
        if not text or not text.strip():
            return SpamResult.clean()

        checks = [
            self._check_urls(text),           # http:// https://
            self._check_tg_links(text),        # t.me/
            self._check_bare_mentions(text),   # @username yolg'iz
            self._check_multi_url(text),       # 2+ URL
            self._check_high_risk_keywords(text),
            self._check_ad_keywords(text),
            self._check_mention_with_context(text),
            self._check_emoji_spam(text),
        ]

        best = max(checks, key=lambda r: r.confidence)
        return best if best.is_spam else SpamResult.clean()

    def check_media_caption(self, caption: str | None) -> SpamResult:
        """Media xabar caption'ini tekshiradi."""
        if not caption:
            return SpamResult.clean()

        # Avval matn tekshiruvi
        result = self.check_text(caption)
        if result.is_spam:
            return result

        # Caption'da istalgan URL → media+link
        urls = _URL_RE.findall(caption) + _TG_LINK_RE.findall(caption)
        if urls:
            return SpamResult(
                is_spam=True,
                spam_type="media_link",
                confidence=self.media_link_conf,
                matched=urls[:5],
            )

        return SpamResult.clean()

    # ── Ichki metodlar ────────────────────────────────────────────────────────

    def _check_urls(self, text: str) -> SpamResult:
        """
        HTTP/HTTPS/WWW URL → darhol ban.
        Har qanday URL reklama deb hisoblanadi, chunki oddiy guruh a'zolari
        URL yubormasligi kerak.
        """
        urls = _URL_RE.findall(text)
        if not urls:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True,
            spam_type="url",
            confidence=self.url_conf,
            matched=urls[:5],
        )

    def _check_tg_links(self, text: str) -> SpamResult:
        """t.me/ yoki telegram.me/ havolalari → darhol ban."""
        matches = _TG_LINK_RE.findall(text)
        if not matches:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True,
            spam_type="tg_link",
            confidence=self.tg_link_conf,
            matched=matches[:5],
        )

    def _check_bare_mentions(self, text: str) -> SpamResult:
        """
        @username yolg'iz → ban.
        Oddiy suhbatda ham @mention bo'lishi mumkin, lekin kanal/guruh
        reklamasi ko'pincha faqat @username dan iborat bo'ladi.
        Shuning uchun bare @mention ham ban sababidir.
        """
        mentions = _MENTION_RE.findall(text)
        if not mentions:
            return SpamResult.clean()

        # Xabarda faqat @mention(lar) bor yoki qisqa matn bilan
        word_count = len(text.split())
        # Agar 5 so'zdan kam bo'lsa va mention bo'lsa — yuqori ishonch
        if word_count <= 5:
            return SpamResult(
                is_spam=True,
                spam_type="bare_mention",
                confidence=self.bare_mention_conf,
                matched=mentions[:5],
            )
        # Ko'proq matn bo'lsa — past ishonch (mention_context da tekshiriladi)
        return SpamResult.clean()

    def _check_multi_url(self, text: str) -> SpamResult:
        """2 va undan ko'p URL → ban."""
        all_urls = _URL_RE.findall(text) + _TG_LINK_RE.findall(text)
        if len(all_urls) < self.multi_url_th:
            return SpamResult.clean()
        return SpamResult(
            is_spam=True,
            spam_type="multi_url",
            confidence=self.multi_url_conf,
            matched=all_urls[:5],
        )

    def _check_high_risk_keywords(self, text: str) -> SpamResult:
        """Yuqori xavf kalit so'zlar — bitta = 0.95 ishonch."""
        lower = text.lower()
        for kw in _HIGH_RISK_KEYWORDS:
            if kw in lower:
                return SpamResult(
                    is_spam=True,
                    spam_type="high_risk_keyword",
                    confidence=self.high_kw_conf,
                    matched=[kw],
                )
        return SpamResult.clean()

    def _check_ad_keywords(self, text: str) -> SpamResult:
        """Oddiy reklama kalit so'zlari — kombinatsiyada ishonch oshadi."""
        lower = text.lower()
        found = [kw for kw in _AD_KEYWORDS if kw in lower]
        if not found:
            return SpamResult.clean()
        # 1 so'z = 0.70, har qo'shimcha +0.08, max 0.95
        conf = min(0.70 + (len(found) - 1) * 0.08, 0.95)
        return SpamResult(
            is_spam=True,
            spam_type="ad_keyword",
            confidence=conf,
            matched=found[:5],
        )

    def _check_mention_with_context(self, text: str) -> SpamResult:
        """
        @mention + URL yoki reklama so'z kombinatsiyasi.
        Ko'p so'zli xabarda mention + reklama konteksti → ban.
        """
        mentions = _MENTION_RE.findall(text)
        if not mentions:
            return SpamResult.clean()

        lower = text.lower()
        has_url  = bool(_URL_RE.search(text) or _TG_LINK_RE.search(text))
        has_ad   = any(kw in lower for kw in _AD_KEYWORDS)
        has_high = any(kw in lower for kw in _HIGH_RISK_KEYWORDS)

        if has_url:
            return SpamResult(
                is_spam=True, spam_type="mention_url",
                confidence=0.95, matched=mentions[:3],
            )
        if has_high:
            return SpamResult(
                is_spam=True, spam_type="mention_high_kw",
                confidence=0.92, matched=mentions[:3],
            )
        if has_ad:
            return SpamResult(
                is_spam=True, spam_type="mention_ad",
                confidence=0.85, matched=mentions[:3],
            )
        return SpamResult.clean()

    def _check_emoji_spam(self, text: str) -> SpamResult:
        """3+ reklama emoji → ban."""
        emoji_matches = _AD_EMOJI_RE.findall(text)
        if len(emoji_matches) < self.emoji_thresh:
            return SpamResult.clean()
        word_count = len(text.split())
        emoji_ratio = len(emoji_matches) / max(word_count, 1)
        conf = min(self.emoji_conf + emoji_ratio * 0.08, 0.92)
        return SpamResult(
            is_spam=True,
            spam_type="emoji_spam",
            confidence=conf,
            matched=list(set(emoji_matches))[:5],
        )


# ─── Global singleton ─────────────────────────────────────────────────────────

spam_detector = SpamDetector()


# ─── Public API ───────────────────────────────────────────────────────────────

def detect_spam_in_text(text: str) -> SpamResult:
    """Matnni spam uchun tekshiradi."""
    return spam_detector.check_text(text)


def detect_spam_in_media(caption: str | None) -> SpamResult:
    """Media caption'ini spam uchun tekshiradi."""
    return spam_detector.check_media_caption(caption)


def is_suspicious_account(username: str | None, first_name: str | None) -> SpamResult:
    """Akkaunt nomini bot/spam pattern uchun tekshiradi."""
    name = (username or first_name or "").lower()
    patterns = [r"bot$", r"_bot_", r"^bot", r"\d{5,}", r"[a-z]{1,3}\d+$"]
    import re as _re
    for pat in patterns:
        if _re.search(pat, name):
            return SpamResult(
                is_spam=True, spam_type="bot_account",
                confidence=0.65, matched=[name],
            )
    return SpamResult.clean()
