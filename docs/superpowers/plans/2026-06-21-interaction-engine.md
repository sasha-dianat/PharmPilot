# Interaction Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, offline, mechanism-aware drug-interaction engine that, given a patient + the Rx under review, returns a severity-graded `InteractionReport` spanning current Rx × active × historical meds × conditions.

**Architecture:** Extend `services/ai/clinical_decision_support/`. Two in-memory indexes load at startup from versioned YAML: a per-drug PK/PD **attribute index** and a curated **rule index**. `build_review_set` assembles the patient's med/condition universe from the DB; `evaluate()` runs explicit rules + mechanistic inference (PK metabolic/transporter/absorption/renal + PD), computes direction-of-effect and a predicted-magnitude→severity lookup with deterministic modifiers, applies a recency cutoff, dedups with `curated > ddinter > inferred` precedence, and returns the report. No network, no LLM in the evaluate path; severity is always a table lookup.

**Tech Stack:** Python 3.12, dataclasses, PyYAML 6.0.3, FastAPI, async SQLAlchemy, pytest. Run python via `/Users/sashad85/miniforge3/bin/python`.

**Spec:** `docs/superpowers/specs/2026-06-21-interaction-engine-design.md`

---

## File structure

```
services/ai/clinical_decision_support/
  interaction/                      # new sub-package for the engine
    __init__.py
    severity.py                     # InteractionSeverity enum, normalize, step, magnitude matrix
    attributes.py                   # DrugAttributes dataclasses + AttributeIndex loader
    rules.py                        # InteractionRule dataclass + RuleIndex loader (curated)
    report.py                       # Finding, InteractionReport dataclasses
    mechanism.py                    # direction-of-effect + magnitude→severity + modifiers (pure)
    review_set.py                   # ReviewMed, Condition, ReviewSet + build_review_set (DB)
    engine.py                       # evaluate(review_set, rules, attrs) -> InteractionReport
    data/
      drug_attributes.yaml          # per-drug PK/PD seed
      interaction_rules.yaml        # curated explicit rules (migrated 6 + drug-disease)
services/platform/routers/cds.py    # add POST /cds/interaction-report
tests/unit/
  test_interaction_severity.py
  test_interaction_attributes.py
  test_interaction_rules.py
  test_interaction_mechanism.py
  test_interaction_engine.py
  test_interaction_report_endpoint.py
```

Note: the existing `clinical_decision_support/{schema,rules,normalizer,engine}.py` and
`/cds/evaluate` stay untouched (backward compat). The new work lives under the
`interaction/` sub-package. The legacy `normalizer.normalize`/`classes_of` are reused.

---

## Task 1: Severity model

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/__init__.py` (empty)
- Create: `services/ai/clinical_decision_support/interaction/severity.py`
- Test: `tests/unit/test_interaction_severity.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_severity.py
from services.ai.clinical_decision_support.interaction.severity import (
    InteractionSeverity as S, normalize_severity, step, magnitude_to_severity,
)


def test_ordering_and_rank():
    assert S.CONTRAINDICATED.rank > S.MAJOR.rank > S.MODERATE.rank > S.MINOR.rank


def test_normalize_legacy_and_ddinter():
    assert normalize_severity("CRITICAL") is S.MAJOR          # not flagged contraindicated
    assert normalize_severity("CRITICAL", contraindicated=True) is S.CONTRAINDICATED
    assert normalize_severity("HIGH") is S.MAJOR
    assert normalize_severity("MODERATE") is S.MODERATE
    assert normalize_severity("INFO") is S.MINOR
    assert normalize_severity("Major") is S.MAJOR             # DDInter / curated already-canonical


def test_step_up_and_down_caps():
    assert step(S.MAJOR, +1) is S.CONTRAINDICATED
    assert step(S.CONTRAINDICATED, +1) is S.CONTRAINDICATED   # capped
    assert step(S.MINOR, -1) is S.MINOR                       # floored
    assert step(S.MAJOR, -1) is S.MODERATE


def test_magnitude_matrix():
    assert magnitude_to_severity("strong", "high") is S.MAJOR
    assert magnitude_to_severity("strong", "low") is S.MODERATE
    assert magnitude_to_severity("moderate", "high") is S.MODERATE
    assert magnitude_to_severity("weak", "low") is S.MINOR
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_severity.py -q`
Expected: FAIL — `ModuleNotFoundError: ...interaction.severity`

- [ ] **Step 3: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/severity.py
from __future__ import annotations

from enum import Enum


class InteractionSeverity(str, Enum):
    CONTRAINDICATED = "Contraindicated"
    MAJOR = "Major"
    MODERATE = "Moderate"
    MINOR = "Minor"

    @property
    def rank(self) -> int:
        return {"Minor": 0, "Moderate": 1, "Major": 2, "Contraindicated": 3}[self.value]


_ORDER = [
    InteractionSeverity.MINOR,
    InteractionSeverity.MODERATE,
    InteractionSeverity.MAJOR,
    InteractionSeverity.CONTRAINDICATED,
]

_LEGACY = {
    "CRITICAL": InteractionSeverity.MAJOR,
    "HIGH": InteractionSeverity.MAJOR,
    "MODERATE": InteractionSeverity.MODERATE,
    "LOW": InteractionSeverity.MINOR,
    "INFO": InteractionSeverity.MINOR,
}


def normalize_severity(raw: str, *, contraindicated: bool = False) -> InteractionSeverity:
    if contraindicated:
        return InteractionSeverity.CONTRAINDICATED
    key = (raw or "").strip()
    try:
        return InteractionSeverity(key.title())        # already canonical ("Major")
    except ValueError:
        return _LEGACY.get(key.upper(), InteractionSeverity.MINOR)


def step(sev: InteractionSeverity, delta: int) -> InteractionSeverity:
    idx = max(0, min(len(_ORDER) - 1, _ORDER.index(sev) + delta))
    return _ORDER[idx]


# inhibitor/inducer strength × victim fm-bin → base severity (spec §2)
_MATRIX = {
    ("strong", "high"): InteractionSeverity.MAJOR,
    ("strong", "low"): InteractionSeverity.MODERATE,
    ("moderate", "high"): InteractionSeverity.MODERATE,
    ("moderate", "low"): InteractionSeverity.MINOR,
    ("weak", "high"): InteractionSeverity.MINOR,
    ("weak", "low"): InteractionSeverity.MINOR,
}


def magnitude_to_severity(strength: str, fm_bin: str) -> InteractionSeverity:
    return _MATRIX.get((strength, fm_bin), InteractionSeverity.MINOR)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_severity.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/__init__.py \
        services/ai/clinical_decision_support/interaction/severity.py \
        tests/unit/test_interaction_severity.py
git commit -m "feat(cds): interaction severity model (canonical scale + magnitude matrix)"
```

---

## Task 2: Drug attribute dataclasses + loader + seed

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/attributes.py`
- Create: `services/ai/clinical_decision_support/interaction/data/drug_attributes.yaml`
- Test: `tests/unit/test_interaction_attributes.py`

- [ ] **Step 1: Write the seed YAML** (small, high-yield; grows via sub-project #3)

```yaml
# services/ai/clinical_decision_support/interaction/data/drug_attributes.yaml
- ingredient: clopidogrel
  classes: [antiplatelet]
  prodrug: true
  activating_enzyme: CYP2C19
  pgx_enzyme: CYP2C19
  enzymes:
    - { enzyme: CYP2C19, role: substrate, fm: 0.5, yields: active }
  elimination_route: hepatic
  pd: { bleeding: { strength: moderate, direction: additive } }

- ingredient: omeprazole
  classes: [ppi]
  enzymes:
    - { enzyme: CYP2C19, role: inhibitor, strength: moderate, inhibition_type: reversible }
  absorption_suppressant: { ph: true }   # raises gastric pH

- ingredient: clarithromycin
  classes: [macrolide]
  enzymes:
    - { enzyme: CYP3A4, role: inhibitor, strength: strong, inhibition_type: mechanism_based }
  transporters: [{ name: P-gp, role: inhibitor, strength: moderate }]
  pd: { qt: { tier: conditional } }

- ingredient: simvastatin
  classes: [statin]
  enzymes: [{ enzyme: CYP3A4, role: substrate, fm: 0.8, yields: parent }]
  transporters: [{ name: OATP1B1, role: substrate, organ: hepatic_uptake }]
  nti: { is_nti: true, consequence: toxicity }

- ingredient: rifampin
  classes: [rifamycin]
  enzymes: [{ enzyme: CYP3A4, role: inducer, strength: strong }]
  induction: { onset_days: 7, offset_days: 14 }

