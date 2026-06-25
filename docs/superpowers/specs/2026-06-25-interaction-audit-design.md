# Interaction Audit / Legal Trail — Design Spec (Phase 2c)

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** Phase 2c of sub-project #2 — a read-only admin view of the interaction acknowledgment + physician-letter legal trail. Closes out sub-project #2.

## Context

Phase 2a records a full-context `ClinicalAuditLog` row each time a pharmacist acknowledges serious interactions. Phase 2b persists every physician responsibility letter verbatim in `physician_letters` (and an audit row). Phase 2c surfaces both for medico-legal review: **which pharmacist** signed off / issued a letter, **for which patient**, **naming which physician**, **over which interactions**, **when** — with the verbatim letter reprintable.

### Decisions locked (in brainstorming)

- **Unified chronological feed** merging acknowledgments + letters (not separate tabs), each record tagged with its `type`.
- **`reports:read` permission** (matches the existing `/ai-hub/audit-log` endpoint) — admins/managers. Tenant-scoped to `staff.pharmacy_id`.
- **Letter drill-down reuses** the existing `GET /cds/physician-letter/{id}` (verbatim text + reprint).
- **Filters:** date range (`from`/`to`), `type` (ack | letter), **patient name**, **physician medical-council ID**, optional `patient_id`, plus `limit`/`offset` pagination.

### Non-goals (Phase 2c)

- No new audit *writing* — this is read-only over data Phases 2a/2b already write.
- No edit/delete of audit records (legal records are append-only).
- No CSV/PDF export (could be a later add).
- No closed-loop physician sign-back (separate phase).

## Components

### 1. Audit endpoint — `GET /cds/interaction-audit` (in `cds.py`)

Gated `require_permission("reports:read")`, tenant-scoped. Query params:
`patient_id?`, `patient_name?`, `council_id?`, `from?`, `to?` (ISO dates), `type?` (`ack`|`letter`),
`limit=50`, `offset=0`.

Merges two sources, each normalized to a common record shape, then sorted by `created_at` desc and
sliced for pagination (bounded fetch: each source capped at `offset+limit`):

- **Acknowledgments** — `ClinicalAuditLog` where `module = 'interaction_acknowledgment'`.
  `ClinicalAuditLog` has no `pharmacy_id` column, so tenant-scope by **joining `patients` on
  `patient_id`** and filtering `patients.pharmacy_id = staff.pharmacy_id`. Filterable fields live in
  `input_snapshot` JSONB:
  - patient name → `input_snapshot->'patient'->>'name' ILIKE :q`
  - council id → `input_snapshot->'physician'->>'medical_council_id' ILIKE :q`
  - patient_id → `patient_id` column.
- **Letters** — `physician_letters` table (tenant-scoped by `pharmacy_id`), **left-joined to
  `patients`** for the patient name. Filterable:
  - patient name → `patients.first_name || ' ' || patients.last_name ILIKE :q`
  - council id → `physician_letters.prescriber_council_id ILIKE :q`
  - patient_id → `physician_letters.patient_id`.

Date range applies to `created_at` on both. `type` selects one source or both.

### 2. Record shape (normalized)

```json
{
  "id": "...",                      // audit_id (ack) or letter_id (letter)
  "type": "ack" | "letter",
  "at": "2026-06-25T...",           // created_at
  "pharmacist": {"id","name","license"},
  "patient": {"id","name"},
  "physician": {"name","council_id"},
  "severities": ["Contraindicated","Major"],   // ack: from findings; letter: derived/empty
  "letter_id": "..." | null,        // present for letter rows → drill-down
  "language": "fa" | null,          // letter only
  "source": "deterministic" | "groq:..." | null,
  "content_hash": "..." | null      // letter only
}
```

Acks read pharmacist/patient/physician/findings from `input_snapshot`; letters read columns
(+ patient name from the join).

### 3. Frontend — `InteractionAuditView.tsx` (admin dashboard)

A new dashboard reachable from Dashboards, gated to admin/manager roles (follow the
`AIProviderSettings.tsx` pattern + `super_admin`/`pharmacy_manager` role check already in `App.tsx`):

- **Filter bar:** date-from, date-to, type select (All/Ack/Letter), patient-name text, council-ID text,
  Apply/Clear.
- **Table** (paginated, newest first): time · type chip · pharmacist · patient · physician (+ council)
  · severity chips · action. Letter rows show a **"View letter"** button.
- **Letter drill-down:** "View letter" → `GET /cds/physician-letter/{letter_id}` → a panel/modal
  showing the verbatim letter (RTL for `fa`) with **Print** (reuse the print-window approach from
  `PhysicianLetterModal`) and the `content_hash` shown for integrity reference.
- Pagination: Prev/Next over `limit`/`offset`.

### 4. API client — `api.ts`

`reportsApi.interactionAudit(params)` → `GET /cds/interaction-audit` (query params);
reuse `clinicalApi` for the existing `getPhysicianLetter` (add a `getPhysicianLetter(id)` GET wrapper
if not already present).

## Data flow

```
admin opens "Interaction Audit" → GET /cds/interaction-audit?filters
  acks  ← ClinicalAuditLog (module=interaction_acknowledgment, JSONB filters)
  letters ← physician_letters ⋈ patients (column/join filters)
  → normalize both → merge → sort desc(at) → slice(offset,limit) → table
click "View letter" → GET /cds/physician-letter/{letter_id} → verbatim RTL letter → Print
```

## Error handling

- Empty result → "No records match these filters."
- Bad date params → 422 (FastAPI validation) surfaced as an inline filter error.
- A malformed/legacy snapshot missing a field → that field renders "—"; never 500 the feed.
- Letter drill-down 404 (deleted/missing) → inline "letter unavailable".

## Testing

Backend (`tests/unit/`):
- Returns merged, time-sorted records from both sources; `type` field correct per source.
- Tenant scope: only the staff's pharmacy rows returned.
- Filters: patient-name (ILIKE on snapshot for acks, on join for letters), council-id, date range,
  type, patient_id each narrow correctly.
- Pagination: `limit`/`offset` slice the merged set.
- `reports:read` enforced (permission dependency present).
- Record shape: ack reads identity from snapshot; letter exposes `letter_id` + `content_hash`.

Frontend (component / preview):
- Filter bar drives the query; table renders rows; type chips correct.
- "View letter" opens the verbatim letter and Print works.

## Security & integrity invariants

- Read-only; no mutation of append-only legal records.
- Tenant-scoped + `reports:read` gated (identifiers shown only to authorized admins — appropriate
  for a legal-trail tool; this is internal storage, never an LLM).
- The displayed letter is the persisted verbatim text; `content_hash` shown so integrity is verifiable.
