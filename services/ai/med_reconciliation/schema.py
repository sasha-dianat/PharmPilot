from __future__ import annotations

from dataclasses import dataclass, field


MODEL_VERSION = "med-reconciliation-rules-v1"

PHARMACIST_VERIFICATION_NOTICE = (
    "This finding is advisory only. Verify with the patient and prescriber before taking any clinical action."
)


@dataclass(frozen=True)
class MedEntry:
    drug_name: str
    normalized_name: str = ""
    strength: str | None = None
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    indication: str | None = None
    status: str | None = None
    source: str = "request"
    patient_id: str | None = None
    id: str | None = None
    classes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReconciliationDiscrepancy:
    discrepancy_id: str
    discrepancy_type: str
    severity: str
    source_a_label: str
    source_b_label: str
    drug_name: str
    drugs_involved: list[str]
    source_a_entry: MedEntry | None
    source_b_entry: MedEntry | None
    explanation: str
    suggested_pharmacist_action: str
    confidence: float
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(frozen=True)
class ReconciliationResult:
    patient_id: str
    source_a_label: str
    source_b_label: str
    discrepancies: list[ReconciliationDiscrepancy]
    drugs_in_source_a: int
    drugs_in_source_b: int
    reconciled_count: int
    assessment_date: str
    pharmacist_verification_notice: str = PHARMACIST_VERIFICATION_NOTICE


@dataclass(frozen=True)
class ReconciliationContext:
    patient_id: str
    source_a_label: str
    source_b_label: str
    source_a_meds: list[MedEntry]
    source_b_meds: list[MedEntry]
