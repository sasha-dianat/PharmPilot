"""
#10 — Compounding Ingredient Compatibility Intelligence  (offline-first)
========================================================================
Flags chemical/physical incompatibilities, beyond-use-date (BUD) conflicts, and
dose-range errors across a compound's ingredient list. Layered ON TOP of the
existing clinical-brain drug–drug interaction (DDI) module — it does not
duplicate it.

LOCAL BRAIN (always available):
  • DDI pairs: query the existing interaction source for known incompatible pairs
    (reuses the platform's trainable DDI module / graph when reachable; otherwise
    a local fallback rule set).
  • Local compatibility RULES:
      - acid + base buffers → precipitation risk
      - oxidizer + reducer → degradation
      - shortest-ingredient BUD wins → flag if the formula BUD exceeds it
      - per-kg dose-range check vs patient weight
  • Reference compatibility charts via the trainable Reference Corpus (§1.5),
    retrievable offline.

CLOUD BRAIN (when online):
  • LLM literature synthesis for rare/novel combinations; latest USP chart refresh.
  Offline → local graph + rules + cached charts, degraded=true.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, generate, ReferenceCorpus,
)

logger = logging.getLogger(__name__)

SERVICE = "compounding_compat"
CORPUS_NAMESPACE = "compounding_compat"

# ─── Local rule knowledge (coarse but always available) ───────────────────────

# Coarse chemical class hints by ingredient name fragment.
_ACIDS = {"acid", "ascorbic", "citric", "salicylic", "lactic", "benzoic", "hcl", "hydrochloride"}
_BASES = {"sodium bicarbonate", "bicarbonate", "hydroxide", "carbonate", "magnesium oxide",
          "calcium carbonate", "trometamol", "tromethamine"}
_OXIDIZERS = {"benzoyl peroxide", "peroxide", "hypochlorite", "permanganate", "iodine"}
_REDUCERS = {"ascorbic", "sodium metabisulfite", "metabisulfite", "thioglycolate", "sulfite"}
_INCOMPATIBLE_PAIRS = [
    ({"benzoyl peroxide"}, {"tretinoin"}, "Benzoyl peroxide oxidises tretinoin — separate or avoid co-formulation."),
    ({"erythromycin"}, {"benzoyl peroxide"}, "Erythromycin degrades with benzoyl peroxide unless freshly compounded."),
    ({"metronidazole"}, {"aluminum"}, "Metronidazole may be incompatible with aluminium-containing bases."),
]


@dataclass
class Ingredient:
    name:     str
    quantity: Optional[float] = None
    unit:     str = ""
    bud_days: Optional[int] = None


@dataclass
class CompatFinding:
    severity:  str            # critical | warning | info
    kind:      str            # ddi | precipitation | oxidation | bud | dose | reference
    ingredients: list[str]
    message:   str
    source:    str            # graph | rule | reference | llm


def _classes(name: str) -> set[str]:
    low = name.lower()
    tags = set()
    if any(a in low for a in _ACIDS):     tags.add("acid")
    if any(b in low for b in _BASES):     tags.add("base")
    if any(o in low for o in _OXIDIZERS): tags.add("oxidizer")
    if any(r in low for r in _REDUCERS):  tags.add("reducer")
    return tags


def _local_rule_findings(ings: list[Ingredient]) -> list[CompatFinding]:
    findings: list[CompatFinding] = []
    names = [i.name for i in ings]
    classes = {i.name: _classes(i.name) for i in ings}

    # Pairwise class clashes.
    for a in range(len(ings)):
        for b in range(a + 1, len(ings)):
            na, nb = ings[a].name, ings[b].name
            ca, cb = classes[na], classes[nb]
            if ("acid" in ca and "base" in cb) or ("base" in ca and "acid" in cb):
                findings.append(CompatFinding(
                    "warning", "precipitation", [na, nb],
                    f"Acid/base combination ({na} + {nb}) — precipitation / neutralisation risk. Verify pH and order of mixing.",
                    "rule"))
            if ("oxidizer" in ca and "reducer" in cb) or ("reducer" in ca and "oxidizer" in cb):
                findings.append(CompatFinding(
                    "warning", "oxidation", [na, nb],
                    f"Oxidizer/reducer combination ({na} + {nb}) — accelerated degradation risk.",
                    "rule"))

    # Named incompatible pairs.
    low_names = [n.lower() for n in names]
    for set_a, set_b, msg in _INCOMPATIBLE_PAIRS:
        has_a = any(any(k in ln for k in set_a) for ln in low_names)
        has_b = any(any(k in ln for k in set_b) for ln in low_names)
        if has_a and has_b:
            findings.append(CompatFinding("critical", "ddi", names, msg, "rule"))

    # BUD: shortest ingredient BUD bounds the formula.
    buds = [(i.name, i.bud_days) for i in ings if i.bud_days is not None]
    if buds:
        shortest_name, shortest = min(buds, key=lambda x: x[1])
        findings.append(CompatFinding(
            "info", "bud", [shortest_name],
            f"Formula beyond-use date is bounded by {shortest_name} (~{shortest} days). "
            "Do not assign a BUD longer than the shortest-stability ingredient.",
            "rule"))
    return findings


async def _ddi_graph_findings(ings: list[Ingredient]) -> list[CompatFinding]:
    """Query the existing DDI source for known incompatible pairs. Best-effort."""
    findings: list[CompatFinding] = []
    try:
        # Reuse the platform graph if reachable.
        from services.graph.drug_interaction_graph import DrugInteractionGraph  # type: ignore
        graph = DrugInteractionGraph()
        names = [i.name for i in ings]
        for a in range(len(names)):
            for b in range(a + 1, len(names)):
                try:
                    inter = graph.check_interaction(names[a], names[b])  # type: ignore
                    if inter:
                        findings.append(CompatFinding(
                            "critical" if getattr(inter, "severity", "") in ("major", "contraindicated") else "warning",
                            "ddi", [names[a], names[b]],
                            getattr(inter, "description", f"Interaction between {names[a]} and {names[b]}."),
                            "graph"))
                except Exception:  # noqa: BLE001
                    continue
    except Exception as exc:  # noqa: BLE001 — graph offline / unavailable is expected
        logger.debug("[compounding_compat] DDI graph unavailable (%s)", exc)
    return findings


def _dose_findings(ings: list[Ingredient], patient_weight_kg: Optional[float]) -> list[CompatFinding]:
    findings: list[CompatFinding] = []
    if not patient_weight_kg or patient_weight_kg <= 0:
        return findings
    # Very coarse: flag implausibly high per-kg amounts (placeholder for real ranges).
    for i in ings:
        if i.quantity and i.unit.lower() in ("mg", "milligram"):
            per_kg = i.quantity / patient_weight_kg
            if per_kg > 100:  # extreme threshold
                findings.append(CompatFinding(
                    "warning", "dose", [i.name],
                    f"{i.name}: {i.quantity}{i.unit} is {per_kg:.0f} mg/kg for a "
                    f"{patient_weight_kg:g} kg patient — verify against the therapeutic range.",
                    "rule"))
    return findings


async def analyze(
    *,
    ingredients: list[dict],
    patient_weight_kg: Optional[float] = None,
    formula_bud_days: Optional[int] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Analyze a compound's ingredient list. §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    ings = [Ingredient(
        name=str(i.get("name", "")).strip(),
        quantity=i.get("quantity"),
        unit=str(i.get("unit", "")),
        bud_days=i.get("bud_days"),
    ) for i in ingredients if i.get("name")]

    if len(ings) < 1:
        return build_envelope(
            {"findings": [], "summary": {"critical": 0, "warning": 0, "info": 0}},
            tier_used=tier, confidence=0.3, degraded=(tier == Tier.LOCAL),
            options_active=["local_rules"], options_offline=[],
            model_version="compounding_compat_v1",
        )

    findings: list[CompatFinding] = []
    findings += _local_rule_findings(ings)
    findings += await _ddi_graph_findings(ings)
    findings += _dose_findings(ings, patient_weight_kg)

    # Formula BUD vs shortest ingredient BUD.
    buds = [i.bud_days for i in ings if i.bud_days is not None]
    if formula_bud_days and buds and formula_bud_days > min(buds):
        findings.append(CompatFinding(
            "critical", "bud", [i.name for i in ings if i.bud_days == min(buds)],
            f"Assigned formula BUD ({formula_bud_days}d) exceeds the shortest-stability "
            f"ingredient ({min(buds)}d). Reduce the BUD.", "rule"))

    options_active  = ["local_rules", "ddi_graph", "reference_charts"]
    options_offline: list[str] = []
    degraded = (tier == Tier.LOCAL)

    # Reference corpus (offline-capable RAG).
    try:
        corpus = ReferenceCorpus(CORPUS_NAMESPACE)
        query = " ".join(i.name for i in ings[:3]) + " compatibility"
        for h in corpus.retrieve(query, k=2):
            findings.append(CompatFinding(
                "info", "reference", [i.name for i in ings],
                h.chunk, "reference"))
    except Exception:  # noqa: BLE001
        options_offline.append("reference_charts")

    # Cloud literature synthesis for novel combos.
    if tier in (Tier.CLOUD, Tier.HYBRID) and len(ings) >= 2:
        names = ", ".join(i.name for i in ings)
        try:
            res = await generate(
                f"Compounding ingredients: {names}. List any known physical or chemical "
                "incompatibilities in one or two short bullet points. If none known, say so.",
                system="You are a compounding pharmacist. Be precise and brief.",
                max_tokens=160, temperature=0.1, phi=False, task="clinical",
            )
            if res.text.strip() and not res.degraded:
                findings.append(CompatFinding("info", "reference", [i.name for i in ings],
                                              res.text.strip(), "llm"))
                options_active.append("literature_synthesis")
                tier = Tier.HYBRID
            elif res.degraded:
                degraded = True
                options_offline.append("literature_synthesis")
        except Exception:  # noqa: BLE001
            options_offline.append("literature_synthesis")
    else:
        options_offline.append("literature_synthesis")

    summary = {
        "critical": sum(1 for f in findings if f.severity == "critical"),
        "warning":  sum(1 for f in findings if f.severity == "warning"),
        "info":     sum(1 for f in findings if f.severity == "info"),
    }
    confidence = 0.75 if not degraded else 0.65
    # De-dup options_offline
    options_offline = sorted(set(options_offline))

    return build_envelope(
        {"findings": [f.__dict__ for f in findings], "summary": summary,
         "ingredient_count": len(ings)},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="compounding_compat_v1",
    )
