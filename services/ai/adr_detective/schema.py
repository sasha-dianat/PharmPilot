from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal


MODEL_VERSION = "adr-rules-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory adverse reaction support only. A licensed pharmacist must verify patient-specific appropriateness before any action."
)
NOT_A_DIAGNOSIS_NOTICE = (
    "This output is not a diagnosis and does not replace clinical assessment, prescriber judgment, or emergency evaluation."
)

Causality = Literal["probable", "possible", "unlikely", "unclear"]
Seriousness = Literal["serious", "moderate", "mild"]
Urgency = Literal["high", "routine", "low", "unknown"]
TypicalOnset = Literal["days", "weeks", "months", "variable"]


@dataclass(frozen=True)
class ReactionEntry:
    reaction: str
    seriousness: Seriousness
    typical_onset: TypicalOnset
    evidence_source: str


@dataclass(frozen=True)
class LabValue:
    value: float | None
    unit: str | None = None
    collected_at: str | None = None


@dataclass(frozen=True)
class ADRMedication:
    drug_name: str
    normalized_name: str
    classes: set[str] = field(default_factory=set)
    start_date: date | None = None
    stop_date: date | None = None
    recent_dose_increase: bool = False
    source: str | None = None


@dataclass(frozen=True)
class ADRContext:
    complaint: str
    onset_date: date | None = None
    medications: list[ADRMedication] = field(default_factory=list)
    labs: dict[str, LabValue] = field(default_factory=dict)
    age: int | None = None
    conditions: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    pharmacy_id: str | None = None
    staff_id: str | None = None


@dataclass
class SuspectedCause:
    drug: str
    normalized_name: str
    reaction: str
    causality: Causality
    seriousness: Seriousness
    signals: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    questions_to_ask: list[str] = field(default_factory=list)
    missing_information: list[str] = field(default_factory=list)
    suggested_pharmacist_action: str = ""
    urgency: Urgency = "unknown"
    evidence_sources: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class LLMMeta:
    llm_used: bool
    provider: str
    degraded: bool
