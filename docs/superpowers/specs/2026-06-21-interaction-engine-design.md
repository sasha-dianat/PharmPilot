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

### Non-goals (this sub-project)

- No UI (sub-project #2).
- No DDInter import (sub-project #3) — but the knowledge index is shaped so
  DDInter rules append without engine changes.
- No client-vs-patient resolution (sub-project #4).
- No live LLM. (Optional prose summary is a later add-on in #2.)

## Module placement

Extend the existing `services/ai/clinical_decision_support/` package (already holds
`schema.py`, `engine.py`, `rules.py`, `normalizer.py`, the `/cds/evaluate` router,
and `tests/unit/test_cds_engine.py`). The 6 hardcoded Python rules migrate into the
new data-driven knowledge file.

## Components

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

Run over the combined med list, dedup by (rule_id, sorted participant key),
highest severity wins:

- **Drug–drug:** every unique unordered pair `(i<j)`; look up specific-drug then
  class-level rules.
- **Drug–disease:** each med × each condition (active or past).
- **Duplicate-therapy:** pairs sharing a therapeutic class → Moderate by default.
- **Drug–allergy:** each med × allergies, plus a minimal cross-reactivity map
  (e.g. penicillin↔cephalosporin) → severity per map, default Major.

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
    severity: str            # canonical 4-tier
    participants: list[dict]  # [{name, kind: drug|condition, provenance, last_seen}]
    mechanism: str
    clinical_problem: str
    suggested_actions: list[str]
    evidence_sources: list[str]
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
Rx claim → /cds/interaction-report {patient_id, rx_ids}
  → build_review_set(db, patient, pharmacy)        # current_rx + active + historical + conditions + allergies
  → engine.evaluate(review_set, knowledge_index)   # in-process, deterministic
       drug_drug | drug_disease | duplicate | allergy  (+ recency cutoff, + modifiers)
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
  condition retained; long-acting drug bypasses cutoff.
- Dedup + highest-severity-wins.
- Patient-specific severity modifier (e.g. K⁺ ≥ 5.5 escalates to Contraindicated).
- Unknown drug surfaced in coverage notes (no silent miss).
- `degraded` path when the index is empty.
- Migration parity: the 6 legacy rules still fire with equivalent (re-mapped) severity.

## Determinism & safety invariants

- No network and no LLM in the evaluate path.
- Severity is data-driven only; never inferred.
- Every finding cites evidence, source, confidence, and the verification notice.
- Aligns with the project's deterministic-first / LLM-prose-only architecture and the
  PHI egress scrubber already in place.
