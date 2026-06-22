from __future__ import annotations

from .attributes import DrugAttributes
from .severity import InteractionSeverity

_DIRECTION = {"additive": "additive_risk", "synergistic": "additive_risk",
              "antagonistic": "opposition"}


def pd_interactions(a: DrugAttributes, b: DrugAttributes, *, labs: dict | None = None) -> list[dict]:
    out = []
    shared = set(a.pd) & set(b.pd)
    for axis in shared:
        ax_a, ax_b = a.pd[axis], b.pd[axis]
        # MAOI + serotonergic → contraindicated
        if axis == "serotonergic" and "maoi" in {ax_a.subtype, ax_b.subtype}:
            out.append({"axis": axis, "direction": "additive_risk",
                        "severity": InteractionSeverity.CONTRAINDICATED,
                        "mechanism": "MAOI with serotonergic agent: hypertensive/serotonin crisis."})
            continue
        # QT conditional gating
        if axis == "qt":
            tiers = {ax_a.tier, ax_b.tier}
            electrolyte_risk = bool(labs and (labs.get("potassium", 9) < 3.5))
            if "conditional" in tiers and not electrolyte_risk and "known" not in tiers:
                out.append({"axis": axis, "direction": "additive_risk",
                            "severity": InteractionSeverity.MINOR,
                            "mechanism": "Conditional QT risk; relevant only with hypokalemia/level rise."})
                continue
            out.append({"axis": axis, "direction": "additive_risk",
                        "severity": InteractionSeverity.MAJOR,
                        "mechanism": "Additive QT prolongation; torsades risk."})
            continue
        direction = _DIRECTION.get(ax_a.direction, "additive_risk")
        sev = InteractionSeverity.MODERATE
        if axis == "cns_depression" and "synergistic" in {ax_a.direction, ax_b.direction}:
            sev = InteractionSeverity.MAJOR
        out.append({"axis": axis, "direction": direction, "severity": sev,
                    "mechanism": f"{'Opposing' if direction == 'opposition' else 'Additive'} "
                                 f"{axis.replace('_', ' ')} effect."})
    return out
