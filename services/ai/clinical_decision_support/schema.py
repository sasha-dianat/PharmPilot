from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict


PHARMACIST_VERIFICATION_NOTICE = (
    "Advisory clinical decision support only. A licensed pharmacist must verify patient-specific appropriateness before any action."
)


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MODERATE = "MODERATE"
    LOW = "LOW"
    INFO = "INFO"


class AlertDict(TypedDict):
    rule_id: str
    severity: str
    title: str
    clinical_problem: str
    mechanism: str
    patient_specific_factors: list[str]
    missing_information: list[str]
    suggested_pharmacist_actions: list[str]
    evidence_sources: list[str]
    confidence: float
    pharmacist_verification_notice: str


@dataclass(frozen=True)
class CDSMedication:
    drug_name: str
    normalized_name: str
    classes: set[str] = field(default_factory=set)
    source: str | None = None


@dataclass(frozen=True)
class LabValue:
    value: float | None
    unit: str | None = None
    collected_at: str | None = None


@dataclass(frozen=True)
class CDSContext:
    age: int | None = None
    weight_kg: float | None = None
    pregnancy_status: str | None = None
    renal_function: str | None = None
    hepatic_status: str | None = None
    conditions: list[str] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    medications: list[CDSMedication] = field(default_factory=list)
    labs: dict[str, LabValue] = field(default_factory=dict)
