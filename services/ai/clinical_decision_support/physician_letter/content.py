# services/ai/clinical_decision_support/physician_letter/content.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ClinicalContent:
    warning: str
    mechanism: str
    drugs: list[str] = field(default_factory=list)


def build_clinical_content(findings: list[dict]) -> ClinicalContent:
    """De-identified clinical payload from contraindicated findings — the ONLY
    clinical text the LLM ever sees. Contains drug names + mechanism, no identifiers."""
    drugs: list[str] = []
    mechanisms: list[str] = []
    for f in findings:
        for p in f.get("participants", []):
            name = p.get("name")
            if name and name not in drugs:
                drugs.append(name)
        if f.get("mechanism"):
            mechanisms.append(f["mechanism"])
    warning = ("A contraindicated drug interaction was identified during pharmacist "
               "verification of this prescription.")
    return ClinicalContent(warning=warning, mechanism=" ".join(mechanisms), drugs=drugs)
