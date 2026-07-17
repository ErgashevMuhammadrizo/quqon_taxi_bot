"""
Decision Matrix
===============
Content Analyzer va Behavior Engine natijalarini birlashtirib,
yagona "risk score" (0-100) hisoblaydi va qanday harakat qilinishini
(IGNORE / WARN / ADMIN_CONFIRM / AUTO_BAN) belgilaydi.

Vaznlar arxitektura sxemasidagi foizlarga mos (config.py da sozlanadi):
  Forward/Hash match      -> 30%
  OCR Text Similarity     -> 25%
  Watermark Verification  -> 20%
  Behavior Score          -> 15%
  Account Age             -> 10%
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from config import settings


class Action(str, enum.Enum):
    IGNORE = "IGNORE"
    WARN = "WARN"
    ADMIN_CONFIRM = "ADMIN_CONFIRM"
    AUTO_BAN = "AUTO_BAN"


@dataclass
class RiskFactors:
    hash_match_score: float = 0.0        # 0..1 - content/forward hash mosligi
    ocr_similarity_score: float = 0.0    # 0..1
    watermark_verified: float = 0.0      # 0..1 (0 yoki 1 odatda)
    behavior_score: float = 0.0          # 0..1
    account_age_score: float = 0.0       # 0..1


@dataclass
class Decision:
    risk_score: float   # 0..100
    action: Action
    factors: RiskFactors


class DecisionMatrix:
    def __init__(self):
        self.w = {
            "hash": settings.WEIGHT_FORWARD_HASH,
            "ocr": settings.WEIGHT_OCR_SIMILARITY,
            "watermark": settings.WEIGHT_WATERMARK,
            "behavior": settings.WEIGHT_BEHAVIOR,
            "age": settings.WEIGHT_ACCOUNT_AGE,
        }

    def compute_risk_score(self, factors: RiskFactors) -> float:
        score = (
            factors.hash_match_score * self.w["hash"]
            + factors.ocr_similarity_score * self.w["ocr"]
            + factors.watermark_verified * self.w["watermark"]
            + factors.behavior_score * self.w["behavior"]
            + factors.account_age_score * self.w["age"]
        )
        return round(score * 100, 2)

    def decide(self, factors: RiskFactors) -> Decision:
        score = self.compute_risk_score(factors)

        if score >= settings.AUTO_BAN_RISK_THRESHOLD:
            action = Action.AUTO_BAN
        elif score >= settings.ADMIN_CONFIRM_RISK_THRESHOLD:
            action = Action.ADMIN_CONFIRM
        elif score >= 25:
            action = Action.WARN
        else:
            action = Action.IGNORE

        return Decision(risk_score=score, action=action, factors=factors)
