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
