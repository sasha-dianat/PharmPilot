# Interaction Engine — Design Spec (Sub-project #1)

**Date:** 2026-06-21
**Status:** Approved design, pre-implementation
**Scope:** Sub-project #1 of the "automatic prescription interaction checker" program.

## Program context (the whole feature)

The pharmacist's middle panel (Verification Center) should automatically check
the patient's prescription for drug interactions and show a concise,
severity-graded report — so the pharmacist never has to navigate
Dashboards → Second Brain → search patient. The check must span **current Rx ×
active medications × historical/discontinued medications × past & present medical
conditions**, covering drug–drug, drug–disease, therapeutic duplication, and
drug–allergy.

Decomposed into four sub-projects, each spec'd/built/verified independently:

1. **Interaction engine + curated class ruleset + unified severity model** ← THIS SPEC
2. Auto "Interaction Report" card in the middle panel (consumes #1's contract)
3. DDInter ingestion via Google Colab (enriches #1's knowledge, runs off-machine)
4. Client-vs-patient resolution via PersonLinkGraph (deferred; needs identity data)

Build order: 1 → 2 → 3, then 4 later.

### Program-level decisions

- **Severity scale:** Contraindicated / Major / Moderate / Minor. Deterministic —
  an LLM never assigns severity.
- **Knowledge:** curated class-based ruleset as the safety core, later enriched by
  an imported open DDI dataset (**DDInter 2.0** — free, ~240k DDIs, already graded
  Major/Moderate/Minor). DrugBank is licensed → excluded. Everything works offline.
- **Heavy ingestion** is delegated to Google Colab (this machine is too weak).
- **Patient resolution:** for #1/#2, checks run against the Rx's `patient_id`;
  client-vs-patient (#4) comes later.

## Sub-project #1 goals

A deterministic, offline, in-process engine that takes a patient + the Rx under
review and returns a structured `InteractionReport`. Ships clinical value with the
curated ruleset alone, before any large dataset exists. No LLM in this path.

### Enhancement scope (folded in vs. deferred)

Per design review, **bundle A (mechanistic knowledge)** is folded into this
sub-project. Bundles **B (reasoning intelligence)**, **C (accelerators)**, and
**D (governance)** are deferred to the enrichment track (alongside #3).

Bundle A folded in (full PK/PD model, professor review items 1–7): per-enzyme PK
attribute layer (CYP + transporters, FDA AUC-ratio strength, reversible/TDI,
induction time-course), **direction-of-effect** modeling (prodrug/active-metabolite),
**phenoconversion**, absorption-phase (chelation + pH) and renal-competition
interactions, mechanistic inference with **predicted-magnitude bands**, PD axes with
**effect direction** (additive/synergistic/antagonistic, MAOI, QT conditional gating),
NTI flagging, special-population + dose/route severity modifiers, combination-product
decomposition, and per-rule **evidence grading**.

**A↔B coupling — explicit line:** PD axes are only useful if something consumes
them. So #1 includes **basic pairwise** PD/PK inference (drug-vs-drug on a shared
mechanism). The **cumulative ≥3-drug roll-ups** (e.g. "3 QT-prolongers → additive
QT"), composite **risk scoring**, multi-source **arbitration**, and the
**safe-alternative recommender** are bundle B and remain deferred. #1 stores all
attributes so B can light up without re-modeling.

### Non-goals (this sub-project)

- No UI (sub-project #2).
- No DDInter import (sub-project #3) — but the knowledge index is shaped so
  DDInter rules append without engine changes.
- No client-vs-patient resolution (sub-project #4).
- No live LLM. (Optional prose summary is a later add-on in #2.)
- No cumulative additive roll-ups / risk score / safe-alternative recommender
  (bundle B). No caching/memoization accelerators (bundle C). No hot-reload bundle,
  validation lint, or coverage telemetry (bundle D). No RxNorm/ATC anchoring (#3).

## Module placement

Extend the existing `services/ai/clinical_decision_support/` package (already holds
`schema.py`, `engine.py`, `rules.py`, `normalizer.py`, the `/cds/evaluate` router,
and `tests/unit/test_cds_engine.py`). The 6 hardcoded Python rules migrate into the
new data-driven knowledge file.

## Components

### 0. Drug attribute layer — `drug_attributes.py` + `data/drug_attributes.yaml`  (bundle A)

The per-drug pharmacology model that makes coverage *generative*. Loaded once into
an in-memory index keyed by normalized ingredient. The schema below is the **full
PK/PD model** (professor review, items 1–7); seed data is high-yield first and
grown via #3.

```yaml
- ingredient: clopidogrel
  classes: [p2y12_inhibitor, antiplatelet]
  atc: B01AC04
  prodrug: true                       # activity requires bioactivation
  activating_enzyme: CYP2C19          # inhibiting THIS = loss of efficacy
  pk:
    enzymes:
      - { enzyme: CYP2C19, role: substrate, fm: 0.5, yields: active }
    transporters: []
    elimination_route: hepatic
  pgx_enzyme: CYP2C19                  # phenoconversion-relevant
  pd:
    bleeding: { strength: moderate, direction: additive }

- ingredient: clarithromycin
  classes: [macrolide]
  pk:
    enzymes:
      - { enzyme: CYP3A4, role: inhibitor, strength: strong,
          inhibition_type: mechanism_based }   # TDI → effect persists after stop
    transporters: [{ name: P-gp, role: inhibitor, strength: moderate }]
  pd: { qt: { tier: conditional } }            # CredibleMeds conditional risk

- ingredient: simvastatin
  classes: [statin]
  pk:
    enzymes: [{ enzyme: CYP3A4, role: substrate, fm: 0.8, yields: parent }]
    transporters: [{ name: OATP1B1, role: substrate, organ: hepatic_uptake }]
  nti: { is_nti: true, consequence: toxicity }  # rhabdomyolysis

- ingredient: rifampin
  classes: [rifamycin]
  pk:
    enzymes: [{ enzyme: CYP3A4, role: inducer, strength: strong }]
    induction: { onset_days: 7, offset_days: 14 }  # delayed on/offset

- ingredient: levothyroxine
  classes: [thyroid_hormone]
  absorption: { chelation_cations: [Ca, Mg, Al, Fe], separation_hours: 4 }

- ingredient: phenelzine
  classes: [maoi]
  pd: { serotonergic: { subtype: maoi } }        # MAOI + serotonergic = contraindicated

- ingredient: amiodarone
  classes: [antiarrhythmic]
  pk:
    enzymes: [{ enzyme: CYP3A4, role: inhibitor, strength: moderate },
              { enzyme: CYP2D6, role: inhibitor, strength: moderate }]
  pd: { qt: { tier: known } }
  nti: { is_nti: true, consequence: toxicity }
  long_acting: true                              # t½ ~58 d → bypasses §5 cutoff
```

**PK model (items 1–6):**
- `enzymes[]` — CYP (3A4/2D6/2C9/2C19/1A2…) and the role:
  - `substrate` carries **per-enzyme `fm`** (fraction metabolized by *that* enzyme)
    and `yields: parent | active` (parent active vs prodrug → drives direction).
  - `inhibitor`/`inducer` carry `strength` defined by the **FDA AUC-ratio standard**
    (inhibitor: strong ≥5×, moderate 2–5×, weak 1.25–2×; inducer: strong ≥80% AUC↓,
    moderate 50–80%, weak 20–50%) and, for inhibitors, `inhibition_type:
    reversible | mechanism_based` (TDI persists post-discontinuation).
  - `induction: {onset_days, offset_days}` captures the delayed on/offset.
- `transporters[]` — P-gp, OATP1B1/1B3, BCRP, OCT2/MATE, OAT — with `organ`
  (hepatic_uptake / intestinal / bbb / renal_secretion) and consequence.
- `prodrug` + `activating_enzyme`, `active_metabolite`, `pgx_enzyme` — enable
  direction-of-effect and **phenoconversion** reasoning.
- `elimination_route` (hepatic/renal/biliary) + renal handling for tubular-secretion
  competition (lithium, methotrexate, digoxin).
- `absorption` — `chelation_cations[]` (+ `separation_hours`) and
  `ph_dependent: acid_requiring` for pre-systemic interactions.

**PD model (item 7):** each axis carries a **`direction`** (`additive | synergistic |
antagonistic`) and a strength/tier, so therapeutic opposition is detected, not just
additive risk:
- `qt: {tier: known | possible | conditional}` (CredibleMeds); conditional agents
  gate on hypokalemia/hypomagnesemia/bradycardia or a PK level rise.
- `serotonergic: {subtype: maoi | sri | releaser | weak}` (MAOI + serotonergic →
  Contraindicated).
- `anticholinergic: {acb: 0–3}`, `cns_depression`, `bleeding`, `nephrotoxic`,
  `raas` (for triple-whammy tagging, used in B), `hyperkalemia`, `hypoglycemia`,
  `hyponatremia`, `hepatotoxic` — each with `direction`.

**Other:** `nti: {is_nti, consequence: toxicity | efficacy_loss}`, `long_acting`
(single source of truth for §5's bypass), pregnancy/lactation + renal/hepatic dose
flags, and `combination_of` for decomposition. Gaps surface via §"Error handling"
coverage notes.

### 1. Knowledge store — `interaction_kb.py` + `data/interaction_rules.yaml`

- Versioned in-repo YAML, loaded once into an in-memory index at startup.
- Rule shape:
  ```yaml
  - kind: drug_drug          # drug_drug | drug_disease
    left: anticoagulant      # class OR specific normalized drug
    right: nsaid
    severity: Major          # Contraindicated | Major | Moderate | Minor
    mechanism: "Additive bleeding risk; NSAID GI mucosal injury + platelet effects."
    action: "Assess GI bleed risk; consider gastroprotection or alternative analgesic."
    evidence: ["Lexicomp: NSAIDs + anticoagulants"]
    confidence: 0.92
    source: curated          # curated | ddinter (appended later)
  ```
- Duplicate-therapy is **derived** (two meds sharing a therapeutic class), not authored.
- Index supports O(1) lookup by normalized (left,right) and by class membership.
- The legacy 6 rules (clarithromycin+simvastatin, ACEI+spironolactone, NSAID+anticoag,
  metformin+low-eGFR, benzo+elderly, SSRI+tramadol) are re-expressed here. Lab/age-
  conditional severity (e.g. K⁺ ≥ 5.5 → Contraindicated) is retained as optional
  `severity_modifiers` on a rule (keeps deterministic patient-specific escalation).

### 2. Unified severity — `severity.py`

- Canonical `InteractionSeverity` enum: `CONTRAINDICATED > MAJOR > MODERATE > MINOR`.
- `normalize_severity(raw)` folds legacy CDS (CRITICAL/HIGH/MODERATE/LOW/INFO) and
  DDInter (Major/Moderate/Minor) into the canonical scale.
  Mapping: CRITICAL→Contraindicated (when rule flagged contraindicated) else Major;
  HIGH→Major; MODERATE→Moderate; LOW/INFO→Minor.
- Frontend `severity.ts` tokens get a matching 4-tier mapping in sub-project #2.

**Direction of clinical effect (computed first, item-1 correctness fix).** Before
severity, the engine derives the *direction* from `role × yields`:

| Perpetrator role | Victim is… | Direction | Clinical consequence |
|---|---|---|---|
| Inhibitor | parent-active substrate | ↑ exposure | **toxicity** |
| Inhibitor | prodrug (inhibits activating enzyme) | ↓ active | **efficacy loss** |
| Inducer | parent-active substrate | ↓ exposure | **efficacy loss** |
| Inducer | prodrug | ↑ active | **toxicity** |

The finding's `mechanism`/`clinical_problem` is phrased from the direction
(clopidogrel + omeprazole → "reduced antiplatelet effect", never "increased levels").

**Predicted-magnitude band → base severity (item 2).** Map inhibitor/inducer
`strength` × victim `fm` on the inhibited pathway to a predicted AUC fold-change band,
then to severity (deterministic lookup, never inferred):

| Strength | Victim fm (inhibited pathway) | Predicted AUC band | Base severity |
|---|---|---|---|
| Strong inhibitor | high (≥0.5) | ~≥5× ↑ | Major |
| Strong inhibitor | low (<0.5) | ~2–5× ↑ | Moderate |
| Moderate inhibitor | high | ~2–5× ↑ | Moderate |
| Moderate/weak | low | <2× | Minor |
| Strong inducer | high | ~≥80% ↓ | Major (efficacy loss) |
| Moderate inducer | high | ~50–80% ↓ | Moderate |

The band is surfaced verbatim in the finding (`predicted_magnitude`) — Lexicomp-grade
output ("may increase exposure ~3–5×").

**Severity modifiers (deterministic, applied after base lookup):**
- **NTI victim** → +1 step (consequence-aware: a toxicity-NTI escalates toxicity
  findings; an efficacy-loss direction on an NTI also escalates). Capped at
  Contraindicated.
- **Renal/hepatic impairment** (labs/flags) relevant to the victim's elimination → +1.
- **Pregnancy** with a pregnancy-risk drug → +1.
- **QT conditional-risk gating** — a `conditional` QT agent only contributes when
  hypokalemia/hypomagnesemia/bradycardia is present *or* a PK interaction raises its
  level; otherwise downgraded to informational.
- **Dose/route attenuation** → −1 for clearly low-risk forms (topical route,
  cardioprotective low-dose aspirin, chelation pairs separated adequately) when the
  rule/attribute declares it dose/route-sensitive.

Modifiers are pure functions of recorded attributes — never inferred, always listed in
`patient_specific_factors`. MAOI + serotonergic and other hard contraindications are
set directly by rule, not via the matrix.

### 3. Review set assembly — extend `_load_context` → `build_review_set`

Produces a `ReviewSet`:
- `meds: list[ReviewMed]` where `ReviewMed = {normalized_name, classes, provenance,
  last_seen_date}` and `provenance ∈ {current_rx, active, historical}`.
  - `current_rx` = the prescription(s) under verification.
  - `active` = active `Medication` rows (existing behavior).
  - `historical` = `Medication` rows with non-active status, not deleted (NEW).
- `conditions: list[Condition]` = `{concept, status ∈ {active, past}, onset_date?}`
  — current AND past (NEW; drug–disease must see resolved conditions).
- `allergies: list[str]` (existing).
- Labs/age/renal/hepatic (existing CDSContext fields retained for modifiers).

### 4. Checks — `engine.evaluate(review_set) -> InteractionReport`

Run over the combined med list, dedup by (sorted participant key), highest
severity wins. **Combination products are decomposed to ingredients first**, so a
combo pill contributes each ingredient to pairing and duplication.

- **Drug–drug (explicit):** every unique unordered pair `(i<j)`; look up
  specific-drug then class-level rules from the knowledge store.
- **Drug–drug (mechanistic inference, bundle A):** for each ordered pair, derive
  unlisted interactions from the attribute layer. Each path computes **direction**
  (§2) first, then magnitude/severity:
  - *Metabolic PK:* A inhibits/induces enzyme E; B is a substrate of E → derive
    direction from B's `yields` (parent vs prodrug) and A's role; severity from the
    predicted-magnitude band × B's fm on E, escalated if B is NTI.
  - *Phenoconversion:* a **strong** inhibitor of B's `pgx_enzyme` converts a normal
    metabolizer to a *functional poor metabolizer* — for a prodrug this yields an
    efficacy-loss finding (codeine/clopidogrel pattern); for a parent-active NTI, a
    toxicity finding.
  - *Transporter PK:* A inhibits transporter T; B is a substrate of T → finding
    framed by T's organ (OATP1B1 → ↑statin → myopathy; P-gp → ↑digoxin; OCT2/MATE or
    OAT → ↓renal secretion of B).
  - *Absorption:* B requires acid and A is acid-suppressing → reduced-absorption
    (efficacy loss); B is chelated by A's `chelation_cations` → reduced absorption,
    **action = separate by `separation_hours`**, severity attenuated if separable.
  - *Renal competition:* A reduces renal clearance of a renally-eliminated NTI B
    (lithium, methotrexate, digoxin) → toxicity finding.
  - *PD:* if A and B share a PD axis, branch on **direction**:
    *additive/synergistic* (two QT, opioid+benzo synergy) → cumulative-risk finding
    at that axis; *antagonistic* (NSAID vs antihypertensive, anticholinergic vs
    AChEI) → **therapeutic-opposition / efficacy-loss** finding. MAOI + serotonergic
    is a hard **Contraindicated**.
  - Inferred findings are tagged `source: inferred_mechanistic`, `evidence_grade:
    Predicted`, with lower base confidence, and are **always overridden** by an
    explicit curated/DDInter rule for the same participants (precedence:
    curated > ddinter > inferred).
- **Drug–disease:** each med × each condition (active or past).
- **Duplicate-therapy:** pairs sharing a therapeutic class/ingredient → Moderate.
- **Drug–allergy:** each med × allergies, plus a minimal cross-reactivity map
  (e.g. penicillin↔cephalosporin) → severity per map, default Major.

Pairwise PD inference here is the deliberate #1-scope payoff of the PD axes;
cumulative ≥3-drug PD roll-ups are bundle B (deferred).

### 5. Recency handling (drug–drug only)

- Default **6-month cutoff** (configurable `HISTORICAL_DDI_WINDOW_DAYS = 183`):
  a drug–drug interaction is **suppressed** when *both* participants' latest
  `last_seen_date` make at least one a `historical` med discontinued > window ago.
- **Drug–disease against past conditions is NEVER suppressed** (condition persists).
- **Long-half-life / depot bypass** — drugs flagged `long_acting` in the attribute
  layer (amiodarone, fluoxetine, leflunomide, depot antipsychotics, denosumab,
  bisphosphonates) keep full drug–drug evaluation regardless of discontinuation date.
- **Pharmacology-aware persistence (items 2–3):** the cutoff also does NOT suppress
  a perpetrator whose effect outlasts dosing — **mechanism-based (TDI) inhibitors**
  and **inducers** (whose `induction.offset_days` extends the active window). The
  effective window for such perpetrators = `max(HISTORICAL_DDI_WINDOW_DAYS,
  offset_days)`. A rifampin or clarithromycin course stopped "recently" is still live.
- Every finding carries a `recency_note` (e.g. "historical — last dispensed 2024-02";
  or "inducer effect persists ~2 wk after stop").

### 6. Output contract — `InteractionReport`

```python
@dataclass
class Finding:
    rule_id: str
    type: str                # drug_drug | drug_disease | duplicate_therapy | drug_allergy
    severity: str            # canonical 4-tier (after modifiers)
    base_severity: str       # before patient-specific modifiers (auditability)
    direction: str           # toxicity | efficacy_loss | additive_risk | opposition
    predicted_magnitude: str | None  # e.g. "~3–5× ↑ exposure" / "~80% ↓ exposure"
    onset_offset: str | None # e.g. "delayed onset ~1 wk; persists ~2 wk after stop"
    participants: list[dict]  # [{name, kind: drug|condition, provenance, last_seen}]
    mechanism: str           # human-readable; for inferred: the PK/PD pathway
    mechanism_basis: str | None  # e.g. "CYP3A4 inhibition (strong, TDI) of substrate fm=0.8"
    clinical_problem: str
    suggested_actions: list[str]
    evidence_sources: list[str]
    evidence_grade: str      # Established | Probable | Theoretical | Predicted
    source: str              # curated | ddinter | inferred_mechanistic
    patient_specific_factors: list[str]  # which modifiers fired + why
    confidence: float
    recency_note: str | None
    pharmacist_verification_notice: str

@dataclass
class InteractionReport:
    summary: dict            # {"Contraindicated": n, "Major": n, "Moderate": n, "Minor": n}
    findings: list[Finding]  # sorted: severity desc, then provenance (current_rx first)
    generated_at: str
    degraded: bool           # True if knowledge index failed to load
```

### 7. Endpoint

Add a **new** `POST /cds/interaction-report` (leaving `/cds/evaluate` untouched for
backward compatibility) accepting `{patient_id, rx_ids?}` and returning
`InteractionReport`. `rx_ids` defaults to all of the patient's `current_rx`-state
prescriptions (the basket under verification). Reuses existing
`require_permission("clinical:read")` + tenant scoping. Response is a typed
Pydantic model mirroring `InteractionReport`.

## Data flow

```
startup → load drug_attributes.yaml + interaction_rules.yaml → in-memory indexes

Rx claim → /cds/interaction-report {patient_id, rx_ids}
  → build_review_set(db, patient, pharmacy)        # current_rx + active + historical + conditions + allergies
       → decompose combination products to ingredients
  → engine.evaluate(review_set, rules_index, attr_index)   # in-process, deterministic
       explicit:   drug_drug | drug_disease | duplicate | allergy
       inferred:   PK metabolic + phenoconversion + transporter + absorption + renal,
                   PD-axis pairwise (additive/synergistic/antagonistic)   (bundle A)
       direction:  role × yields → toxicity | efficacy_loss | opposition
       precedence: curated > ddinter > inferred_mechanistic
       severity:   magnitude band → modifiers (NTI, renal/hepatic, pregnancy, QT-gating, dose/route)
       recency:    drug–drug cutoff (long_acting + TDI/inducer-offset bypass); drug–disease never suppressed
  → InteractionReport (severity-sorted)            # no network, no LLM
```

## Error handling

- Knowledge index fails to load → `degraded: true`, empty findings, logged warning;
  endpoint still 200 so the panel renders a "report unavailable" state (#2).
- Unknown drug (no normalization/class) → included in the list, contributes to no
  rules, and is reported under `missing_information`/coverage notes so the pharmacist
  knows it wasn't checked (no silent gaps).
- Per-check exceptions are caught and isolated so one bad rule can't sink the report.

## Testing

`tests/unit/test_interaction_engine.py`:
- Each check type produces the expected finding + severity.
- Severity normalization across all source scales.
- Provenance tagging (current_rx / active / historical) on `build_review_set`.
- Recency cutoff: stale historical drug–drug suppressed; drug–disease against a past
  condition retained; long-acting drug (per `drug_attributes` flag) bypasses cutoff.
- Dedup + highest-severity-wins.
- Patient-specific severity modifier (e.g. K⁺ ≥ 5.5 escalates to Contraindicated).
- Unknown drug surfaced in coverage notes (no silent miss).
- `degraded` path when the index is empty.
- Migration parity: the 6 legacy rules still fire with equivalent (re-mapped) severity.

Mechanistic layer (bundle A, full PK/PD model items 1–7):
- **Magnitude→severity**: strong-3A4-inhibitor × high-fm-3A4-substrate → predicted
  Major with `predicted_magnitude ≈ ≥5×` (clarithromycin × simvastatin reproduced
  *by mechanism*); every matrix cell verified for strength × fm.
- **Direction of effect (item 1)**: clopidogrel + omeprazole (2C19 inhibitor ×
  2C19-activated prodrug) → `direction: efficacy_loss`, NOT toxicity; rifampin +
  warfarin (inducer × parent-active) → efficacy_loss; inducer × prodrug → toxicity.
- **Phenoconversion**: strong 2D6 inhibitor + codeine (2D6 prodrug) → analgesic-
  failure finding.
- **Transporter**: OATP1B1 inhibitor × statin → myopathy framing; P-gp inhibitor ×
  digoxin → ↑digoxin.
- **Absorption**: levothyroxine + calcium → chelation finding with
  `action: separate by 4 h` and attenuated severity; acid-requiring drug + PPI →
  efficacy loss.
- **Renal competition**: NSAID/diuretic × lithium → toxicity finding.
- **PD direction**: two QT (additive); opioid+benzo (synergistic); NSAID ×
  antihypertensive → `direction: opposition` (efficacy loss); MAOI + serotonergic →
  Contraindicated; **QT conditional gating** — a conditional-risk QT drug contributes
  only with hypokalemia/bradycardia or a level-raising PK interaction.
- **NTI escalation**: inferred Major on an NTI victim → Contraindicated.
- **Recency persistence**: TDI inhibitor / inducer stopped within its `offset_days`
  is NOT suppressed; `long_acting` drug bypasses the cutoff.
- **Precedence**: explicit curated rule overrides the inferred finding for the same
  pair (one finding, source=curated); inferred dropped, not duplicated.
- **Combination decomposition**: a combo product triggers ingredient-level pairing
  and duplicate-therapy.
- Inferred findings always carry `evidence_grade: Predicted` and
  `source: inferred_mechanistic`.

## Determinism & safety invariants

- No network and no LLM in the evaluate path.
- **Severity is always data-driven** — looked up from rules or the mechanism×victim
  matrix, then adjusted by deterministic modifiers. The engine *infers interactions*
  (which pairs interact, by mechanism) but **never infers a severity number**.
- Inferred findings are explicitly labeled (`evidence_grade: Predicted`,
  `source: inferred_mechanistic`) and always yield to vetted explicit rules.
- Every finding cites evidence, grade, source, confidence, the mechanism basis, the
  modifiers that fired, and the verification notice — fully auditable.
- Aligns with the project's deterministic-first / LLM-prose-only architecture and the
  PHI egress scrubber already in place.
