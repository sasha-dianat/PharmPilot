from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MODEL_VERSION = "counselling-v1"

LEVELS = ("professional", "standard", "low_literacy", "elderly", "caregiver")
LANGUAGES = ("en", "fr", "fa", "ar", "es")

CounsellingLevel = Literal["professional", "standard", "low_literacy", "elderly", "caregiver"]
CounsellingLanguage = Literal["en", "fr", "fa", "ar", "es"]

PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory patient counselling support only. A licensed pharmacist must verify patient-specific "
    "appropriateness, answer questions, and confirm understanding before use."
)


@dataclass(frozen=True)
class CounsellingFacts:
    what_for: str
    how_to_take: str
    what_to_avoid: list[str]
    common_side_effects: list[str]
    serious_red_flags: list[str]
    missed_dose: str
    adherence_tips: list[str]
    evidence_source: str


@dataclass
class CounsellingContent:
    drug_name: str
    what_for: str
    how_to_take: str
    what_to_avoid: list[str] = field(default_factory=list)
    common_side_effects: list[str] = field(default_factory=list)
    serious_red_flags: list[str] = field(default_factory=list)
    missed_dose: str = ""
    adherence_tips: list[str] = field(default_factory=list)
    teach_back_questions: list[str] = field(default_factory=list)
    level: str = "standard"
    language: str = "en"


@dataclass(frozen=True)
class LLMMeta:
    llm_used: bool
    provider: str
    degraded: bool
    note: str | None = None
