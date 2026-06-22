from __future__ import annotations

from dataclasses import dataclass

from .severity import InteractionSeverity

VERIFICATION_NOTICE = (
    "Advisory clinical decision support only. A licensed pharmacist must verify "
    "patient-specific appropriateness before any action."
)

_PROV_RANK = {"current_rx": 0, "active": 1, "historical": 2}


@dataclass
class Finding:
    rule_id: str
    type: str                       # drug_drug | drug_disease | duplicate_therapy | drug_allergy | drug_context
    severity: InteractionSeverity
    base_severity: InteractionSeverity
    direction: str                  # toxicity | efficacy_loss | additive_risk | opposition
    predicted_magnitude: str | None
    onset_offset: str | None
    participants: list[dict]
    mechanism: str
    mechanism_basis: str | None
    clinical_problem: str
    suggested_actions: list[str]
    evidence_sources: list[str]
    evidence_grade: str             # Established | Probable | Theoretical | Predicted
    source: str                     # curated | ddinter | inferred_mechanistic
    patient_specific_factors: list[str]
    confidence: float
    recency_note: str | None
    pharmacist_verification_notice: str = VERIFICATION_NOTICE


@dataclass
class InteractionReport:
    summary: dict
    findings: list[Finding]
    generated_at: str | None = None
    degraded: bool = False


def _provenance_rank(f: Finding) -> int:
    return min((_PROV_RANK.get(p.get("provenance"), 3) for p in f.participants), default=3)


def build_report(findings: list[Finding], *, degraded: bool = False,
                 generated_at: str | None = None) -> InteractionReport:
    ordered = sorted(findings, key=lambda f: (-f.severity.rank, _provenance_rank(f)))
    summary = {s.value: 0 for s in InteractionSeverity}
    for f in ordered:
        summary[f.severity.value] += 1
    return InteractionReport(summary=summary, findings=ordered,
                             generated_at=generated_at, degraded=degraded)
