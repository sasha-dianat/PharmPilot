"""Geriatric prescribing safety — Beers Criteria 2023."""
BEERS_CRITERIA_2023 = {
    "diphenhydramine": "Anticholinergic — avoid in elderly (cognitive impairment, urinary retention, falls)",
    "diazepam": "Long-acting benzodiazepine — avoid in elderly (fall risk, cognitive impairment)",
    "amitriptyline": "Tricyclic antidepressant — highly anticholinergic, avoid in elderly",
    "promethazine": "High anticholinergic burden — avoid in elderly",
    "indomethacin": "NSAID — highest risk in elderly (GI bleed, renal toxicity, fluid retention)",
    "meperidine": "Opioid — avoid in elderly (neurotoxic metabolite accumulation)",
    "nifedipine": "Short-acting calcium channel blocker — hypotension, falls risk",
    "glipizide": "Sulfonylurea — hypoglycemia risk in elderly; prefer safer alternatives",
    "doxazosin": "Alpha-blocker — orthostatic hypotension, falls",
}

class GeriatricsModule:
    def beers_check(self, drug: dict, patient_conditions: list[str]) -> str | None:
        drug_name = drug.get("drug_name", "").lower()
        for beers_drug, concern in BEERS_CRITERIA_2023.items():
            if beers_drug in drug_name:
                return f"BEERS CRITERIA (2023): {drug_name} — {concern}"
        return None
