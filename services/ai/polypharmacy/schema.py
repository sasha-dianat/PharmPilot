from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


MODEL_VERSION = "polypharmacy-rules-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory polypharmacy support only. A licensed pharmacist must verify patient-specific appropriateness "
    "and discuss any medication changes with the prescriber before action."
)

Priority = Literal["high", "moderate", "low"]
MessageFormat = Literal["sbar", "concise"]


@dataclass(frozen=True)
class LabValue:
    value: float | None
    unit: str | None = None
    collected_at: str | None = None


@dataclass(frozen=True)
class PolyMedication:
    drug_name: str
    normalized_name: str
    classes: set[str] = field(default_factory=set)
    indication: str | None = None
    status: str | None = "active"
    source: str | None = None


@dataclass(frozen=True)
class PolyContext:
    age: int | None = None
    conditions: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    medications: list[PolyMedication] = field(default_factory=list)
    labs: dict[str, LabValue] = field(default_factory=dict)


@dataclass(frozen=True)
class BurdenScore:
    score: int
    drugs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Finding:
    category: str
    priority: Priority
    drugs_involved: list[str]
    explanation: str
    suggested_pharmacist_discussion: str
    tapering_caution: str | None
    evidence_sources: list[str]
    confidence: float
    missing_information: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReviewResult:
    anticholinergic_burden: BurdenScore
    sedative_fall_risk: BurdenScore
    findings: list[Finding]
    missing_information: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CascadeRule:
    rule_id: str
    trigger_matches: set[str]
    effect: str
    treating_matches: set[str]
    explanation: str
    evidence_sources: list[str]
    confidence: float = 0.78


@dataclass(frozen=True)
class PIMRule:
    rule_id: str
    matches: set[str]
    explanation: str
    evidence_sources: list[str]
    priority: Priority = "moderate"
    confidence: float = 0.82


@dataclass(frozen=True)
class ExpectedRule:
    rule_id: str
    condition_matches: set[str]
    expected_matches: set[str]
    explanation: str
    evidence_sources: list[str]
    min_age: int | None = None
    any_condition_matches: set[str] = field(default_factory=set)
    confidence: float = 0.72
