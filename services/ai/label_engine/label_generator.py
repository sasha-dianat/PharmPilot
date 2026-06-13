"""
Label Generator — Phase 23
===========================
Assembles structured LabelData from Rx, patient, and pharmacy records.
Resolves auxiliary (warning) labels based on drug class / SIG keywords.

PrintMode.HANDWRITTEN:
  No print command is sent. The workstation displays a "Handwriting Reference
  Sheet" — enlarged text layout the pharmacist reads while writing by hand.
  A `handwritten_by` staff ID is recorded in the audit log.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Print Mode
# ---------------------------------------------------------------------------

class PrintMode(str, Enum):
    THERMAL_PDF = "thermal_pdf"   # Browser PDF → thermal printer (Dymo, HP)
    THERMAL_ZPL = "thermal_zpl"   # ZPL II → Zebra LP2844 / GK420d / ZD420
    HANDWRITTEN  = "handwritten"  # Pharmacist writes by hand; no print sent


# ---------------------------------------------------------------------------
# Auxiliary (warning) label catalog
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AuxiliaryLabel:
    code: str
    text: str
    color_hex: str       # Standard pharmacy color coding
    icon: str            # Emoji / single char for workstation display

AUXILIARY_LABELS: dict[str, AuxiliaryLabel] = {
    "take_food":        AuxiliaryLabel("take_food",        "Take with food or milk",                "#FFA500", "🍽"),
    "avoid_alcohol":    AuxiliaryLabel("avoid_alcohol",    "Avoid alcohol",                          "#FF4444", "🚫"),
    "drowsiness":       AuxiliaryLabel("drowsiness",       "May cause drowsiness — use care",        "#FFCC00", "😴"),
    "avoid_sunlight":   AuxiliaryLabel("avoid_sunlight",   "Avoid prolonged sun exposure",           "#87CEEB", "☀"),
    "empty_stomach":    AuxiliaryLabel("empty_stomach",    "Take on empty stomach (1 hr before or 2 hr after meals)", "#90EE90", "⏱"),
    "do_not_crush":     AuxiliaryLabel("do_not_crush",     "Do not crush or chew",                   "#DEB887", "💊"),
    "refrigerate":      AuxiliaryLabel("refrigerate",      "Refrigerate — Do not freeze",            "#ADD8E6", "❄"),
    "shake_well":       AuxiliaryLabel("shake_well",       "Shake well before use",                  "#FFE4B5", "🔄"),
    "finish_all":       AuxiliaryLabel("finish_all",       "Finish all of this medication",          "#98FB98", "✅"),
    "blood_thinner":    AuxiliaryLabel("blood_thinner",    "Blood thinner — watch for unusual bleeding", "#FF6B6B", "⚠"),
    "no_grapefruit":    AuxiliaryLabel("no_grapefruit",    "Avoid grapefruit / grapefruit juice",    "#FF8C00", "🍊"),
    "take_with_water":  AuxiliaryLabel("take_with_water",  "Take with a full glass of water",        "#E0F7FA", "💧"),
    "diabetic":         AuxiliaryLabel("diabetic",         "Monitor blood sugar — report changes",   "#C8E6C9", "📊"),
    "do_not_stop":      AuxiliaryLabel("do_not_stop",      "Do not stop taking without prescriber approval", "#E8D5B7", "⛔"),
    "external_only":    AuxiliaryLabel("external_only",    "For external use only",                  "#F5F5DC", "🏷"),
    "eye_drops":        AuxiliaryLabel("eye_drops",        "For eye use only",                       "#E3F2FD", "👁"),
    "ear_drops":        AuxiliaryLabel("ear_drops",        "For ear use only",                       "#FFF9C4", "👂"),
    "controlled_cv":    AuxiliaryLabel("controlled_cv",    "C-V Controlled Substance",               "#FFD700", "Ⓒ"),
    "controlled_civ":   AuxiliaryLabel("controlled_civ",   "C-IV Controlled Substance",              "#FFA500", "Ⓒ"),
    "controlled_ciii":  AuxiliaryLabel("controlled_ciii",  "C-III Controlled Substance",             "#FF8C00", "Ⓒ"),
    "controlled_cii":   AuxiliaryLabel("controlled_cii",   "C-II Controlled Substance — No Refills", "#FF0000", "Ⓒ"),
}


# ---------------------------------------------------------------------------
# SIG / drug-class heuristics for automatic aux label selection
# ---------------------------------------------------------------------------

_AUX_SIG_RULES: list[tuple[re.Pattern, list[str]]] = [
    (re.compile(r"\bwith food\b|\bwith meal|\bafter eating", re.I), ["take_food"]),
    (re.compile(r"\bempty stomach\b|\bbefore meal|\bfasting\b", re.I), ["empty_stomach"]),
    (re.compile(r"\bshake\b", re.I), ["shake_well"]),
    (re.compile(r"\bophthalmic\b|\beye drop\b|\boptic\b", re.I), ["eye_drops"]),
    (re.compile(r"\botic\b|\bear drop\b", re.I), ["ear_drops"]),
    (re.compile(r"\btopical\b|\bapply to skin\b|\bcream\b|\bointment\b|\bgel\b", re.I), ["external_only"]),
    (re.compile(r"\bfinish\b|\bcomplete course\b|\bcomplete all\b|\bantibiotic\b", re.I), ["finish_all"]),
]

_AUX_DRUG_CLASS_RULES: dict[str, list[str]] = {
    # Sedatives / anxiolytics
    "alprazolam":    ["avoid_alcohol", "drowsiness"],
    "diazepam":      ["avoid_alcohol", "drowsiness"],
    "lorazepam":     ["avoid_alcohol", "drowsiness"],
    "clonazepam":    ["avoid_alcohol", "drowsiness"],
    "zolpidem":      ["avoid_alcohol", "drowsiness"],
    "eszopiclone":   ["avoid_alcohol", "drowsiness"],
    # Opioids
    "oxycodone":     ["avoid_alcohol", "drowsiness"],
    "hydrocodone":   ["avoid_alcohol", "drowsiness"],
    "codeine":       ["avoid_alcohol", "drowsiness"],
    "tramadol":      ["avoid_alcohol", "drowsiness"],
    "morphine":      ["avoid_alcohol", "drowsiness"],
    # Antibiotics
    "amoxicillin":   ["finish_all"],
    "azithromycin":  ["finish_all"],
    "ciprofloxacin": ["finish_all", "avoid_sunlight", "take_with_water"],
    "metronidazole": ["finish_all", "avoid_alcohol"],
    "levofloxacin":  ["finish_all", "avoid_sunlight"],
    # Anticoagulants
    "warfarin":      ["blood_thinner", "avoid_alcohol", "no_grapefruit"],
    "rivaroxaban":   ["blood_thinner", "take_food"],
    "apixaban":      ["blood_thinner"],
    # Statins (grapefruit)
    "simvastatin":   ["no_grapefruit"],
    "lovastatin":    ["no_grapefruit"],
    "atorvastatin":  ["no_grapefruit"],
    # Thyroid
    "levothyroxine": ["empty_stomach", "do_not_stop"],
    # Antidepressants / mood
    "fluoxetine":    ["avoid_alcohol", "do_not_stop"],
    "sertraline":    ["avoid_alcohol", "do_not_stop"],
    "venlafaxine":   ["avoid_alcohol", "do_not_stop"],
    # Insulin / diabetes
    "insulin":       ["refrigerate", "diabetic"],
    "metformin":     ["take_food", "diabetic"],
    # Bisphosphonates
    "alendronate":   ["empty_stomach", "take_with_water"],
    # Tetracyclines
    "doxycycline":   ["finish_all", "avoid_sunlight", "take_with_water"],
    "tetracycline":  ["finish_all", "avoid_sunlight", "take_with_water"],
    # Topicals with photosensitivity
    "tretinoin":     ["avoid_sunlight", "external_only"],
    "isotretinoin":  ["avoid_sunlight"],
    # Suspensions
    "amoxicillin suspension":   ["shake_well", "refrigerate", "finish_all"],
    "azithromycin suspension":  ["shake_well", "finish_all"],
}


def _resolve_aux_labels(drug_name: str, sig_text: str, dea_schedule: Optional[str]) -> list[str]:
    """Return list of auxiliary label codes for this Rx."""
    codes: list[str] = []
    seen: set[str] = set()

    drug_lower = (drug_name or "").lower()

    # DEA schedule first
    if dea_schedule:
        sched = dea_schedule.strip().upper()
        code_map = {"II": "controlled_cii", "III": "controlled_ciii",
                    "IV": "controlled_civ", "V": "controlled_cv"}
        for k, v in code_map.items():
            if k in sched and v not in seen:
                codes.append(v)
                seen.add(v)

    # Drug-name heuristics
    for key, label_codes in _AUX_DRUG_CLASS_RULES.items():
        if key in drug_lower:
            for c in label_codes:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)

    # SIG heuristics
    sig = sig_text or ""
    for pattern, label_codes in _AUX_SIG_RULES:
        if pattern.search(sig):
            for c in label_codes:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)

    return codes[:6]   # cap at 6 auxiliary labels per label spec


# ---------------------------------------------------------------------------
# LabelData dataclass
# ---------------------------------------------------------------------------

@dataclass
class LabelData:
    # --- Rx fields ---
    rx_number: str
    fill_number: int = 1
    fill_date: str = ""           # ISO date string

    # --- Drug fields ---
    drug_name: str = ""
    drug_strength: str = ""
    dosage_form: str = ""
    ndc11: str = ""
    quantity: float = 0
    days_supply: int = 0
    refills_remaining: int = 0
    sig_text: str = ""

    # --- Patient fields ---
    patient_last: str = ""
    patient_first: str = ""
    patient_dob_masked: str = ""  # MM/YYYY only — no full DOB on label

    # --- Prescriber fields ---
    prescriber_name: str = ""
    prescriber_npi: str = ""

    # --- Pharmacy fields ---
    pharmacy_name: str = ""
    pharmacy_address: str = ""
    pharmacy_city_state_zip: str = ""
    pharmacy_phone: str = ""
    pharmacy_npi: str = ""

    # --- Controlled substance ---
    is_controlled: bool = False
    dea_schedule: Optional[str] = None

    # --- Auxiliary labels ---
    auxiliary_labels: list[str] = field(default_factory=list)

    # --- Print mode + audit ---
    print_mode: PrintMode = PrintMode.THERMAL_PDF
    handwritten_by: Optional[str] = None   # staff_id when mode=HANDWRITTEN

    @property
    def patient_name_display(self) -> str:
        return f"{self.patient_last.upper()}, {self.patient_first}".strip(", ")

    @property
    def aux_label_objects(self) -> list[AuxiliaryLabel]:
        return [AUXILIARY_LABELS[c] for c in self.auxiliary_labels if c in AUXILIARY_LABELS]


# ---------------------------------------------------------------------------
# LabelGenerator
# ---------------------------------------------------------------------------

class LabelGenerator:
    """
    Assembles LabelData from raw Rx/patient/pharmacy dicts.

    Usage::

        gen = LabelGenerator()
        label = gen.from_records(
            rx=rx_dict,
            patient=patient_dict,
            pharmacy=pharmacy_dict,
            print_mode=PrintMode.HANDWRITTEN,
            handwritten_by="tech_01",
        )
    """

    def from_records(
        self,
        rx: dict,
        patient: dict,
        pharmacy: dict,
        print_mode: PrintMode = PrintMode.THERMAL_PDF,
        handwritten_by: Optional[str] = None,
        extra_aux_codes: Optional[list[str]] = None,
    ) -> LabelData:
        from datetime import date

        dob_raw = patient.get("date_of_birth", "")
        try:
            dob_dt = date.fromisoformat(str(dob_raw)[:10])
            dob_masked = dob_dt.strftime("%m/%Y")
        except Exception:
            dob_masked = "**/**"

        fill_date = rx.get("fill_date") or str(date.today())

        drug_name = rx.get("drug_name", "")
        sig_text  = rx.get("sig_text", "")
        dea_sched = rx.get("dea_schedule") or (rx.get("is_controlled") and "II") or None

        # Auto-resolve aux labels, then merge any explicitly requested ones
        auto_codes = _resolve_aux_labels(drug_name, sig_text, dea_sched)
        if extra_aux_codes:
            for c in extra_aux_codes:
                if c not in auto_codes and c in AUXILIARY_LABELS:
                    auto_codes.append(c)

        city   = pharmacy.get("city", "")
        state  = pharmacy.get("state", "")
        zipc   = pharmacy.get("zip_code", "")
        city_state_zip = f"{city}, {state} {zipc}".strip(", ")

        prescriber = rx.get("prescriber") or {}
        if isinstance(prescriber, str):
            prescriber_name = prescriber
            prescriber_npi  = ""
        else:
            p_first = prescriber.get("first_name", "")
            p_last  = prescriber.get("last_name", "")
            prescriber_name = f"Dr. {p_first} {p_last}".strip()
            prescriber_npi  = str(prescriber.get("npi", ""))

        return LabelData(
            rx_number           = str(rx.get("rx_number", "")),
            fill_number         = int(rx.get("fill_number", 1)),
            fill_date           = fill_date,
            drug_name           = drug_name,
            drug_strength       = rx.get("drug_strength", ""),
            dosage_form         = rx.get("dosage_form", ""),
            ndc11               = rx.get("ndc11", ""),
            quantity            = float(rx.get("quantity_prescribed", 0)),
            days_supply         = int(rx.get("days_supply", 0)),
            refills_remaining   = int(rx.get("refills_remaining", 0)),
            sig_text            = sig_text,
            patient_last        = patient.get("last_name", ""),
            patient_first       = patient.get("first_name", ""),
            patient_dob_masked  = dob_masked,
            prescriber_name     = prescriber_name,
            prescriber_npi      = prescriber_npi,
            pharmacy_name       = pharmacy.get("name", "PharmPilot Pharmacy"),
            pharmacy_address    = pharmacy.get("address_line1", ""),
            pharmacy_city_state_zip = city_state_zip,
            pharmacy_phone      = pharmacy.get("phone", ""),
            pharmacy_npi        = str(pharmacy.get("npi", "")),
            is_controlled       = bool(rx.get("is_controlled", False)),
            dea_schedule        = dea_sched,
            auxiliary_labels    = auto_codes,
            print_mode          = print_mode,
            handwritten_by      = handwritten_by,
        )