- ingredient: warfarin
  classes: [anticoagulant]
  enzymes: [{ enzyme: CYP2C9, role: substrate, fm: 0.9, yields: parent }]
  nti: { is_nti: true, consequence: toxicity }
  pd: { bleeding: { strength: high, direction: additive } }

- ingredient: codeine
  classes: [opioid]
  prodrug: true
  activating_enzyme: CYP2D6
  pgx_enzyme: CYP2D6
  enzymes: [{ enzyme: CYP2D6, role: substrate, fm: 0.1, yields: active }]
  pd: { cns_depression: { strength: moderate, direction: synergistic } }

- ingredient: paroxetine
  classes: [ssri]
  enzymes: [{ enzyme: CYP2D6, role: inhibitor, strength: strong, inhibition_type: mechanism_based }]
  pd: { serotonergic: { subtype: sri }, bleeding: { strength: moderate, direction: additive } }

- ingredient: phenelzine
  classes: [maoi]
  pd: { serotonergic: { subtype: maoi } }

- ingredient: levothyroxine
  classes: [thyroid_hormone]
  absorption: { chelation_cations: [Ca, Mg, Al, Fe], separation_hours: 4 }

- ingredient: calcium carbonate
  classes: [antacid, mineral]
  provides_cations: [Ca]

- ingredient: lithium
  classes: [mood_stabilizer]
  elimination_route: renal
  nti: { is_nti: true, consequence: toxicity }

- ingredient: ibuprofen
  classes: [nsaid]
  reduces_renal_clearance_of: [lithium, methotrexate]
  pd: { bleeding: { strength: moderate, direction: additive },
        nephrotoxic: { strength: moderate, direction: additive },
        antihypertensive_opposition: { direction: antagonistic } }

- ingredient: amiodarone
  classes: [antiarrhythmic]
  enzymes: [{ enzyme: CYP3A4, role: inhibitor, strength: moderate },
            { enzyme: CYP2D6, role: inhibitor, strength: moderate }]
  pd: { qt: { tier: known } }
  nti: { is_nti: true, consequence: toxicity }
  long_acting: true

- ingredient: digoxin
  classes: [cardiac_glycoside]
  transporters: [{ name: P-gp, role: substrate, organ: renal_secretion }]
  elimination_route: renal
  nti: { is_nti: true, consequence: toxicity }
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_interaction_attributes.py
from services.ai.clinical_decision_support.interaction.attributes import (
    load_attribute_index, AttributeIndex,
)


def test_index_loads_seed():
    idx = load_attribute_index()
    assert isinstance(idx, AttributeIndex)
    assert idx.get("simvastatin") is not None


def test_substrate_enzyme_parsed():
    idx = load_attribute_index()
    simva = idx.get("simvastatin")
    sub = [e for e in simva.enzymes if e.role == "substrate"][0]
    assert sub.enzyme == "CYP3A4" and sub.fm == 0.8 and sub.yields == "parent"
    assert simva.nti.is_nti is True and simva.nti.consequence == "toxicity"


def test_inhibitor_and_tdi_flag():
    idx = load_attribute_index()
    clarith = idx.get("clarithromycin")
    inh = clarith.enzymes[0]
    assert inh.role == "inhibitor" and inh.strength == "strong"
    assert inh.inhibition_type == "mechanism_based"


def test_prodrug_and_absorption_and_long_acting():
    idx = load_attribute_index()
    assert idx.get("clopidogrel").prodrug is True
    assert idx.get("clopidogrel").activating_enzyme == "CYP2C19"
    assert idx.get("levothyroxine").absorption.chelation_cations == ["Ca", "Mg", "Al", "Fe"]
    assert idx.get("amiodarone").long_acting is True


def test_unknown_returns_none():
    assert load_attribute_index().get("not-a-drug") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_attributes.py -q`
Expected: FAIL — `ModuleNotFoundError: ...interaction.attributes`

- [ ] **Step 4: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/attributes.py
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from services.ai.clinical_decision_support.normalizer import normalize

_DATA = Path(__file__).parent / "data" / "drug_attributes.yaml"


@dataclass(frozen=True)
class EnzymeRole:
    enzyme: str
    role: str                       # substrate | inhibitor | inducer
    strength: str | None = None     # strong | moderate | weak
    fm: float | None = None
    yields: str | None = None       # parent | active
    inhibition_type: str | None = None  # reversible | mechanism_based


@dataclass(frozen=True)
class TransporterRole:
    name: str
    role: str
    strength: str | None = None
    organ: str | None = None


@dataclass(frozen=True)
class PDAxis:
    axis: str
    strength: str | None = None
    tier: str | None = None
    subtype: str | None = None
    direction: str = "additive"     # additive | synergistic | antagonistic


@dataclass(frozen=True)
class NTI:
    is_nti: bool = False
    consequence: str = "toxicity"   # toxicity | efficacy_loss


@dataclass(frozen=True)
class Absorption:
    chelation_cations: list[str] = field(default_factory=list)
    separation_hours: int | None = None
    ph_dependent: str | None = None  # "acid_requiring"


@dataclass(frozen=True)
class DrugAttributes:
    ingredient: str
    classes: tuple[str, ...] = ()
    enzymes: tuple[EnzymeRole, ...] = ()
    transporters: tuple[TransporterRole, ...] = ()
    pd: dict[str, PDAxis] = field(default_factory=dict)
    nti: NTI = field(default_factory=NTI)
    prodrug: bool = False
    activating_enzyme: str | None = None
    active_metabolite: str | None = None
    pgx_enzyme: str | None = None
    elimination_route: str | None = None
    absorption: Absorption | None = None
    provides_cations: tuple[str, ...] = ()
    absorption_suppressant_ph: bool = False
    reduces_renal_clearance_of: tuple[str, ...] = ()
    induction_offset_days: int | None = None
    long_acting: bool = False


@dataclass(frozen=True)
class AttributeIndex:
    by_ingredient: dict[str, DrugAttributes]

    def get(self, name: str | None) -> DrugAttributes | None:
        return self.by_ingredient.get(normalize(name))


def _parse(entry: dict) -> DrugAttributes:
    pd = {}
    for axis, body in (entry.get("pd") or {}).items():
        body = body or {}
        pd[axis] = PDAxis(axis=axis, strength=body.get("strength"), tier=body.get("tier"),
                          subtype=body.get("subtype"), direction=body.get("direction", "additive"))
    absn = entry.get("absorption")
    absorption = Absorption(
        chelation_cations=list((absn or {}).get("chelation_cations", [])),
        separation_hours=(absn or {}).get("separation_hours"),
        ph_dependent=(absn or {}).get("ph_dependent"),
    ) if absn else None
    nti_raw = entry.get("nti") or {}
    induction = entry.get("induction") or {}
    return DrugAttributes(
        ingredient=normalize(entry["ingredient"]),
        classes=tuple(entry.get("classes", [])),
        enzymes=tuple(EnzymeRole(**e) for e in entry.get("enzymes", [])),
        transporters=tuple(TransporterRole(**t) for t in entry.get("transporters", [])),
        pd=pd,
        nti=NTI(is_nti=nti_raw.get("is_nti", False), consequence=nti_raw.get("consequence", "toxicity")),
        prodrug=entry.get("prodrug", False),
        activating_enzyme=entry.get("activating_enzyme"),
        active_metabolite=entry.get("active_metabolite"),
        pgx_enzyme=entry.get("pgx_enzyme"),
        elimination_route=entry.get("elimination_route"),
        absorption=absorption,
        provides_cations=tuple(entry.get("provides_cations", [])),
        absorption_suppressant_ph=bool((entry.get("absorption_suppressant") or {}).get("ph", False)),
        reduces_renal_clearance_of=tuple(normalize(x) for x in entry.get("reduces_renal_clearance_of", [])),
        induction_offset_days=induction.get("offset_days"),
        long_acting=entry.get("long_acting", False),
    )


@lru_cache(maxsize=1)
def load_attribute_index() -> AttributeIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    return AttributeIndex(by_ingredient={a.ingredient: a for a in (_parse(e) for e in raw)})
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_attributes.py -q`
Expected: PASS (5 passed)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/attributes.py \
        services/ai/clinical_decision_support/interaction/data/drug_attributes.yaml \
        tests/unit/test_interaction_attributes.py
git commit -m "feat(cds): per-drug PK/PD attribute index + high-yield seed"
```

---

## Task 3: Curated rule index + seed (migrate the 6 legacy rules + drug-disease)

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/rules.py`
- Create: `services/ai/clinical_decision_support/interaction/data/interaction_rules.yaml`
- Test: `tests/unit/test_interaction_rules.py`

