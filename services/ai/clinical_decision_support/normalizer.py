"""Deterministic medication normalization for the CDS vertical slice."""

from __future__ import annotations

import re


BRAND_TO_GENERIC: dict[str, str] = {
    "biaxin": "clarithromycin",
    "zocor": "simvastatin",
    "prinivil": "lisinopril",
    "zestril": "lisinopril",
    "vasotec": "enalapril",
    "altace": "ramipril",
    "aldactone": "spironolactone",
    "inspra": "eplerenone",
    "glucophage": "metformin",
    "coumadin": "warfarin",
    "jantoven": "warfarin",
    "eliquis": "apixaban",
    "xarelto": "rivaroxaban",
    "pradaxa": "dabigatran",
    "lovenox": "enoxaparin",
    "advil": "ibuprofen",
    "motrin": "ibuprofen",
    "aleve": "naproxen",
    "celebrex": "celecoxib",
    "valium": "diazepam",
    "xanax": "alprazolam",
    "ativan": "lorazepam",
    "klonopin": "clonazepam",
    "lexapro": "escitalopram",
    "zoloft": "sertraline",
    "prozac": "fluoxetine",
    "paxil": "paroxetine",
    "celexa": "citalopram",
    "ultram": "tramadol",
}

GENERIC_CLASSES: dict[str, set[str]] = {
    "simvastatin": {"statin"},
    "atorvastatin": {"statin"},
    "lovastatin": {"statin"},
    "rosuvastatin": {"statin"},
    "pravastatin": {"statin"},
    "ibuprofen": {"nsaid"},
    "naproxen": {"nsaid"},
    "diclofenac": {"nsaid"},
    "meloxicam": {"nsaid"},
    "celecoxib": {"nsaid"},
    "warfarin": {"anticoagulant"},
    "apixaban": {"anticoagulant"},
    "rivaroxaban": {"anticoagulant"},
    "dabigatran": {"anticoagulant"},
    "edoxaban": {"anticoagulant"},
    "enoxaparin": {"anticoagulant"},
    "heparin": {"anticoagulant"},
    "alprazolam": {"benzodiazepine"},
    "clonazepam": {"benzodiazepine"},
    "diazepam": {"benzodiazepine"},
    "lorazepam": {"benzodiazepine"},
    "temazepam": {"benzodiazepine"},
    "oxazepam": {"benzodiazepine"},
    "sertraline": {"ssri"},
    "fluoxetine": {"ssri"},
    "paroxetine": {"ssri"},
    "citalopram": {"ssri"},
    "escitalopram": {"ssri"},
    "fluvoxamine": {"ssri"},
    "lisinopril": {"ace_inhibitor"},
    "enalapril": {"ace_inhibitor"},
    "ramipril": {"ace_inhibitor"},
    "benazepril": {"ace_inhibitor"},
    "captopril": {"ace_inhibitor"},
    "quinapril": {"ace_inhibitor"},
    "spironolactone": {"potassium_sparing_diuretic"},
    "eplerenone": {"potassium_sparing_diuretic"},
    "clarithromycin": {"macrolide_cyp3a4_inhibitor"},
    "erythromycin": {"macrolide_cyp3a4_inhibitor"},
    "metformin": {"biguanide"},
    "tramadol": {"serotonergic_opioid"},
    "amoxicillin": {"penicillin"},
    "ampicillin": {"penicillin"},
}


def normalize(name: str | None) -> str:
    if not name:
        return ""
    cleaned = re.sub(r"[^a-zA-Z0-9\s-]", " ", name).lower()
    cleaned = re.sub(r"\b(tablet|tab|capsule|cap|oral|solution|mg|mcg|ml|er|xr|sr)\b", " ", cleaned)
    # Drop strength/dose tokens (e.g. "25mg", "10", "0.5") — a drug name never
    # starts with a digit, but a strength suffix does. Without this, an
    # unrecognized drug like "diphenhydramine 25mg" normalizes to
    # "diphenhydramine 25mg" instead of "diphenhydramine", so downstream
    # exact-name lookups (e.g. the anticholinergic-burden table) miss.
    tokens = [token for token in re.split(r"[\s/-]+", cleaned) if token and not token[0].isdigit()]
    for token in tokens:
        if token in BRAND_TO_GENERIC:
            return BRAND_TO_GENERIC[token]
        if token in GENERIC_CLASSES:
            return token
    compact = " ".join(tokens)
    return BRAND_TO_GENERIC.get(compact, compact)


def classes_of(name: str | None) -> set[str]:
    return set(GENERIC_CLASSES.get(normalize(name), set()))
