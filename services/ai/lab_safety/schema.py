from __future__ import annotations

from dataclasses import dataclass, field


MODEL_VERSION = "lab-safety-rules-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "This alert is advisory only. Verify with the prescriber before taking any clinical action."
)


@dataclass(frozen=True)
class LabSafetyFinding:
    rule_id: str
    severity: str
    drug: str
    lab_name: str
    lab_value: str
    lab_unit: str | None
    lab_date: str
    threshold_triggered: str
    explanation: str
    suggested_pharmacist_action: str
    evidence_source: str
    confidence: float
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(frozen=True)
class MissingLab:
    drug: str
    lab_name: str
    reason: str


@dataclass(frozen=True)
class LabSafetyResult:
    patient_id: str
    findings: list[LabSafetyFinding] = field(default_factory=list)
    missing_labs: list[MissingLab] = field(default_factory=list)
    drugs_evaluated: list[str] = field(default_factory=list)
    labs_evaluated: list[str] = field(default_factory=list)
    assessment_date: str = ""
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(frozen=True)
class LabSafetyContext:
    patient_id: str
    medications: list
    lab_results: list