- [ ] **Step 1: Write the seed YAML**

```yaml
# services/ai/clinical_decision_support/interaction/data/interaction_rules.yaml
# kind: drug_drug | drug_disease. left/right are normalized drug OR class names.
- kind: drug_drug
  left: anticoagulant
  right: nsaid
  severity: Major
  mechanism: "Additive bleeding risk; NSAID GI mucosal injury plus platelet effects."
  action: "Assess GI bleed risk; consider gastroprotection or alternative analgesic."
  evidence: ["Lexicomp: NSAIDs + anticoagulants"]
  confidence: 0.92
  source: curated

- kind: drug_drug
  left: clarithromycin
  right: simvastatin
  severity: Contraindicated
  mechanism: "Strong CYP3A4 inhibition raises simvastatin exposure; myopathy/rhabdomyolysis risk."
  action: "Hold simvastatin during the macrolide course or use azithromycin."
  evidence: ["FDA label: simvastatin"]
  confidence: 0.97
  source: curated

- kind: drug_drug
  left: ssri
  right: serotonergic_opioid
  severity: Major
  mechanism: "Additive serotonergic effect; serotonin syndrome and seizure risk."
  action: "Assess for serotonin toxicity; discuss alternative analgesic."
  evidence: ["Lexicomp: tramadol + SSRIs"]
  confidence: 0.90
  source: curated

- kind: drug_disease
  left: nsaid
  right: peptic_ulcer_disease
  severity: Major
  mechanism: "NSAIDs impair GI mucosal protection; ulcer recurrence/bleeding risk."
  action: "Avoid NSAID or add gastroprotection; consider alternative analgesic."
  evidence: ["AGS Beers 2023"]
  confidence: 0.9
  source: curated

- kind: drug_disease
  left: nsaid
  right: chronic_kidney_disease
  severity: Major
  mechanism: "NSAIDs reduce renal perfusion; AKI / CKD progression risk."
  action: "Avoid NSAID in significant CKD; prefer acetaminophen."
  evidence: ["KDIGO"]
  confidence: 0.9
  source: curated
```

- [ ] **Step 2: Write the failing test**

```python
# tests/unit/test_interaction_rules.py
from services.ai.clinical_decision_support.interaction.rules import load_rule_index
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def test_drug_drug_lookup_is_order_independent():
    idx = load_rule_index()
    r1 = idx.find_drug_drug({"clarithromycin"}, {"simvastatin"})
    r2 = idx.find_drug_drug({"simvastatin"}, {"clarithromycin"})
    assert r1 and r2 and r1[0].severity is S.CONTRAINDICATED


def test_class_level_match():
    idx = load_rule_index()
    # warfarin is class 'anticoagulant', ibuprofen is class 'nsaid'
    hits = idx.find_drug_drug({"warfarin", "anticoagulant"}, {"ibuprofen", "nsaid"})
    assert any(h.severity is S.MAJOR for h in hits)


def test_drug_disease_lookup():
    idx = load_rule_index()
    hits = idx.find_drug_disease({"ibuprofen", "nsaid"}, "peptic_ulcer_disease")
    assert hits and hits[0].severity is S.MAJOR
```

- [ ] **Step 3: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_rules.py -q`
Expected: FAIL — module not found

- [ ] **Step 4: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/rules.py
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .severity import InteractionSeverity, normalize_severity

_DATA = Path(__file__).parent / "data" / "interaction_rules.yaml"


@dataclass(frozen=True)
class InteractionRule:
    kind: str                # drug_drug | drug_disease
    left: str
    right: str
    severity: InteractionSeverity
    mechanism: str
    action: str
    evidence: tuple[str, ...]
    confidence: float
    source: str              # curated | ddinter


@dataclass(frozen=True)
class RuleIndex:
    drug_drug: tuple[InteractionRule, ...]
    drug_disease: tuple[InteractionRule, ...]

    def find_drug_drug(self, a_tokens: set[str], b_tokens: set[str]) -> list[InteractionRule]:
        out = []
        for r in self.drug_drug:
            if (r.left in a_tokens and r.right in b_tokens) or \
               (r.left in b_tokens and r.right in a_tokens):
                out.append(r)
        return out

    def find_drug_disease(self, drug_tokens: set[str], condition: str) -> list[InteractionRule]:
        return [r for r in self.drug_disease
                if r.left in drug_tokens and r.right == condition]


def _parse(e: dict) -> InteractionRule:
    return InteractionRule(
        kind=e["kind"], left=e["left"], right=e["right"],
        severity=normalize_severity(e["severity"]),
        mechanism=e.get("mechanism", ""), action=e.get("action", ""),
        evidence=tuple(e.get("evidence", [])), confidence=float(e.get("confidence", 0.8)),
        source=e.get("source", "curated"),
    )


@lru_cache(maxsize=1)
def load_rule_index() -> RuleIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    rules = [_parse(e) for e in raw]
    return RuleIndex(
        drug_drug=tuple(r for r in rules if r.kind == "drug_drug"),
        drug_disease=tuple(r for r in rules if r.kind == "drug_disease"),
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_rules.py -q`
Expected: PASS (3 passed)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/rules.py \
        services/ai/clinical_decision_support/interaction/data/interaction_rules.yaml \
        tests/unit/test_interaction_rules.py
git commit -m "feat(cds): curated interaction rule index (drug-drug + drug-disease)"
```

---

## Task 4: Finding + InteractionReport dataclasses

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/report.py`
- Test: `tests/unit/test_interaction_report_model.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_report_model.py
from services.ai.clinical_decision_support.interaction.report import (
    Finding, InteractionReport, build_report,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S


def _f(sev, prov="current_rx"):
    return Finding(
        rule_id="r", type="drug_drug", severity=sev, base_severity=sev,
        direction="toxicity", predicted_magnitude=None, onset_offset=None,
        participants=[{"name": "a", "kind": "drug", "provenance": prov, "last_seen": None}],
        mechanism="m", mechanism_basis=None, clinical_problem="p",
        suggested_actions=["a"], evidence_sources=["e"], evidence_grade="Established",
        source="curated", patient_specific_factors=[], confidence=0.9, recency_note=None,
    )


def test_report_summary_and_sort():
    rep = build_report([_f(S.MINOR), _f(S.CONTRAINDICATED), _f(S.MODERATE)])
    assert rep.summary == {"Contraindicated": 1, "Major": 0, "Moderate": 1, "Minor": 1}
    assert [f.severity for f in rep.findings][0] is S.CONTRAINDICATED   # severity desc
    assert rep.degraded is False


def test_degraded_flag():
    rep = build_report([], degraded=True)
    assert rep.degraded is True and rep.findings == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_report_model.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/report.py
from __future__ import annotations

from dataclasses import dataclass, field

from .severity import InteractionSeverity

VERIFICATION_NOTICE = (
    "Advisory clinical decision support only. A licensed pharmacist must verify "
    "patient-specific appropriateness before any action."
)

_PROV_RANK = {"current_rx": 0, "active": 1, "historical": 2}


@dataclass
class Finding:
    rule_id: str
    type: str                       # drug_drug | drug_disease | duplicate_therapy | drug_allergy
    severity: InteractionSeverity
    base_severity: InteractionSeverity
    direction: str                  # toxicity | efficacy_loss | additive_risk | opposition
    predicted_magnitude: str | None
    onset_offset: str | None
    participants: list[dict]
    mechanism: str
    mechanism_basis: str | None
    clinical_problem: str
    suggested_actions: list[str]
    evidence_sources: list[str]
    evidence_grade: str             # Established | Probable | Theoretical | Predicted
    source: str                     # curated | ddinter | inferred_mechanistic
    patient_specific_factors: list[str]
    confidence: float
    recency_note: str | None
    pharmacist_verification_notice: str = VERIFICATION_NOTICE


@dataclass
class InteractionReport:
    summary: dict
    findings: list[Finding]
    generated_at: str | None = None
    degraded: bool = False


def _provenance_rank(f: Finding) -> int:
    return min((_PROV_RANK.get(p.get("provenance"), 3) for p in f.participants), default=3)


def build_report(findings: list[Finding], *, degraded: bool = False,
                 generated_at: str | None = None) -> InteractionReport:
    ordered = sorted(findings, key=lambda f: (-f.severity.rank, _provenance_rank(f)))
    summary = {s.value: 0 for s in InteractionSeverity}
    for f in ordered:
        summary[f.severity.value] += 1
    return InteractionReport(summary=summary, findings=ordered,
                             generated_at=generated_at, degraded=degraded)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_report_model.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/report.py \
        tests/unit/test_interaction_report_model.py
git commit -m "feat(cds): Finding + InteractionReport model with severity sort + summary"
```

