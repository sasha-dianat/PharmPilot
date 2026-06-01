"""Drug safety and interaction checking module."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class DrugSafetyFinding:
    severity: str
    description: str
    interacting_drug: Optional[str] = None
    evidence_grade: str = "B"


class DrugSafetyModule:
    HIGH_ALERT_DRUGS = {
        "insulin", "warfarin", "heparin", "methotrexate", "digoxin",
        "lithium", "phenytoin", "theophylline", "cyclosporine", "tacrolimus",
    }
    # Use substring matching — drug names include strength ("Oxycodone 10mg")
    CNS_DEPRESSANTS = {"alprazolam", "clonazepam", "diazepam", "lorazepam",
                       "zolpidem", "eszopiclone", "temazepam", "triazolam"}
    OPIOIDS = {"morphine", "oxycodone", "hydrocodone", "fentanyl",
               "tramadol", "codeine", "hydromorphone", "oxymorphone",
               "buprenorphine", "methadone"}

    def _contains(self, drug_name: str, drug_set: set) -> bool:
        """Check if drug_name contains any member of drug_set as a substring."""
        name_lower = drug_name.lower()
        return any(d in name_lower for d in drug_set)

    def check_interactions(
        self,
        new_drug: dict,
        active_medications: list[dict],
        allergies: list[dict],
    ) -> list[dict]:
        findings = []
        new_name = new_drug.get("drug_name", "").lower()

        # ── Allergy check ─────────────────────────────────────────────────
        for allergy in allergies:
            allergen = allergy.get("allergen_name", "").lower()
            if allergen and len(allergen) > 2 and allergen in new_name:
                findings.append({
                    "severity": "critical",
                    "type": "allergy",
                    "description": (
                        f"ALLERGY MATCH: Patient allergic to {allergy['allergen_name']}. "
                        f"Reaction: {allergy.get('reaction', 'documented')}. "
                        f"Severity: {allergy.get('severity', 'unknown')}."
                    ),
                    "evidence_grade": "A",
                })

        # ── Opioid + CNS depressant (FDA Black Box) ───────────────────────
        if self._contains(new_name, self.OPIOIDS):
            for med in active_medications:
                med_name = med.get("drug_name", "").lower()
                if self._contains(med_name, self.CNS_DEPRESSANTS):
                    findings.append({
                        "severity": "critical",
                        "type": "interaction",
                        "description": (
                            f"FDA BLACK BOX WARNING — Opioid + CNS Depressant: "
                            f"{new_drug.get('drug_name')} + {med.get('drug_name')}. "
                            f"Concurrent use increases risk of profound sedation, "
                            f"respiratory depression, coma, and death. "
                            f"Prescriber clarification required."
                        ),
                        "interacting_drug": med.get("drug_name"),
                        "evidence_grade": "A",
                    })

        return findings
