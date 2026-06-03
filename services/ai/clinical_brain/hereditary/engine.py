"""
Hereditary / Familial Risk Engine.
==================================
Cross-references the PRESCRIBED drug against (a) the patient's own inherited
conditions and (b) inherited conditions documented in linked family members
(via the person-link graph), emitting council findings with explicit provenance.

Design goals:
  - Pure, deterministic, dependency-free → unit-testable without DB or models.
  - Every family-derived finding carries a "based on relative: <rel> — <cond>"
    provenance line (your point 1b).
  - Consent-gated: family cross-reference only runs when family_consent is True;
    the patient's OWN documented inherited status is always evaluated.
  - High Iranian relevance: G6PD deficiency / favism (very prevalent) drives an
    oxidant-drug contraindication rule; thalassemia and FH are also covered.
  - Safe language only; never diagnoses. AI proposes, pharmacist confirms.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HereditaryFinding:
    severity: str            # blocker | caution | counseling | monitoring
    condition: str           # the inherited condition driving the finding
    drug_name: str
    message: str
    provenance: list[str] = field(default_factory=list)   # ["self"] or ["relative: spouse — favism"]
    evidence_source: Optional[str] = None
    evidence_grade: Optional[str] = None


# ── Inherited-condition canonicalization ──────────────────────────────────────
# Maps many surface spellings (English + Persian) to a canonical condition key.
_CONDITION_SYNONYMS: dict[str, list[str]] = {
    "g6pd_deficiency": [
        "g6pd", "g-6-pd", "glucose-6-phosphate", "favism", "فاویسم", "g6pd deficiency",
        "کمبود g6pd", "نقص g6pd", "گلوکز ۶ فسفات",
    ],
    "hereditary_angioedema": [
        "hereditary angioedema", "hae", "c1 esterase", "c1-inhibitor deficiency",
        "آنژیوادم ارثی", "آنژیوادم",
    ],
    "familial_hypercholesterolemia": [
        "familial hypercholesterolemia", "fh ", "ldlr", "familial hyperlipidemia",
        "هایپرکلسترولمی خانوادگی", "کلسترول بالای ارثی",
    ],
    "long_qt_syndrome": [
        "long qt", "lqts", "congenital long qt", "romano-ward", "jervell",
        "سندرم کیوتی طولانی", "qt طولانی",
    ],
    "warfarin_sensitivity": [
        "warfarin sensitivity", "cyp2c9", "vkorc1", "bleeding on warfarin",
        "حساسیت به وارفارین",
    ],
    "thalassemia": [
        "thalassemia", "thalassaemia", "beta thal", "تالاسمی", "thalassemia minor",
    ],
    "malignant_hyperthermia": [
        "malignant hyperthermia", "ryr1", "هایپرترمی بدخیم",
    ],
    "porphyria": [
        "porphyria", "acute intermittent porphyria", "پورفیری",
    ],
}


def canonicalize_condition(text: str) -> Optional[str]:
    """Map a free-text condition (EN/FA) to a canonical inherited-condition key."""
    t = (text or "").lower().strip()
    for key, syns in _CONDITION_SYNONYMS.items():
        if any(s in t for s in syns):
            return key
    return None


# ── Drug → risk class membership ──────────────────────────────────────────────
# G6PD oxidant-stress drugs (hemolysis risk). Curated from WHO / NIH G6PD lists.
_G6PD_OXIDANT_DRUGS = {
    "primaquine", "tafenoquine", "dapsone", "rasburicase", "methylene blue",
    "methylthioninium", "nitrofurantoin", "sulfamethoxazole", "co-trimoxazole",
    "cotrimoxazole", "trimethoprim-sulfamethoxazole", "sulfadiazine", "sulfasalazine",
    "phenazopyridine", "quinine", "quinidine", "chloroquine", "primaquine",
    "nalidixic acid", "ciprofloxacin", "norfloxacin", "moxifloxacin", "glibenclamide",
    "glyburide", "menadione", "toluidine blue", "pegloticase",
}
# ACE inhibitors — contraindicated/avoid in hereditary angioedema.
_ACE_INHIBITORS = {
    "lisinopril", "enalapril", "ramipril", "captopril", "benazepril",
    "perindopril", "quinapril", "fosinopril", "trandolapril", "moexipril",
}
# QT-prolonging drugs (subset) — caution in congenital long-QT.
_QT_DRUGS = {
    "azithromycin", "clarithromycin", "erythromycin", "ciprofloxacin",
    "levofloxacin", "moxifloxacin", "haloperidol", "quetiapine", "ziprasidone",
    "amiodarone", "sotalol", "dronedarone", "ondansetron", "methadone",
    "citalopram", "escitalopram", "chlorpromazine", "thioridazine", "domperidone",
}
# Statins — relevant (reinforcing) when familial hypercholesterolemia present.
_STATINS = {
    "atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin",
    "pitavastatin", "fluvastatin",
}
# Porphyrinogenic drugs (subset) — avoid in acute porphyria.
_PORPHYRINOGENIC = {
    "barbiturate", "phenobarbital", "carbamazepine", "phenytoin", "rifampin",
    "sulfonamide", "griseofulvin", "ergot", "estrogen", "progesterone",
}
# Pharmacogenomic single-gene drug risks (patient's own genotype).
_PGX = {
    "codeine": ("CYP2D6", "Codeine activation varies by CYP2D6; ultrarapid metabolizers risk opioid toxicity.", "CPIC", "A"),
    "tramadol": ("CYP2D6", "Tramadol activation varies by CYP2D6; consider non-CYP2D6 analgesic if PM/UM.", "CPIC", "A"),
    "clopidogrel": ("CYP2C19", "Reduced activation in CYP2C19 poor metabolizers; consider alternative antiplatelet.", "CPIC", "A"),
    "abacavir": ("HLA-B*5701", "Test HLA-B*5701 before use — positive patients risk hypersensitivity.", "CPIC", "A"),
    "carbamazepine": ("HLA-B*1502", "HLA-B*1502 (esp. Asian ancestry) risks severe cutaneous reaction.", "CPIC", "A"),
    "allopurinol": ("HLA-B*5801", "HLA-B*5801 risks severe cutaneous reaction (SJS/TEN).", "CPIC", "A"),
}


def _drug_in(drug: str, drug_set: set[str]) -> bool:
    d = (drug or "").lower()
    return any(token in d for token in drug_set)


class HereditaryRiskEngine:
    """
    Evaluate a prescribed drug against inherited risks (self + family).

    patient:   {"inherited_conditions": [..], "diagnoses": [..], "pharmacogenomics": {...}|None}
    family:    [{"relationship": "spouse"|..|None, "diagnoses": [...], "inherited_conditions": [...]}]
    """

    def evaluate(
        self,
        drug_name: str,
        patient: dict,
        family: Optional[list[dict]] = None,
        family_consent: bool = True,
    ) -> list[HereditaryFinding]:
        findings: list[HereditaryFinding] = []
        drug = drug_name or ""

        # 1) Aggregate inherited conditions with provenance.
        #    self conditions always count; family only when consented.
        condition_provenance: dict[str, list[str]] = {}

        def add_condition(cond_key: Optional[str], prov: str):
            if not cond_key:
                return
            condition_provenance.setdefault(cond_key, [])
            if prov not in condition_provenance[cond_key]:
                condition_provenance[cond_key].append(prov)

        for raw in (patient.get("inherited_conditions", []) + patient.get("diagnoses", [])):
            add_condition(canonicalize_condition(raw), "self")

        if family_consent and family:
            for fp in family:
                rel = fp.get("relationship") or "relative"
                for raw in (fp.get("inherited_conditions", []) + fp.get("diagnoses", [])):
                    ck = canonicalize_condition(raw)
                    if ck:
                        add_condition(ck, f"relative: {rel} — {raw}")

        # 2) Apply rules per condition present.
        for cond, prov in condition_provenance.items():
            findings.extend(self._rule_for(cond, drug, prov, patient))

        # 3) Pharmacogenomic single-gene prompts (patient's own genotype/ancestry).
        for key, (gene, msg, src, grade) in _PGX.items():
            if key in drug.lower():
                has_pgx = bool(patient.get("pharmacogenomics"))
                findings.append(HereditaryFinding(
                    severity="counseling" if has_pgx else "monitoring",
                    condition=f"pharmacogenomic:{gene}",
                    drug_name=drug,
                    message=f"Pharmacogenomic consideration ({gene}): {msg}",
                    provenance=["self"],
                    evidence_source=src,
                    evidence_grade=grade,
                ))

        return findings

    # ── per-condition rules ───────────────────────────────────────────────────

    def _rule_for(self, cond: str, drug: str, prov: list[str], patient: dict) -> list[HereditaryFinding]:
        is_self = "self" in prov

        if cond == "g6pd_deficiency":
            if _drug_in(drug, _G6PD_OXIDANT_DRUGS):
                # If the PATIENT is known G6PD-deficient → blocker; if only family → caution.
                sev = "blocker" if is_self else "caution"
                return [HereditaryFinding(
                    severity=sev,
                    condition="G6PD deficiency / favism",
                    drug_name=drug,
                    message=(
                        f"Oxidant-drug consideration in G6PD deficiency: {drug} is a recognized "
                        "oxidant that may precipitate acute hemolysis in G6PD-deficient patients. "
                        + ("Patient's own G6PD-deficient status documented — consider prescriber "
                           "clarification for an alternative agent." if is_self else
                           "A linked relative has documented G6PD deficiency/favism — consider "
                           "confirming the patient's G6PD status before dispensing.")
                    ),
                    provenance=prov,
                    evidence_source="WHO/NIH G6PD oxidant-drug list",
                    evidence_grade="A",
                )]
            return []

        if cond == "hereditary_angioedema":
            if _drug_in(drug, _ACE_INHIBITORS):
                return [HereditaryFinding(
                    severity="blocker" if is_self else "caution",
                    condition="Hereditary angioedema",
                    drug_name=drug,
                    message=(
                        f"ACE-inhibitor consideration in hereditary angioedema: {drug} may precipitate "
                        "severe, potentially life-threatening angioedema. Consider prescriber "
                        "clarification for a non-ACE-inhibitor antihypertensive."
                    ),
                    provenance=prov,
                    evidence_source="HAE management guidelines",
                    evidence_grade="A",
                )]
            return []

        if cond == "long_qt_syndrome":
            if _drug_in(drug, _QT_DRUGS):
                return [HereditaryFinding(
                    severity="blocker" if is_self else "caution",
                    condition="Congenital long-QT syndrome",
                    drug_name=drug,
                    message=(
                        f"QT-prolongation consideration in congenital long-QT: {drug} prolongs the QT "
                        "interval and may increase torsades risk. Consider prescriber clarification "
                        "regarding ECG monitoring or an alternative agent."
                    ),
                    provenance=prov,
                    evidence_source="CredibleMeds / LQTS guidelines",
                    evidence_grade="A",
                )]
            return []

        if cond == "familial_hypercholesterolemia":
            if _drug_in(drug, _STATINS):
                return [HereditaryFinding(
                    severity="counseling",
                    condition="Familial hypercholesterolemia",
                    drug_name=drug,
                    message=(
                        f"Statin therapy ({drug}) is consistent with familial hypercholesterolemia "
                        "management. Reinforce adherence and consider LDL target review."
                    ),
                    provenance=prov,
                    evidence_source="FH management guidelines",
                    evidence_grade="B",
                )]
            return [HereditaryFinding(
                severity="counseling",
                condition="Familial hypercholesterolemia",
                drug_name=drug,
                message=(
                    "Inherited hypercholesterolemia context present in the family history; "
                    "lipid-management review may be warranted during counseling."
                ),
                provenance=prov,
                evidence_grade="C",
            )]

        if cond == "warfarin_sensitivity":
            if "warfarin" in drug.lower():
                return [HereditaryFinding(
                    severity="monitoring",
                    condition="Inherited warfarin sensitivity (CYP2C9/VKORC1)",
                    drug_name=drug,
                    message=(
                        "Family/genetic context of warfarin sensitivity — consider conservative "
                        "initiation and closer INR monitoring; pharmacogenomic testing may help."
                    ),
                    provenance=prov,
                    evidence_source="CPIC warfarin guideline",
                    evidence_grade="A",
                )]
            return []

        if cond == "porphyria":
            if _drug_in(drug, _PORPHYRINOGENIC):
                return [HereditaryFinding(
                    severity="caution",
                    condition="Acute porphyria",
                    drug_name=drug,
                    message=(
                        f"Porphyrinogenic-drug consideration: {drug} may trigger an acute porphyria "
                        "attack. Consider prescriber clarification for a porphyria-safe alternative."
                    ),
                    provenance=prov,
                    evidence_source="Porphyria drug-safety database",
                    evidence_grade="B",
                )]
            return []

        if cond == "thalassemia":
            return [HereditaryFinding(
                severity="counseling",
                condition="Thalassemia",
                drug_name=drug,
                message=(
                    "Thalassemia context present — avoid routine iron supplementation unless "
                    "iron deficiency is confirmed; counsel accordingly."
                ),
                provenance=prov,
                evidence_grade="C",
            )]

        return []