---

## Task 5: Mechanism core — direction, magnitude, modifiers (pure)

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/mechanism.py`
- Test: `tests/unit/test_interaction_mechanism.py`

This is the heart of the PK logic. Pure functions over `DrugAttributes`, no DB.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_mechanism.py
from services.ai.clinical_decision_support.interaction.attributes import load_attribute_index
from services.ai.clinical_decision_support.interaction.mechanism import (
    metabolic_interaction, fm_bin,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S

ATTR = load_attribute_index()


def test_fm_bin():
    assert fm_bin(0.8) == "high" and fm_bin(0.4) == "low" and fm_bin(None) == "low"


def test_inhibitor_parent_substrate_is_toxicity_major():
    # clarithromycin (strong 3A4 inhibitor) + simvastatin (3A4 substrate fm 0.8, NTI)
    f = metabolic_interaction(ATTR.get("clarithromycin"), ATTR.get("simvastatin"))
    assert f is not None
    assert f["direction"] == "toxicity"
    assert f["base_severity"] is S.MAJOR
    assert f["severity"] is S.CONTRAINDICATED          # NTI victim → +1 step
    assert "≥5" in f["predicted_magnitude"] or "5" in f["predicted_magnitude"]


def test_inhibitor_prodrug_is_efficacy_loss():
    # omeprazole (2C19 inhibitor) + clopidogrel (2C19-activated prodrug)
    f = metabolic_interaction(ATTR.get("omeprazole"), ATTR.get("clopidogrel"))
    assert f is not None and f["direction"] == "efficacy_loss"


def test_inducer_parent_is_efficacy_loss_with_offset_note():
    # rifampin (strong inducer) + warfarin (parent-active substrate, but 2C9 here)
    f = metabolic_interaction(ATTR.get("rifampin"), ATTR.get("warfarin"))
    # rifampin induces 3A4 in seed; warfarin substrate is 2C9 → no enzyme overlap → None
    assert f is None
```

