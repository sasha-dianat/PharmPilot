from __future__ import annotations

from .attributes import DrugAttributes, EnzymeRole
from .severity import InteractionSeverity, magnitude_to_severity, step

_MAG = {
    ("strong", "high"): "~≥5× ↑ exposure",
    ("strong", "low"): "~2–5× ↑ exposure",
    ("moderate", "high"): "~2–5× ↑ exposure",
    ("moderate", "low"): "<2× ↑ exposure",
    ("weak", "high"): "<2× ↑ exposure",
    ("weak", "low"): "minimal change",
}
_INDUCE_MAG = {"strong": "~≥80% ↓ exposure", "moderate": "~50–80% ↓ exposure",
               "weak": "~20–50% ↓ exposure"}


def fm_bin(fm: float | None) -> str:
    return "high" if (fm is not None and fm >= 0.5) else "low"


def _substrate_of(victim: DrugAttributes, enzyme: str) -> EnzymeRole | None:
    for e in victim.enzymes:
        if e.role == "substrate" and e.enzyme == enzyme:
            return e
    return None


def metabolic_interaction(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    """Directional PK metabolic interaction perp→victim, or None if no shared enzyme."""
    for pe in perp.enzymes:
        if pe.role not in ("inhibitor", "inducer"):
            continue
        sub = _substrate_of(victim, pe.enzyme)
        if not sub:
            continue
        bin_ = fm_bin(sub.fm)
        strength = pe.strength or "moderate"
        inhibits = pe.role == "inhibitor"
        prodrug = victim.prodrug and sub.yields == "active"
        if inhibits:
            direction = "efficacy_loss" if prodrug else "toxicity"
            base = magnitude_to_severity(strength, bin_)
            magnitude = _MAG.get((strength, bin_), "uncertain")
        else:  # inducer
            direction = "toxicity" if prodrug else "efficacy_loss"
            base = InteractionSeverity.MAJOR if bin_ == "high" and strength == "strong" \
                else InteractionSeverity.MODERATE if bin_ == "high" \
                else InteractionSeverity.MINOR
            magnitude = _INDUCE_MAG.get(strength, "uncertain")
        sev = base
        factors = []
        if victim.nti.is_nti:
            sev = step(sev, +1)
            factors.append(f"{victim.ingredient} is narrow-therapeutic-index")
        onset = None
        if pe.inhibition_type == "mechanism_based":
            onset = "time-dependent inhibition; effect persists days after stopping"
        elif pe.role == "inducer" and perp.induction_offset_days:
            onset = f"induction onset/offset ~{perp.induction_offset_days} d"
        return {
            "type": "drug_drug",
            "direction": direction,
            "base_severity": base,
            "severity": sev,
            "predicted_magnitude": magnitude,
            "onset_offset": onset,
            "mechanism_basis": f"{pe.enzyme} {pe.role} ({strength}) of substrate fm={sub.fm}",
            "patient_specific_factors": factors,
            "enzyme": pe.enzyme,
        }
    return None


def phenoconversion(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    if not victim.pgx_enzyme:
        return None
    for pe in perp.enzymes:
        if pe.role == "inhibitor" and pe.strength == "strong" and pe.enzyme == victim.pgx_enzyme:
            return {
                "type": "drug_drug",
                "direction": "efficacy_loss" if victim.prodrug else "toxicity",
                "base_severity": InteractionSeverity.MAJOR,
                "severity": InteractionSeverity.MAJOR,
                "predicted_magnitude": "functional poor-metabolizer phenotype",
                "onset_offset": None,
                "mechanism_basis": f"phenoconversion via strong {pe.enzyme} inhibition",
                "patient_specific_factors": [],
            }
    return None


def transporter_interaction(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    for pt in perp.transporters:
        if pt.role != "inhibitor":
            continue
        for vt in victim.transporters:
            if vt.role == "substrate" and vt.name == pt.name:
                return {
                    "type": "drug_drug", "direction": "toxicity",
                    "base_severity": InteractionSeverity.MODERATE,
                    "severity": step(InteractionSeverity.MODERATE, +1) if victim.nti.is_nti
                    else InteractionSeverity.MODERATE,
                    "predicted_magnitude": None, "onset_offset": None,
                    "mechanism_basis": f"{pt.name} inhibition ({vt.organ or 'transport'}) of {victim.ingredient}",
                    "patient_specific_factors": (
                        [f"{victim.ingredient} is narrow-therapeutic-index"] if victim.nti.is_nti else []),
                }
    return None


def absorption_interaction(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    if victim.absorption and victim.absorption.chelation_cations and perp.provides_cations:
        if set(perp.provides_cations) & set(victim.absorption.chelation_cations):
            hrs = victim.absorption.separation_hours or 2
            return {
                "type": "drug_drug", "direction": "efficacy_loss",
                "base_severity": InteractionSeverity.MODERATE, "severity": InteractionSeverity.MODERATE,
                "predicted_magnitude": "reduced absorption", "onset_offset": None,
                "mechanism_basis": "polyvalent-cation chelation",
                "action": f"Separate administration by {hrs} h.",
                "patient_specific_factors": [],
            }
    if victim.absorption and victim.absorption.ph_dependent == "acid_requiring" and perp.absorption_suppressant_ph:
        return {
            "type": "drug_drug", "direction": "efficacy_loss",
            "base_severity": InteractionSeverity.MODERATE, "severity": InteractionSeverity.MODERATE,
            "predicted_magnitude": "reduced absorption (raised gastric pH)", "onset_offset": None,
            "mechanism_basis": "pH-dependent absorption", "patient_specific_factors": [],
        }
    return None


def renal_competition(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    if victim.ingredient in perp.reduces_renal_clearance_of and victim.nti.is_nti:
        return {
            "type": "drug_drug", "direction": "toxicity",
            "base_severity": InteractionSeverity.MAJOR,
            "severity": step(InteractionSeverity.MAJOR, +1),    # NTI
            "predicted_magnitude": "reduced renal clearance", "onset_offset": None,
            "mechanism_basis": "competition / reduced GFR lowering renal elimination",
            "patient_specific_factors": [f"{victim.ingredient} is narrow-therapeutic-index"],
        }
    return None
