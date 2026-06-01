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
    CNS_DEPRESSANTS = {"alprazolam","clonazepam","diazepam","lorazepam","zolpidem","eszopiclone"}
    OPIOIDS = {"morphine","oxycodone","hydrocodone","fentanyl","tramadol","codeine","hydromorphone"}

    def check_interactions(self, new_drug: dict, active_medications: list[dict], allergies: list[dict]) -> list[dict]:
        findings = []
        new_name = new_drug.get("drug_name", "").lower()
        new_rxcui = new_drug.get("rxcui", "")

        for allergy in allergies:
            allergen = allergy.get("allergen_name", "").lower()
            if allergen and allergen in new_name:
                findings.append({
                    "severity": "critical",
                    "type": "allergy",
                    "description": f"ALLERGY MATCH: Patient allergic to {allergen}. Reaction: {allergy.get('reaction', 'documented')}",
                    "evidence_grade": "A",
                })

        if new_name in self.OPIOIDS:
            for med in active_medications:
                med_name = med.get("drug_name", "").lower()
                if med_name in self.CNS_DEPRESSANTS:
                    findings.append({
                        "severity": "critical",
                        "type": "interaction",
                        "description": f"Opioid + benzodiazepine combination: {new_name} + {med_name}. FDA Black Box Warning — increased risk of respiratory depression and death.",
                        "interacting_drug": med_name,
                        "evidence_grade": "A",
                    })
        return findings
