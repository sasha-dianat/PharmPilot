# Interaction Report Panel — Design Spec (Phase 2a)

**Date:** 2026-06-22
**Status:** Approved design, pre-implementation
**Scope:** Phase 2a of sub-project #2 (surface the interaction engine in the pharmacist's middle panel).

## Context

Sub-project #1 built a deterministic interaction engine (`services/ai/clinical_decision_support/interaction/`) returning an `InteractionReport`. Sub-project #2 surfaces it to the pharmacist. Decomposed into three phases:

- **Phase 2a** (this spec): precompute-at-intake + cache + report panel + full-context audited acknowledgment.
- **Phase 2b**: physician responsibility letter (PHI-safe LLM-or-template, RTL print, verbatim persisted).
- **Phase 2c**: admin legal-trail view.

### Phase-2a decisions (locked in brainstorming)

- The report is **precomputed at prescription intake**, not on pharmacist request, so review is never slowed by a live query. Result is **cached per-patient keyed by a review-set hash** (drugs + conditions + relevant labs).
- The panel sits **alongside** the existing DUR panel (read-only advisory); the DUR override/hard-stop workflow is untouched.
- Serious findings (**Contraindicated** or **Major**) require a **one-click pharmacist acknowledgment** before adjudication — audited with the authenticated pharmacist's identity. It never hard-blocks dispensing.
- The acknowledgment is **bound to a findings-hash**: if the basket changes and new serious findings appear, the ack auto-invalidates and is re-required.
- **Failure never implies safety**: "computed clean" and "could not compute (degraded)" are distinct, prominent states.

### Non-goals (Phase 2a)

- Physician letter (2b), admin trail (2c).
- The OCR / handwriting-ML / insurance-lookup pipeline that populates prescriber/patient data — Phase 2a only *consumes* those records.
- No change to the DUR alert workflow or adjudication hard-stops.

## Components

### 1. Review-set hash — `interaction/review_set.py`

`review_set_hash(rs: ReviewSet) -> str`: a stable SHA-256 over the engine's actual inputs —
sorted normalized med names (+ provenance), sorted condition concepts, and the lab values
the engine reads (potassium, egfr) rounded — so any change that could change findings
changes the hash. Drives cache freshness.

### 2. Findings hash — `interaction/report.py`

`findings_hash(report) -> str`: stable hash over the **serious** findings only
(`Contraindicated`/`Major`), each reduced to `(rule_id, sorted participant names, severity)`.
Included in the report payload. The acknowledgment binds to this value.

### 3. Cache table — migration in `data/migrations/versions/`

New table `interaction_reports` (one current row per patient+pharmacy, upserted):

| column | type | note |
|---|---|---|
| id | UUID pk | |
| patient_id | UUID fk, indexed | |
| pharmacy_id | UUID fk, indexed | tenant scope |
| review_set_hash | str(64) | freshness key |
| findings_hash | str(64) | ack-binding key |
| report | JSONB | serialized `InteractionReport` |
| model_version | str(40) | engine version |
| computed_at | timestamptz | |

Unique constraint on `(patient_id, pharmacy_id)` → upsert on recompute.

### 4. Precompute service — `interaction/precompute.py`

`async recompute_and_cache(db, patient, pharmacy_id) -> InteractionReport`:
build_review_set → compute `review_set_hash` → `engine.evaluate` → upsert the cache row.
Wrapped so callers can fire it without it ever raising into their critical path.

**Hook points** (each isolated in try/except; failure logged, never blocks the host action):
- `POST /prescriptions` intake (`prescriptions.py:intake_prescription`) — after the Rx row is created.
- `POST /prescriptions/{rx_id}/reanalyze` — forced refresh.
- Rx transitions that add/remove a current-basket Rx.

### 5. Report read endpoint — `GET /cds/interaction-report/{patient_id}`

Cache-first: load the cached row; recompute current `review_set_hash`; if it matches, return
the cached report instantly; if it mismatches or no row exists, `recompute_and_cache` then
return. Always returns `{report, cached: bool, computed_at}`. Tenant-scoped, `clinical:read`.
(The Phase-1 `POST /cds/interaction-report` stays for ad-hoc/forced compute.)

### 6. Acknowledgment endpoint — `POST /cds/interaction-ack`

Body: `{patient_id, rx_id, findings_hash, acknowledged: [{rule_id, severity}]}`.
Builds a **self-contained legal snapshot** and writes one `ClinicalAuditLog` row
(`module="interaction_acknowledgment"`):

