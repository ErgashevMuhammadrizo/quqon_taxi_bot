"""
GroupSecurityConfig — har guruh uchun sozlanadigan xavfsizlik parametrlari.

`database.models.ProtectedGroup` jadvalidagi ustunlardan quriladi (14-band:
"Configuration"). Guruh hali `ProtectedGroup` sifatida ro'yxatdan o'tmagan
bo'lsa ham (masalan bot yangi qo'shilgan bo'lsa), `default()` orqali xavfsiz
standart qiymatlar bilan ishlaydi — shu bilan Security Engine har doim
guruhga bog'lanmasdan ham to'g'ri ishlay oladi.
"""
from __future__ import annotations

from dataclasses import dataclass

from config import settings


@dataclass(frozen=True)
class GroupSecurityConfig:
    raid_protection_enabled: bool = True
    captcha_enabled: bool = True
    forward_block_enabled: bool = False
    link_block_enabled: bool = True
    media_block_enabled: bool = False
    ai_detection_enabled: bool = False
    risk_threshold: int = settings.SECURITY_RISK_ADMIN_ALERT
    trust_threshold: int = settings.SECURITY_TRUST_THRESHOLD_DEFAULT

    @classmethod
    def default(cls) -> "GroupSecurityConfig":
        return cls()

    @classmethod
    def from_protected_group(cls, group) -> "GroupSecurityConfig":  # type: ignore[no-untyped-def]
        """`ProtectedGroup` ORM obyektidan config yasaydi. `group` None bo'lsa default."""
        if group is None:
            return cls.default()
        return cls(
            raid_protection_enabled=bool(getattr(group, "raid_protection_enabled", True)),
            captcha_enabled=bool(getattr(group, "captcha_enabled", True)),
            forward_block_enabled=bool(getattr(group, "forward_block_enabled", False)),
            link_block_enabled=bool(getattr(group, "link_block_enabled", True)),
            media_block_enabled=bool(getattr(group, "media_block_enabled", False)),
            ai_detection_enabled=bool(getattr(group, "ai_detection_enabled", False)),
            risk_threshold=int(getattr(group, "risk_threshold", settings.SECURITY_RISK_ADMIN_ALERT)),
            trust_threshold=int(getattr(group, "trust_threshold", settings.SECURITY_TRUST_THRESHOLD_DEFAULT)),
        )
