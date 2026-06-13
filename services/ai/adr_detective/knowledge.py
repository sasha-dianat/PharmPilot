from __future__ import annotations

from services.ai.adr_detective.schema import ReactionEntry
from services.ai.clinical_decision_support.normalizer import BRAND_TO_GENERIC


REACTION_KEYWORDS: dict[str, list[str]] = {
    "cough": ["dry cough", "persistent cough", "cough"],
    "myalgia": ["muscle aches", "muscle ache", "muscle pain", "muscle weakness", "myalgia", "weakness"],
    "peripheral_edema": ["ankle swelling", "swollen ankles", "leg swelling", "peripheral edema", "edema", "oedema"],
    "confusion": ["confusion", "disoriented", "disorientation", "delirium", "memory"],
    "bleeding": ["black stool", "black stools", "melena", "bleeding", "bruising", "blood"],
    "hyponatremia": ["low sodium", "hyponatremia", "dizziness", "nausea confusion", "nausea"],
    "constipation": ["constipation", "hard stools", "no bowel movement"],
    "bradycardia_fatigue": ["slow heart rate", "bradycardia", "fatigue", "tiredness"],
    "hyperkalemia": ["high potassium", "hyperkalemia", "weakness palpitations"],
    "hypoglycemia": ["low blood sugar", "hypoglycemia", "sweating", "shakiness", "confusion after insulin"],
    "gi_upset": ["diarrhea", "stomach upset", "nausea vomiting", "gi upset"],
    "angioedema": ["angioedema", "face swelling", "lip swelling", "tongue swelling", "throat swelling"],
}

ANTICHOLINERGIC_DRUGS = {
    "amitriptyline",
    "benztropine",
    "chlorpheniramine",
    "cyclobenzaprine",
    "dicyclomine",
    "diphenhydramine",
    "doxepin",
    "hydroxyzine",
    "imipramine",
    "meclizine",
    "nortriptyline",
    "olanzapine",
    "oxybutynin",
    "promethazine",
    "scopolamine",
    "tolterodine",
}

SEROTONERGIC_DRUGS = {
    "sertraline",
    "fluoxetine",
    "paroxetine",
    "citalopram",
    "escitalopram",
    "fluvoxamine",
    "duloxetine",
    "venlafaxine",
    "desvenlafaxine",
    "tramadol",
}

NSAIDS = {"ibuprofen", "naproxen", "diclofenac", "meloxicam", "celecoxib", "indomethacin", "ketorolac"}
ANTICOAGULANTS = {"warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "enoxaparin", "heparin"}

DRUG_REACTIONS: dict[str, list[ReactionEntry]] = {
    "ace_inhibitor": [
        ReactionEntry("cough", "mild", "weeks", "FDA label: lisinopril"),
        ReactionEntry("angioedema", "serious", "variable", "FDA label: lisinopril"),
        ReactionEntry("hyperkalemia", "serious", "variable", "Lexicomp adverse effects monograph"),
    ],
    "statin": [
        ReactionEntry("myalgia", "moderate", "weeks", "FDA label: atorvastatin"),
    ],
    "dihydropyridine_ccb": [
        ReactionEntry("peripheral_edema", "moderate", "weeks", "FDA label: amlodipine"),
    ],
    "amlodipine": [
        ReactionEntry("peripheral_edema", "moderate", "weeks", "FDA label: amlodipine"),
    ],
    "ssri": [
        ReactionEntry("hyponatremia", "serious", "weeks", "Lexicomp adverse effects monograph"),
    ],
    "serotonergic": [
        ReactionEntry("hyponatremia", "serious", "weeks", "Lexicomp adverse effects monograph"),
    ],
    "anticholinergic": [
        ReactionEntry("confusion", "serious", "variable", "AGS Beers Criteria 2023"),
        ReactionEntry("constipation", "moderate", "variable", "AGS Beers Criteria 2023"),
    ],
    "nsaid": [
        ReactionEntry("bleeding", "serious", "variable", "FDA label: ibuprofen"),
    ],
    "anticoagulant": [
        ReactionEntry("bleeding", "serious", "variable", "FDA label: warfarin"),
    ],
    "opioid": [
        ReactionEntry("constipation", "moderate", "days", "Lexicomp adverse effects monograph"),
    ],
    "beta_blocker": [
        ReactionEntry("bradycardia_fatigue", "moderate", "days", "Lexicomp adverse effects monograph"),
    ],
    "biguanide": [
        ReactionEntry("gi_upset", "mild", "days", "FDA label: metformin"),
    ],
    "sulfonylurea": [
        ReactionEntry("hypoglycemia", "serious", "variable", "Lexicomp adverse effects monograph"),
    ],
    "potassium_sparing_diuretic": [
        ReactionEntry("hyperkalemia", "serious", "variable", "Lexicomp adverse effects monograph"),
    ],
    "insulin": [
        ReactionEntry("hypoglycemia", "serious", "variable", "FDA label: insulin"),
    ],
}

DRUG_CLASS_OVERRIDES: dict[str, set[str]] = {
    "amlodipine": {"dihydropyridine_ccb"},
    "nifedipine": {"dihydropyridine_ccb"},
    "felodipine": {"dihydropyridine_ccb"},
    "metoprolol": {"beta_blocker"},
    "atenolol": {"beta_blocker"},
    "carvedilol": {"beta_blocker"},
    "propranolol": {"beta_blocker"},
    "oxycodone": {"opioid"},
    "hydrocodone": {"opioid"},
    "morphine": {"opioid"},
    "fentanyl": {"opioid"},
    "glipizide": {"sulfonylurea"},
    "glyburide": {"sulfonylurea"},
    "glimepiride": {"sulfonylurea"},
    "insulin": {"insulin"},
}

for drug in ANTICHOLINERGIC_DRUGS:
    DRUG_CLASS_OVERRIDES.setdefault(drug, set()).add("anticholinergic")

for drug in SEROTONERGIC_DRUGS:
    DRUG_CLASS_OVERRIDES.setdefault(drug, set()).add("serotonergic")

for drug in NSAIDS:
    DRUG_CLASS_OVERRIDES.setdefault(drug, set()).add("nsaid")

for drug in ANTICOAGULANTS:
    DRUG_CLASS_OVERRIDES.setdefault(drug, set()).add("anticoagulant")

KNOWN_DRUG_NAMES: set[str] = (
    set(DRUG_CLASS_OVERRIDES)
    | ANTICHOLINERGIC_DRUGS
    | SEROTONERGIC_DRUGS
    | NSAIDS
    | ANTICOAGULANTS
    | set(BRAND_TO_GENERIC)
    | set(BRAND_TO_GENERIC.values())
    | {
        "lisinopril",
        "enalapril",
        "ramipril",
        "benazepril",
        "captopril",
        "atorvastatin",
        "simvastatin",
        "rosuvastatin",
        "pravastatin",
        "lovastatin",
        "metformin",
        "spironolactone",
        "eplerenone",
        "losartan",
        "aspirin",
        "clopidogrel",
    }
)