- `user_id` = authenticated `staff.id` (**which pharmacist** — the legal requirement)
- `patient_id`
- `input_snapshot`: pharmacist (id, name, `pharmacist_license_number`), physician (from the Rx's
  `Prescriber`: name, `medical_council_id`, specialty), patient (id, name, national_id),
  prescription (rx_id, drug list), and the acknowledged findings (rule_id, severity, mechanism,
  direction).
- `output_snapshot`: `{acknowledged_by, acknowledged_at, findings_hash}`
- `rules_triggered`: list of acknowledged rule_ids
- `model_version`: engine version

Returns `{audit_id, findings_hash}`. Idempotent per `(rx_id, findings_hash, staff)`.

### 7. Frontend — `InteractionReportPanel.tsx` (Verification Center middle column)

- react-query: `GET /cds/interaction-report/{patient_id}` on `selectedRx` change (mirrors the
  existing `durAlerts` query). Refetches on Rx transitions.
- **Concise report**: severity-count header chips (Contraindicated/Major/Moderate/Minor) using
  the existing `severity.ts` tokens; findings grouped by severity. Each finding renders
  participants (`a × b` or `drug × condition`), **direction** badge (toxicity / efficacy-loss /
  opposition), mechanism, **predicted magnitude**, recommended action, provenance + recency
  note, and an **evidence badge** (Established vs Predicted). Reuses the legible/bold patterns.
- **States**: loading; "No interactions detected" (only when `degraded=false`); **degraded**
  banner ("Could not compute — manual review required"); empty basket.
- **Acknowledgment gate**: if any finding is Contraindicated/Major, show "Acknowledge N serious
  interaction(s)". Click → `POST /cds/interaction-ack` with the report's `findings_hash` → on
  success mark acknowledged for that `findings_hash`.
- Lift `acknowledgedFindingsHash` to `VerificationCenter`; extend `canAdjudicate` to also require
  `acknowledgedFindingsHash === report.findings_hash` **when** serious findings exist. Because the
  ack binds to the hash, a changed basket (new hash) re-requires acknowledgment automatically.
- Carries the engine's `pharmacist_verification_notice`.

## Data flow

```
POST /prescriptions (intake)  ──┐
POST .../reanalyze            ──┼─→ recompute_and_cache(patient)
basket-changing transition    ──┘     build_review_set → review_set_hash → engine.evaluate
                                       → upsert interaction_reports (report, hashes, computed_at)

panel open → GET /cds/interaction-report/{patient_id}
   cached row hash == current basket hash ?  → return cached (instant)
   else                                       → recompute_and_cache → return

serious findings present → pharmacist clicks Acknowledge
   POST /cds/interaction-ack {patient_id, rx_id, findings_hash, acknowledged}
   → ClinicalAuditLog snapshot (pharmacist + physician + patient + rx + findings)
   → canAdjudicate unlocked while findings_hash matches
```

## Error handling

- Precompute failure at any hook → logged, host action (intake/transition) proceeds normally;
  the report row is absent/stale → the read endpoint recomputes on open.
- Engine `degraded=true` → panel shows the degraded banner, never "no interactions"; acknowledgment
  gate is disabled (nothing to safely acknowledge) and adjudication shows "interaction check
  unavailable — manual review".
- Ack endpoint: missing prescriber/patient fields degrade gracefully (snapshot records what's
  present + a `missing` list); never fails the ack.
- Stale/duplicate ack (same rx_id+findings_hash+staff) → idempotent no-op returning the prior audit.

## Testing

Backend (`tests/unit/`):
- `review_set_hash`: stable for same inputs; changes when a drug/condition/relevant-lab changes;
  unchanged by irrelevant fields.
- `findings_hash`: covers only serious findings; changes when a serious finding is added/removed.
- `recompute_and_cache`: upserts one row per patient; updates hashes + computed_at.
- read endpoint: cache-hit returns cached (no recompute); hash-mismatch recomputes; missing row
  recomputes.
- ack endpoint: writes one `ClinicalAuditLog` with the correct `user_id`=pharmacist, and a snapshot
  containing physician (council id), patient (national id), rx drugs, and findings; returns audit_id;
  idempotent on repeat.
- degraded path: ack disabled, distinct state.

Frontend (component-level / preview verification):
- Renders severity-grouped findings from a cached report; correct severity tokens.
- "No interactions" vs degraded banner are distinct.
- Acknowledge posts the right `findings_hash`; gate unlocks; a changed `findings_hash` re-locks.

## Determinism & safety invariants

- Precompute uses the same deterministic engine (no network, no LLM).
- Hashes are stable and content-derived — no timestamps/random in the key.
- Legal snapshots are self-contained (no reliance on later-mutable joins) and written to the
  append-only `ClinicalAuditLog`.
- The panel must never present a clean state on engine failure.