(Note: the rifampin+warfarin enzyme mismatch is intentional — it proves the engine
only fires on a *shared* enzyme. A 2C9-inducer seed entry would make it fire; that is
added in sub-project #3's data expansion.)

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_mechanism.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/mechanism.py
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
        # direction from role × what the substrate yields (parent vs prodrug-active)
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_mechanism.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/mechanism.py \
        tests/unit/test_interaction_mechanism.py
git commit -m "feat(cds): PK metabolic mechanism core (direction + magnitude + NTI step)"
```

---

## Task 6: Review-set assembly from the DB

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/review_set.py`
- Test: `tests/unit/test_interaction_review_set.py`

`build_review_set` is DB-coupled; the test uses lightweight fakes (no live DB) by
passing prebuilt row objects through the pure assembly helper `assemble_review_set`,
keeping the DB query in a thin wrapper.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_review_set.py
from datetime import date
from types import SimpleNamespace as NS

from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set


def test_provenance_tagging_and_classes():
    rx = [NS(drug_name="ibuprofen")]
    active = [NS(normalized_name="warfarin", drug_name="warfarin", status="active", start_date=date(2026, 1, 1))]
    historical = [NS(normalized_name="simvastatin", drug_name="simvastatin", status="discontinued", start_date=date(2023, 1, 1))]
    rs = assemble_review_set(current_rx=rx, meds=active + historical,
                             conditions=["peptic_ulcer_disease"], allergies=["penicillin"])
    provs = {m.normalized_name: m.provenance for m in rs.meds}
    assert provs["ibuprofen"] == "current_rx"
    assert provs["warfarin"] == "active"
    assert provs["simvastatin"] == "historical"
    assert any("nsaid" in m.classes for m in rs.meds)       # class resolved via normalizer
    assert rs.conditions[0].concept == "peptic_ulcer_disease"
    assert rs.allergies == ["penicillin"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_review_set.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/review_set.py
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.clinical import Medication
from shared.models.prescription import Prescription, RxStatus
from services.ai.clinical_decision_support.normalizer import normalize, classes_of

CURRENT_RX_STATUSES = [
    RxStatus.INTAKE, RxStatus.PENDING_DUR, RxStatus.DUR_HOLD,
    RxStatus.PENDING_VERIFICATION, RxStatus.VERIFICATION_IN_PROGRESS,
    RxStatus.PENDING_ADJUDICATION, RxStatus.PENDING_PA, RxStatus.READY_TO_FILL,
    RxStatus.FILLING, RxStatus.WILL_CALL, RxStatus.ON_HOLD,
]


@dataclass(frozen=True)
class ReviewMed:
    normalized_name: str
    classes: tuple[str, ...]
    provenance: str                 # current_rx | active | historical
    last_seen_date: date | None


@dataclass(frozen=True)
class Condition:
    concept: str
    status: str = "active"          # model has no per-condition status yet (#1 limitation)


@dataclass(frozen=True)
class ReviewSet:
    meds: list[ReviewMed]
    conditions: list[Condition]
    allergies: list[str] = field(default_factory=list)
    labs: dict = field(default_factory=dict)        # {lab_key: float} latest value
    age: int | None = None


def norm_condition(text: str) -> str:
    """Conditions are NOT drugs — never run the drug normalizer on them."""
    return (text or "").strip().lower().replace(" ", "_")


def _med(name: str, provenance: str, last_seen: date | None) -> ReviewMed:
    norm = normalize(name)
    tokens = {norm, *classes_of(name)}
    return ReviewMed(normalized_name=norm, classes=tuple(sorted(tokens)),
                     provenance=provenance, last_seen_date=last_seen)


def assemble_review_set(*, current_rx, meds, conditions, allergies,
                        labs=None, age=None) -> ReviewSet:
    review_meds = [_med(rx.drug_name, "current_rx", None) for rx in current_rx]
    for m in meds:
        prov = "active" if getattr(m, "status", "active") == "active" else "historical"
        review_meds.append(_med(getattr(m, "normalized_name", None) or m.drug_name,
                                prov, getattr(m, "start_date", None)))
    return ReviewSet(
        meds=review_meds,
        conditions=[Condition(concept=norm_condition(c)) for c in (conditions or [])],
        allergies=list(allergies or []),
        labs=labs or {},
        age=age,
    )


async def build_review_set(*, db: AsyncSession, patient, pharmacy_id, rx_ids=None) -> ReviewSet:
    rx_q = select(Prescription).where(
        Prescription.patient_id == patient.id,
        Prescription.pharmacy_id == pharmacy_id,
        Prescription.is_deleted == False,  # noqa: E712
    )
    rx_q = rx_q.where(Prescription.id.in_(rx_ids)) if rx_ids else \
        rx_q.where(Prescription.status.in_([s.value for s in CURRENT_RX_STATUSES]))
    current_rx = (await db.execute(rx_q)).scalars().all()

    meds = (await db.execute(select(Medication).where(
        Medication.patient_id == patient.id,
        Medication.pharmacy_id == pharmacy_id,
        Medication.is_deleted == False,  # noqa: E712
    ))).scalars().all()

    # Latest value per lab (reuse the existing cds helpers for keys/floats).
    from services.platform.routers.cds import _lab_key, _to_float, _age_from_dob
    from shared.models.patient import LabResult, PatientAllergy
    lab_rows = (await db.execute(
        select(LabResult).where(LabResult.patient_id == patient.id,
                                LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc()))).scalars().all()
    labs: dict[str, float] = {}
    for row in lab_rows:
        key = _lab_key(row.test_name)
        if key not in labs and _to_float(row.value) is not None:
            labs[key] = _to_float(row.value)
    allergy_rows = (await db.execute(
        select(PatientAllergy).where(PatientAllergy.patient_id == patient.id,
                                     PatientAllergy.is_deleted == False))  # noqa: E712
        ).scalars().all()

    return assemble_review_set(
        current_rx=current_rx, meds=meds,
        conditions=getattr(patient, "conditions", None) or [],
        allergies=[a.allergen_name for a in allergy_rows],
        labs=labs, age=_age_from_dob(getattr(patient, "date_of_birth", None)),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_review_set.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/review_set.py \
        tests/unit/test_interaction_review_set.py
git commit -m "feat(cds): review-set assembly (provenance-tagged meds + conditions)"
```

---

## Task 7: Engine — explicit checks (drug-drug, drug-disease, duplicate, allergy)

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/engine.py`
- Test: `tests/unit/test_interaction_engine.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_interaction_engine.py
from services.ai.clinical_decision_support.interaction.engine import evaluate
from services.ai.clinical_decision_support.interaction.review_set import (
    assemble_review_set, ReviewSet,
)
from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S
from types import SimpleNamespace as NS


def _rs(drugs, conditions=None, allergies=None):
    return assemble_review_set(current_rx=[NS(drug_name=d) for d in drugs],
                               meds=[], conditions=conditions or [], allergies=allergies or [])


def test_explicit_drug_drug_pair():
    rep = evaluate(_rs(["warfarin", "ibuprofen"]))
    dd = [f for f in rep.findings if f.type == "drug_drug" and f.source == "curated"]
    assert dd and dd[0].severity is S.MAJOR


def test_drug_disease():
    rep = evaluate(_rs(["ibuprofen"], conditions=["peptic_ulcer_disease"]))
    assert any(f.type == "drug_disease" for f in rep.findings)


def test_duplicate_therapy_same_class():
    rep = evaluate(_rs(["ibuprofen", "naproxen"]))   # both nsaid (naproxen via normalizer/seed class)
    assert any(f.type == "duplicate_therapy" for f in rep.findings)


def test_drug_allergy():
    rep = evaluate(_rs(["amoxicillin"], allergies=["penicillin"]))
    assert any(f.type == "drug_allergy" for f in rep.findings)
```

(If `naproxen`/`amoxicillin` classes are not yet in the normalizer, add them to
`GENERIC_CLASSES` in `services/ai/clinical_decision_support/normalizer.py` as part of
this task: `"naproxen": {"nsaid"}`, `"amoxicillin": {"penicillin"}`, and the
cross-reactivity is handled by the allergy check below.)

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: FAIL — module not found

- [ ] **Step 3: Write minimal implementation**

```python
# services/ai/clinical_decision_support/interaction/engine.py
from __future__ import annotations

from itertools import combinations

from .attributes import AttributeIndex, load_attribute_index
from .report import Finding, InteractionReport, build_report
from .review_set import ReviewSet
from .rules import RuleIndex, load_rule_index
from .severity import InteractionSeverity

CROSS_REACTIVITY = {"penicillin": {"penicillin", "cephalosporin"}}


def _finding(*, rule_id, type, severity, direction, mechanism, actions, evidence,
             grade, source, participants, base=None, magnitude=None, onset=None,
             basis=None, factors=None, confidence=0.85, recency=None,
             clinical_problem="") -> Finding:
    return Finding(
        rule_id=rule_id, type=type, severity=severity, base_severity=base or severity,
        direction=direction, predicted_magnitude=magnitude, onset_offset=onset,
        participants=participants, mechanism=mechanism, mechanism_basis=basis,
        clinical_problem=clinical_problem or mechanism, suggested_actions=actions,
        evidence_sources=list(evidence), evidence_grade=grade, source=source,
        patient_specific_factors=factors or [], confidence=confidence, recency_note=recency,
    )


def _p(med):
    return {"name": med.normalized_name, "kind": "drug", "provenance": med.provenance,
            "last_seen": med.last_seen_date.isoformat() if med.last_seen_date else None}


def _explicit_drug_drug(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        for r in rules.find_drug_drug(set(a.classes), set(b.classes)):
            out.append(_finding(
                rule_id=f"dd:{r.left}-{r.right}", type="drug_drug", severity=r.severity,
                direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                evidence=r.evidence, grade="Established", source=r.source,
                participants=[_p(a), _p(b)], confidence=r.confidence))
    return out


def _drug_disease(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for med in rs.meds:
        for cond in rs.conditions:
            for r in rules.find_drug_disease(set(med.classes), cond.concept):
                out.append(_finding(
                    rule_id=f"ddz:{r.left}-{r.right}", type="drug_disease", severity=r.severity,
                    direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                    evidence=r.evidence, grade="Established", source=r.source,
                    participants=[_p(med), {"name": cond.concept, "kind": "condition",
                                            "provenance": cond.status, "last_seen": None}],
                    confidence=r.confidence))
    return out


def _duplicate_therapy(rs: ReviewSet) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        shared = (set(a.classes) & set(b.classes)) - {a.normalized_name, b.normalized_name}
        # ignore the ingredient tokens; require a real therapeutic class overlap
        shared = {c for c in shared if c not in (a.normalized_name, b.normalized_name)}
        if shared and a.normalized_name != b.normalized_name:
            out.append(_finding(
                rule_id=f"dup:{sorted(shared)[0]}", type="duplicate_therapy",
                severity=InteractionSeverity.MODERATE, direction="additive_risk",
                mechanism=f"Therapeutic duplication: both are {sorted(shared)[0]}.",
                actions=["Confirm intentional; risk of additive class effects/overdose."],
                evidence=["Therapeutic duplication"], grade="Established", source="curated",
                participants=[_p(a), _p(b)], confidence=0.8))
    return out


def _drug_allergy(rs: ReviewSet) -> list[Finding]:
    out = []
    allergy_classes = set()
    for a in rs.allergies:
        from services.ai.clinical_decision_support.normalizer import normalize, classes_of
        allergy_classes |= {normalize(a), *classes_of(a)}
        allergy_classes |= CROSS_REACTIVITY.get(normalize(a), set())
    for med in rs.meds:
        if set(med.classes) & allergy_classes:
            out.append(_finding(
                rule_id="allergy", type="drug_allergy", severity=InteractionSeverity.MAJOR,
                direction="toxicity",
                mechanism=f"{med.normalized_name} overlaps a documented allergy class.",
                actions=["Verify allergy history; consider non-cross-reactive alternative."],
                evidence=["Allergy cross-reactivity"], grade="Established", source="curated",
                participants=[_p(med)], confidence=0.85))
    return out


def evaluate(rs: ReviewSet, *, rules: RuleIndex | None = None,
             attrs: AttributeIndex | None = None) -> InteractionReport:
    try:
        rules = rules or load_rule_index()
        attrs = attrs or load_attribute_index()
    except Exception:
        return build_report([], degraded=True)
    findings: list[Finding] = []
    findings += _explicit_drug_drug(rs, rules)
    findings += _drug_disease(rs, rules)
    findings += _duplicate_therapy(rs)
    findings += _drug_allergy(rs)
    return build_report(findings)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: PASS (4 passed). If a class is missing, add it to `normalizer.GENERIC_CLASSES` and re-run.

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/engine.py \
        services/ai/clinical_decision_support/normalizer.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): engine explicit checks (drug-drug/disease/duplicate/allergy)"
```

---

## Task 8: Engine — wire in PK metabolic inference + precedence

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/engine.py`
- Test: `tests/unit/test_interaction_engine.py` (add cases)

- [ ] **Step 1: Write the failing test (append)**

```python
# append to tests/unit/test_interaction_engine.py
def test_inferred_pk_when_no_explicit_rule():
    # clarithromycin + simvastatin: explicit rule EXISTS → source curated, inferred suppressed
    rep = evaluate(_rs(["clarithromycin", "simvastatin"]))
    dd = [f for f in rep.findings if {p["name"] for p in f.participants} ==
          {"clarithromycin", "simvastatin"}]
    assert len(dd) == 1 and dd[0].source == "curated"     # precedence: curated > inferred


def test_inferred_pk_emitted_without_explicit_rule():
    # amiodarone (mod 3A4 inhibitor) + simvastatin (3A4 substrate) has no curated rule here
    rep = evaluate(_rs(["amiodarone", "simvastatin"]))
    inf = [f for f in rep.findings if f.source == "inferred_mechanistic"
           and {p["name"] for p in f.participants} == {"amiodarone", "simvastatin"}]
    assert inf and inf[0].evidence_grade == "Predicted"
    assert inf[0].direction == "toxicity"
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: FAIL — `test_inferred_pk_*` fail (no inference yet)

- [ ] **Step 3: Add inference + precedence to `engine.py`**

```python
# add imports at top of engine.py
from .mechanism import metabolic_interaction

# add this function
def _inferred_pk(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for perp, victim, pm, vm in ((aa, ba, a, b), (ba, aa, b, a)):
            m = metabolic_interaction(perp, victim)
            if not m:
                continue
            out.append(_finding(
                rule_id=f"pk:{m['enzyme']}:{perp.ingredient}->{victim.ingredient}",
                type="drug_drug", severity=m["severity"], base=m["base_severity"],
                direction=m["direction"], magnitude=m["predicted_magnitude"],
                onset=m["onset_offset"], basis=m["mechanism_basis"],
                mechanism=f"{perp.ingredient} {m['mechanism_basis']} → {victim.ingredient}",
                actions=["Review need; monitor for the predicted effect or adjust dose."],
                evidence=["Mechanistic inference (PK)"], grade="Predicted",
                source="inferred_mechanistic", participants=[_p(pm), _p(vm)],
                factors=m["patient_specific_factors"], confidence=0.6))
    return out


# add a precedence/dedup helper
def _dedup(findings: list[Finding]) -> list[Finding]:
    _SRC = {"curated": 0, "ddinter": 1, "inferred_mechanistic": 2}
    best: dict[tuple, Finding] = {}
    for f in findings:
        key = (f.type, frozenset(p["name"] for p in f.participants))
        cur = best.get(key)
        if cur is None or (_SRC[f.source], -f.severity.rank) < (_SRC[cur.source], -cur.severity.rank):
            best[key] = f
    return list(best.values())
```

Then update `evaluate` to call inference and dedup:

```python
    findings: list[Finding] = []
    findings += _explicit_drug_drug(rs, rules)
    findings += _inferred_pk(rs, attrs)
    findings += _drug_disease(rs, rules)
    findings += _duplicate_therapy(rs)
    findings += _drug_allergy(rs)
    return build_report(_dedup(findings))
```

- [ ] **Step 4: Run to verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/engine.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): PK metabolic inference + curated>inferred precedence/dedup"
```

---

## Task 9: Mechanism — phenoconversion, transporter, absorption, renal

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/mechanism.py`
- Modify: `services/ai/clinical_decision_support/interaction/engine.py` (call new paths)
- Test: `tests/unit/test_interaction_mechanism.py` + `tests/unit/test_interaction_engine.py`

- [ ] **Step 1: Write failing tests (append to test_interaction_engine.py)**

```python
def test_phenoconversion_codeine_efficacy_loss():
    rep = evaluate(_rs(["paroxetine", "codeine"]))   # strong 2D6 inhibitor + 2D6 prodrug
    f = [x for x in rep.findings if x.direction == "efficacy_loss"
         and {p["name"] for p in x.participants} == {"paroxetine", "codeine"}]
    assert f


def test_transporter_pgp_digoxin():
    rep = evaluate(_rs(["clarithromycin", "digoxin"]))   # P-gp inhibitor + P-gp substrate
    assert any("P-gp" in (f.mechanism_basis or "") for f in rep.findings)


def test_absorption_chelation_separation_action():
    rep = evaluate(_rs(["levothyroxine", "calcium carbonate"]))
    f = [x for x in rep.findings if x.type == "drug_drug" and "separate" in
         " ".join(x.suggested_actions).lower()]
    assert f


def test_renal_competition_lithium():
    rep = evaluate(_rs(["ibuprofen", "lithium"]))
    assert any(f.direction == "toxicity" and {p["name"] for p in f.participants} ==
               {"ibuprofen", "lithium"} for f in rep.findings)
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: FAIL — the 4 new cases fail

- [ ] **Step 3: Add mechanism functions** (append to `mechanism.py`)

```python
def phenoconversion(perp: DrugAttributes, victim: DrugAttributes) -> dict | None:
    if not victim.pgx_enzyme:
        return None
    for pe in perp.enzymes:
        if pe.role == "inhibitor" and pe.strength == "strong" and pe.enzyme == victim.pgx_enzyme:
            prodrug = victim.prodrug
            return {
                "type": "drug_drug",
                "direction": "efficacy_loss" if prodrug else "toxicity",
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
            "mechanism_basis": "competition/▼GFR reducing renal elimination",
            "patient_specific_factors": [f"{victim.ingredient} is narrow-therapeutic-index"],
        }
    return None
```

- [ ] **Step 4: Wire them into the engine** — generalize `_inferred_pk` to run all mechanism functions. Replace the metabolic-only loop body:

```python
# in engine.py, replace _inferred_pk with:
from .mechanism import (
    metabolic_interaction, phenoconversion, transporter_interaction,
    absorption_interaction, renal_competition,
)

_MECHANISMS = (metabolic_interaction, phenoconversion, transporter_interaction,
               absorption_interaction, renal_competition)


def _inferred_pk(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for perp, victim, pm, vm in ((aa, ba, a, b), (ba, aa, b, a)):
            for fn in _MECHANISMS:
                m = fn(perp, victim)
                if not m:
                    continue
                out.append(_finding(
                    rule_id=f"{fn.__name__}:{perp.ingredient}->{victim.ingredient}",
                    type="drug_drug", severity=m["severity"], base=m["base_severity"],
                    direction=m["direction"], magnitude=m.get("predicted_magnitude"),
                    onset=m.get("onset_offset"), basis=m["mechanism_basis"],
                    mechanism=f"{perp.ingredient}: {m['mechanism_basis']} → {victim.ingredient}",
                    actions=[m.get("action", "Review need; monitor or adjust dose.")],
                    evidence=["Mechanistic inference"], grade="Predicted",
                    source="inferred_mechanistic", participants=[_p(pm), _p(vm)],
                    factors=m.get("patient_specific_factors", []), confidence=0.6))
    return out
```

- [ ] **Step 5: Run tests**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py tests/unit/test_interaction_mechanism.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/mechanism.py \
        services/ai/clinical_decision_support/interaction/engine.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): phenoconversion + transporter + absorption + renal inference"
```

---

## Task 10: PD inference (additive / synergistic / antagonistic, MAOI, QT gating)

**Files:**
- Create: `services/ai/clinical_decision_support/interaction/pd.py`
- Modify: `services/ai/clinical_decision_support/interaction/engine.py`
- Test: `tests/unit/test_interaction_engine.py`

- [ ] **Step 1: Write failing tests (append)**

```python
def test_pd_additive_bleeding():
    rep = evaluate(_rs(["warfarin", "paroxetine"]))   # both bleeding axis (+ no explicit rule)
    assert any(f.type == "drug_drug" and f.direction == "additive_risk" and
               "bleeding" in f.mechanism.lower() for f in rep.findings)


def test_pd_opposition_nsaid_antihypertensive():
    # ibuprofen has antihypertensive_opposition (antagonistic); pair with an antihypertensive
    rs = _rs(["ibuprofen", "lisinopril"])
    rep = evaluate(rs)
    assert any(f.direction == "opposition" for f in rep.findings)


def test_maoi_serotonergic_contraindicated():
    rep = evaluate(_rs(["phenelzine", "paroxetine"]))
    from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S
    assert any(f.severity is S.CONTRAINDICATED for f in rep.findings)
```

(Add `lisinopril` attribute entry with `pd: { antihypertensive_opposition: {direction: antagonistic} }`
or simpler: give ibuprofen the opposition axis and lisinopril an
`pd: { antihypertensive: {direction: antagonistic} }` matching token. Use a shared axis
name `antihypertensive_opposition` on BOTH so the pairing matches — add lisinopril to
`drug_attributes.yaml`: `pd: { antihypertensive_opposition: { direction: antagonistic } }`.)

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: FAIL — 3 new cases

- [ ] **Step 3: Implement PD inference**

```python
# services/ai/clinical_decision_support/interaction/pd.py
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
```

- [ ] **Step 4: Wire into engine** — add PD pass before dedup:

```python
# engine.py: add import + function + call
from .pd import pd_interactions


def _inferred_pd(rs: ReviewSet, attrs: AttributeIndex) -> list[Finding]:
    out = []
    for a, b in combinations(rs.meds, 2):
        aa, ba = attrs.get(a.normalized_name), attrs.get(b.normalized_name)
        if not aa or not ba:
            continue
        for m in pd_interactions(aa, ba):
            out.append(_finding(
                rule_id=f"pd:{m['axis']}:{aa.ingredient}-{ba.ingredient}", type="drug_drug",
                severity=m["severity"], direction=m["direction"], mechanism=m["mechanism"],
                actions=["Review combination; monitor for the additive/opposing effect."],
                evidence=["Mechanistic inference (PD)"], grade="Predicted",
                source="inferred_mechanistic", participants=[_p(a), _p(b)], confidence=0.6))
    return out
```

Add `findings += _inferred_pd(rs, attrs)` in `evaluate` (before `_dedup`).

- [ ] **Step 5: Run tests**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: PASS (all)

- [ ] **Step 6: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/pd.py \
        services/ai/clinical_decision_support/interaction/engine.py \
        services/ai/clinical_decision_support/interaction/data/drug_attributes.yaml \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): PD inference (additive/synergistic/antagonistic, MAOI, QT gating)"
```

---

## Task 11: Recency cutoff (long-acting + TDI/inducer-offset bypass)

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/engine.py`
- Test: `tests/unit/test_interaction_engine.py`

- [ ] **Step 1: Write failing tests (append)**

```python
from datetime import date, timedelta
from types import SimpleNamespace as NS


def _rs_hist(current, historical_name, days_ago, status="discontinued"):
    from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
    return assemble_review_set(
        current_rx=[NS(drug_name=d) for d in current],
        meds=[NS(normalized_name=historical_name, drug_name=historical_name,
                 status=status, start_date=date.today() - timedelta(days=days_ago))],
        conditions=[], allergies=[])


def test_stale_historical_drug_drug_suppressed():
    rep = evaluate(_rs_hist(["simvastatin"], "clarithromycin", days_ago=400),
                   now=date.today())
    assert not any({p["name"] for p in f.participants} == {"clarithromycin", "simvastatin"}
                   for f in rep.findings)


def test_long_acting_bypasses_cutoff():
    rep = evaluate(_rs_hist(["simvastatin"], "amiodarone", days_ago=400), now=date.today())
    assert any({p["name"] for p in f.participants} == {"amiodarone", "simvastatin"}
               for f in rep.findings)
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: FAIL — `now` kwarg unknown / stale not suppressed

- [ ] **Step 3: Implement recency filter**

```python
# engine.py
from datetime import date

HISTORICAL_DDI_WINDOW_DAYS = 183


def _is_stale(med, attrs: AttributeIndex, now: date) -> bool:
    if med.provenance != "historical" or med.last_seen_date is None:
        return False
    a = attrs.get(med.normalized_name)
    if a and a.long_acting:
        return False
    window = HISTORICAL_DDI_WINDOW_DAYS
    if a and a.induction_offset_days:
        window = max(window, a.induction_offset_days)
    return (now - med.last_seen_date).days > window


def _suppress_stale_dd(findings: list[Finding], rs: ReviewSet,
                       attrs: AttributeIndex, now: date) -> list[Finding]:
    by_name = {m.normalized_name: m for m in rs.meds}
    kept = []
    for f in findings:
        if f.type == "drug_drug":
            meds = [by_name.get(p["name"]) for p in f.participants if p["kind"] == "drug"]
            if any(m and _is_stale(m, attrs, now) for m in meds):
                continue
        kept.append(f)
    return kept
```

Update `evaluate` signature and body:

```python
def evaluate(rs: ReviewSet, *, rules=None, attrs=None, now: date | None = None) -> InteractionReport:
    ...
    now = now or date.today()
    findings = _dedup(findings)
    findings = _suppress_stale_dd(findings, rs, attrs, now)
    return build_report(findings)
```

- [ ] **Step 4: Run tests**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/engine.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): recency cutoff for stale historical drug-drug (long-acting/TDI bypass)"
```

---

## Task 11b: Lab/age-conditional severity modifiers (legacy CDS parity)

Ports the remaining legacy rules whose severity depends on labs/age: metformin×eGFR,
benzodiazepine×age, and ACE-inhibitor×potassium-sparing-diuretic escalated by serum
potassium. Adds a `drug_context` rule kind and a `lab_escalation` field on drug_drug
rules. Reuses the class mappings already in `normalizer.GENERIC_CLASSES`.

**Files:**
- Modify: `services/ai/clinical_decision_support/interaction/data/interaction_rules.yaml`
- Modify: `services/ai/clinical_decision_support/interaction/rules.py`
- Modify: `services/ai/clinical_decision_support/interaction/engine.py`
- Test: `tests/unit/test_interaction_engine.py`

- [ ] **Step 1: Append rules to the YAML**

```yaml
- kind: drug_context
  left: biguanide
  requires_lab: egfr
  thresholds:
    - { max: 30, severity: Contraindicated, note: "eGFR <30: metformin contraindicated" }
    - { max: 44, severity: Major, note: "eGFR 30–44: review dose with prescriber" }
  missing_severity: Moderate
  mechanism: "Reduced renal clearance → metformin accumulation; lactic-acidosis risk."
  action: "Verify a recent eGFR before continuation."
  evidence: ["FDA DSC: metformin & renal function"]
  source: curated

- kind: drug_context
  left: benzodiazepine
  requires_age_min: 65
  severity: Major
  mechanism: "Beers: benzodiazepines raise falls/cognitive/MVA risk in older adults."
  action: "Review necessity and duration; taper if discontinuing."
  evidence: ["AGS Beers 2023"]
  source: curated

- kind: drug_drug
  left: ace_inhibitor
  right: potassium_sparing_diuretic
  severity: Moderate
  lab_escalation:
    lab: potassium
    steps: [{ min: 5.5, severity: Contraindicated }, { min: 5.0, severity: Major }]
  mechanism: "Additive potassium retention → hyperkalemia."
  action: "Check serum potassium and renal function; review need."
  evidence: ["Lexicomp: ACEI + potassium-sparing diuretics"]
  confidence: 0.9
  source: curated
```

- [ ] **Step 2: Write failing tests (append to test_interaction_engine.py)**

```python
def test_metformin_low_egfr_contraindicated():
    from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
    from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S
    rs = assemble_review_set(current_rx=[NS(drug_name="metformin")], meds=[],
                             conditions=[], allergies=[], labs={"egfr": 25.0})
    rep = evaluate(rs)
    assert any(f.type == "drug_context" and f.severity is S.CONTRAINDICATED for f in rep.findings)


def test_benzodiazepine_elderly_major():
    from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
    rs = assemble_review_set(current_rx=[NS(drug_name="diazepam")], meds=[],
                             conditions=[], allergies=[], age=72)
    rep = evaluate(rs)
    assert any(f.type == "drug_context" for f in rep.findings)


def test_acei_spironolactone_potassium_escalation():
    from services.ai.clinical_decision_support.interaction.review_set import assemble_review_set
    from services.ai.clinical_decision_support.interaction.severity import InteractionSeverity as S
    rs = assemble_review_set(current_rx=[NS(drug_name="lisinopril"), NS(drug_name="spironolactone")],
                             meds=[], conditions=[], allergies=[], labs={"potassium": 5.6})
    rep = evaluate(rs)
    dd = [f for f in rep.findings if f.type == "drug_drug" and
          {p["name"] for p in f.participants} == {"lisinopril", "spironolactone"}]
    assert dd and dd[0].severity is S.CONTRAINDICATED
```

(Ensure `lisinopril` → `ace_inhibitor`, `spironolactone` → `potassium_sparing_diuretic`,
`metformin` → `biguanide`, `diazepam` → `benzodiazepine` exist in
`normalizer.GENERIC_CLASSES`; they are used by the legacy engine so most already do —
add any missing.)

- [ ] **Step 3: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -k "egfr or elderly or potassium" -q`
Expected: FAIL

- [ ] **Step 4: Extend the rule loader** (`rules.py`)

```python
# add fields to InteractionRule
    requires_lab: str | None = None
    thresholds: tuple[dict, ...] = ()
    missing_severity: InteractionSeverity | None = None
    requires_age_min: int | None = None
    lab_escalation: dict | None = None
```

```python
# in _parse, after the base fields:
    sev_missing = e.get("missing_severity")
    lab_esc = e.get("lab_escalation")
    if lab_esc:
        lab_esc = {"lab": lab_esc["lab"],
                   "steps": [{"min": s["min"], "severity": normalize_severity(s["severity"])}
                             for s in lab_esc["steps"]]}
    return InteractionRule(
        kind=e["kind"], left=e["left"], right=e.get("right", ""),
        severity=normalize_severity(e.get("severity", "Moderate")),
        mechanism=e.get("mechanism", ""), action=e.get("action", ""),
        evidence=tuple(e.get("evidence", [])), confidence=float(e.get("confidence", 0.8)),
        source=e.get("source", "curated"),
        requires_lab=e.get("requires_lab"),
        thresholds=tuple({"max": t["max"], "severity": normalize_severity(t["severity"]),
                          "note": t.get("note", "")} for t in e.get("thresholds", [])),
        missing_severity=normalize_severity(sev_missing) if sev_missing else None,
        requires_age_min=e.get("requires_age_min"),
        lab_escalation=lab_esc,
    )
```

```python
# add to RuleIndex: a drug_context tuple + accessor
    drug_context: tuple[InteractionRule, ...]

    def find_drug_context(self, drug_tokens: set[str]) -> list[InteractionRule]:
        return [r for r in self.drug_context if r.left in drug_tokens]
```

```python
# in load_rule_index, build it:
    return RuleIndex(
        drug_drug=tuple(r for r in rules if r.kind == "drug_drug"),
        drug_disease=tuple(r for r in rules if r.kind == "drug_disease"),
        drug_context=tuple(r for r in rules if r.kind == "drug_context"),
    )
```

(Update the three existing `test_interaction_rules.py` constructions if they build
`RuleIndex` directly — they use `load_rule_index()`, so no change needed.)

- [ ] **Step 5: Apply in the engine** (`engine.py`)

```python
def _apply_lab_escalation(rule, base, labs) -> InteractionSeverity:
    esc = rule.lab_escalation
    if not esc:
        return base
    val = labs.get(esc["lab"])
    if val is None:
        return base
    for s in esc["steps"]:               # steps are highest-threshold first
        if val >= s["min"]:
            return s["severity"]
    return base


def _drug_context(rs: ReviewSet, rules: RuleIndex) -> list[Finding]:
    out = []
    for med in rs.meds:
        for r in rules.find_drug_context(set(med.classes)):
            if r.requires_age_min is not None:
                if rs.age is None or rs.age < r.requires_age_min:
                    continue
                sev, factors = r.severity, [f"age {rs.age} ≥ {r.requires_age_min}"]
            elif r.requires_lab:
                val = rs.labs.get(r.requires_lab)
                if val is None:
                    sev, factors = (r.missing_severity or InteractionSeverity.MINOR), \
                        [f"{r.requires_lab} not on file"]
                else:
                    sev = r.missing_severity or InteractionSeverity.MINOR
                    factors = [f"{r.requires_lab}={val:g}"]
                    for t in r.thresholds:
                        if val <= t["max"]:
                            sev = t["severity"]
                            break
                    else:
                        continue   # value above all thresholds → no alert
            else:
                sev, factors = r.severity, []
            out.append(_finding(
                rule_id=f"ctx:{r.left}", type="drug_context", severity=sev,
                direction="toxicity", mechanism=r.mechanism, actions=[r.action],
                evidence=r.evidence, grade="Established", source=r.source,
                participants=[_p(med)], factors=factors, confidence=r.confidence))
    return out
```

Update `_explicit_drug_drug` to apply lab escalation, and add the context pass to
`evaluate`:

```python
# inside _explicit_drug_drug, replace severity=r.severity with the escalated value:
            sev = _apply_lab_escalation(r, r.severity, rs.labs)
            out.append(_finding(
                rule_id=f"dd:{r.left}-{r.right}", type="drug_drug", severity=sev,
                base=r.severity, direction="toxicity", mechanism=r.mechanism,
                actions=[r.action], evidence=r.evidence, grade="Established",
                source=r.source, participants=[_p(a), _p(b)], confidence=r.confidence))
```

```python
# in evaluate(), add before _drug_disease:
    findings += _drug_context(rs, rules)
```

(`_apply_lab_escalation` needs `rs.labs`; `_explicit_drug_drug` already receives `rs`.)

- [ ] **Step 6: Run tests**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_engine.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add services/ai/clinical_decision_support/interaction/rules.py \
        services/ai/clinical_decision_support/interaction/engine.py \
        services/ai/clinical_decision_support/interaction/data/interaction_rules.yaml \
        services/ai/clinical_decision_support/normalizer.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): lab/age-conditional severity modifiers (eGFR/age/K+ parity)"
```

---

## Task 12: Endpoint `POST /cds/interaction-report` + migration parity

**Files:**
- Modify: `services/platform/routers/cds.py`
- Test: `tests/unit/test_interaction_report_endpoint.py`

- [ ] **Step 1: Write the failing test** (mirrors the existing `test_second_brain_endpoint.py` style — exercises the route function with a fake DB session and a patient stub)

```python
# tests/unit/test_interaction_report_endpoint.py
import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Result:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _FakeDB:
    def __init__(self, patient, rx, meds):
        self._seq = [_Result([patient]), _Result(rx), _Result(meds)]
    async def execute(self, *_a, **_k): return self._seq.pop(0)


def test_interaction_report_endpoint_returns_findings():
    pid = uuid4()
    patient = NS(id=pid, pharmacy_id=uuid4(), conditions=["peptic_ulcer_disease"],
                 date_of_birth=None, is_deleted=False)
    rx = [NS(drug_name="ibuprofen")]
    db = _FakeDB(patient, rx, meds=[])
    staff = NS(pharmacy_id=patient.pharmacy_id, id=uuid4())
    body = cds.InteractionReportRequest(patient_id=pid)
    out = asyncio.run(cds.interaction_report(body, staff=staff, db=db))
    assert "summary" in out and "findings" in out
    assert any(f["type"] == "drug_disease" for f in out["findings"])
```

- [ ] **Step 2: Run to verify failure**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_report_endpoint.py -q`
Expected: FAIL — `InteractionReportRequest`/`interaction_report` missing

- [ ] **Step 3: Add the endpoint to `cds.py`**

```python
# add near the other request models in services/platform/routers/cds.py
from dataclasses import asdict
from services.ai.clinical_decision_support.interaction.engine import evaluate as eval_interactions
from services.ai.clinical_decision_support.interaction.review_set import build_review_set


class InteractionReportRequest(BaseModel):
    patient_id: UUID
    rx_ids: list[UUID] | None = None


@router.post("/interaction-report")
async def interaction_report(
    body: InteractionReportRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == body.patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    review_set = await build_review_set(
        db=db, patient=patient, pharmacy_id=staff.pharmacy_id, rx_ids=body.rx_ids)
    report = eval_interactions(review_set)
    return {
        "summary": report.summary,
        "degraded": report.degraded,
        "findings": [_finding_json(f) for f in report.findings],
        "pharmacist_verification_notice": (
            report.findings[0].pharmacist_verification_notice if report.findings
            else "Advisory clinical decision support only."),
    }


def _finding_json(f) -> dict:
    d = asdict(f)
    d["severity"] = f.severity.value
    d["base_severity"] = f.base_severity.value
    return d
```

Confirm `HTTPException` is imported in `cds.py` (add `from fastapi import HTTPException` if absent).

- [ ] **Step 4: Run the endpoint test**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_report_endpoint.py -q`
Expected: PASS (1 passed)

- [ ] **Step 5: Add migration-parity test** (the legacy pairs still fire via the new engine)

```python
# append to tests/unit/test_interaction_engine.py
import pytest

@pytest.mark.parametrize("pair", [
    ("clarithromycin", "simvastatin"),
    ("warfarin", "ibuprofen"),
    ("paroxetine", "tramadol"),     # ssri + serotonergic_opioid via classes
])
def test_legacy_pairs_still_fire(pair):
    rep = evaluate(_rs(list(pair)))
    assert any(f.type == "drug_drug" for f in rep.findings), pair
```

(If `tramadol` lacks the `serotonergic_opioid` class, add it to `normalizer.GENERIC_CLASSES`:
`"tramadol": {"opioid", "serotonergic_opioid"}`.)

- [ ] **Step 6: Run the full interaction suite**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py -q`
Expected: PASS (all)

- [ ] **Step 7: Commit**

```bash
git add services/platform/routers/cds.py \
        services/ai/clinical_decision_support/normalizer.py \
        tests/unit/test_interaction_report_endpoint.py \
        tests/unit/test_interaction_engine.py
git commit -m "feat(cds): POST /cds/interaction-report endpoint + legacy parity tests"
```

---

## Task 13: Graph refresh + final verification

- [ ] **Step 1: Run the entire interaction test suite + existing CDS tests**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_interaction_*.py tests/unit/test_cds_engine.py -q`
Expected: PASS (all)

- [ ] **Step 2: Verify the app imports cleanly (router registered)**

Run: `/Users/sashad85/miniforge3/bin/python -c "import services.platform.routers.cds as c; print([r.path for r in c.router.routes])"`
Expected: list includes `/interaction-report` and `/evaluate`

- [ ] **Step 3: Refresh the knowledge graph**

Run: `graphify update . >/dev/null 2>&1 &`

- [ ] **Step 4: Final commit (if any uncommitted changes remain)**

```bash
git add -A && git commit -m "chore(cds): finalize interaction engine sub-project #1" || echo "nothing to commit"
```

---

## Notes for the implementer

- **Determinism:** `evaluate()` must never call the network or an LLM. Keep it pure over
  `ReviewSet` + the two indexes.
- **Severity is lookup-only:** the engine infers *which* pairs interact and the magnitude
  band, but never a severity number — that always comes from a rule or the matrix in
  `severity.py` plus deterministic modifiers.
- **Precedence:** `curated > ddinter > inferred_mechanistic` — `_dedup` enforces this; never
  emit both a curated and an inferred finding for the same participant set.
- **Coverage honesty:** unknown drugs (no attribute/class) simply produce no findings — do not
  fail. A later task in sub-project #2 surfaces a "not fully checked" coverage note in the UI.
- **Out of scope (deferred):** cumulative ≥3-drug roll-ups, risk scoring, safe-alternative
  recommender (bundle B); caching/memoization (bundle C); hot-reload + lint + telemetry
  (bundle D); DDInter import (sub-project #3); client-vs-patient resolution (#4).
