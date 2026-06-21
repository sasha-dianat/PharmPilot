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

Bundle A folded in: PK attribute layer (CYP/transporter substrate·inhibitor·inducer
with fraction-metabolized), PD axis tags, **mechanistic inference** of unlisted
interactions, NTI flagging, special-population + dose/route severity modifiers,
combination-product decomposition, and per-rule **evidence grading**.

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
an in-memory index keyed by normalized ingredient. Per drug:

```yaml
- ingredient: clarithromycin
  classes: [macrolide]
  atc: J01FA09
  pk:
    cyp:
      - { enzyme: CYP3A4, role: inhibitor, strength: strong }
    transporters:
      - { name: P-gp, role: inhibitor, strength: moderate }
  pd: {}                       # PD axes when applicable
  nti: false
  combination_of: []          # ingredient list if this is a combo product

- ingredient: simvastatin
  classes: [statin]
  pk:
    cyp:
      - { enzyme: CYP3A4, role: substrate, fraction_metabolized: 0.8 }
  nti: false

- ingredient: amiodarone
  classes: [antiarrhythmic]
  pk:
    cyp: [{ enzyme: CYP3A4, role: inhibitor, strength: moderate },
          { enzyme: CYP2D6, role: inhibitor, strength: moderate }]
  pd: { qt: high }            # CredibleMeds "known risk" tier
  nti: true
  long_acting: true           # bypasses the recency cutoff (ties to §5)
```

- **PK axes:** CYP (3A4/2D6/2C9/2C19/1A2) and transporters (P-gp, OATP1B1, BCRP,
  OCT2/MATE) tagged `substrate | inhibitor | inducer` with `strength` and, for
  substrates, `fraction_metabolized`.
- **PD axes:** `qt`, `serotonergic`, `anticholinergic` (ACB score), `cns_depression`,
  `bleeding`, `nephrotoxic`, `hyperkalemia`, `hypoglycemia`, `hyponatremia`,
  `hepatotoxic` — each with a strength/tier.
- **NTI**, **long_acting** (depot/long-half-life bypass for §5), pregnancy/lactation
  and renal/hepatic dose flags, and `combination_of` for decomposition.
- Seeded for a high-yield starter drug set; gaps surface via §"Error handling"
  coverage notes. The `long_acting` flag here is the source of truth for §5's
  `LONG_ACTING_DRUGS` bypass (single definition, no duplication).

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

**Mechanism × victim matrix (bundle A)** — base severity for *inferred* PK findings,
a deterministic table (no inference of severity, only lookup):

| Inhibitor/inducer strength | Substrate fraction-metabolized | Base severity |
|---|---|---|
| Strong inhibitor | high (≥0.5) | Major |
| Strong inhibitor | low (<0.5) | Moderate |
| Moderate inhibitor | high | Moderate |
| Moderate/weak | low | Minor |
| Strong inducer (efficacy loss) | high | Moderate |

**Severity modifiers (deterministic, applied after base lookup):**
- **NTI victim** → +1 step (e.g. Major→Contraindicated), capped at Contraindicated.
- **Renal/hepatic impairment** (from labs/flags) relevant to the victim → +1 step.
- **Pregnancy** with a pregnancy-risk drug → +1 step.
- **Dose/route attenuation** → −1 step for clearly low-risk forms (e.g. topical route,
  cardioprotective low-dose aspirin) when the rule declares it dose/route-sensitive.

Modifiers are pure functions of recorded patient/drug attributes — never inferred,
always explained in the finding's `patient_specific_factors`.

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
  unlisted interactions from the attribute layer:
  - *PK:* if A is an inhibitor/inducer of enzyme/transporter E and B is a substrate
    of E → predicted finding. Severity from the **mechanism × victim matrix**
    (see §2): inhibitor strength × substrate `fraction_metabolized`, escalated when
    the victim is **NTI**. Inducers emit a *reduced-efficacy* finding.
  - *PD:* if A and B share a PD axis (both QT-prolonging, both serotonergic, both
    bleeding-risk, etc.) → predicted additive finding at that axis.
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
- **Long-half-life / depot exception list** (`LONG_ACTING_DRUGS`) bypasses the
  cutoff — e.g. amiodarone, fluoxetine, leflunomide, depot antipsychotics,
  denosumab, bisphosphonates. These keep full drug–drug evaluation regardless of
  discontinuation date.
- Every finding carries a `recency_note` (e.g. "historical — last dispensed 2024-02").

### 6. Output contract — `InteractionReport`

```python
@dataclass
class Finding:
    rule_id: str
    type: str                # drug_drug | drug_disease | duplicate_therapy | drug_allergy
    severity: str            # canonical 4-tier (after modifiers)
    base_severity: str       # before patient-specific modifiers (auditability)
    participants: list[dict]  # [{name, kind: drug|condition, provenance, last_seen}]
    mechanism: str           # human-readable; for inferred: the PK/PD pathway
    mechanism_basis: str | None  # e.g. "CYP3A4 inhibition (strong) of substrate fm=0.8"
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
       inferred:   PK (CYP/transporter) + PD-axis pairwise        (bundle A)
       precedence: curated > ddinter > inferred_mechanistic
       severity:   base lookup/matrix → modifiers (NTI, renal/hepatic, pregnancy, dose/route)
       recency:    drug–drug cutoff (long_acting bypass); drug–disease never suppressed
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

Mechanistic layer (bundle A):
- **PK inference**: strong-3A4-inhibitor × high-fm-3A4-substrate → predicted Major
  (e.g. clarithromycin × simvastatin reproduced *by mechanism*, independent of the
  explicit rule); matrix cells verified for each strength × fm combination.
- **NTI escalation**: inferred Major on an NTI victim → Contraindicated.
- **Inducer** path emits a reduced-efficacy finding, not a toxicity one.
- **PD pairwise**: two QT-prolongers / two serotonergic agents → predicted additive
  finding at the right axis.
- **Precedence**: an explicit curated rule overrides the inferred finding for the
  same pair (one finding, source=curated); inferred is dropped, not duplicated.
- **Combination decomposition**: a combo product triggers ingredient-level pairing
  and duplicate-therapy.
- **Dose/route attenuation**: low-dose aspirin / topical route steps severity down
  only when the rule is flagged dose/route-sensitive.
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
