# Physician Responsibility Letter — Design Spec (Phase 2b)

**Date:** 2026-06-24
**Status:** Approved design, pre-implementation
**Scope:** Phase 2b of sub-project #2 — the physician notice generated when a prescription carries a **Contraindicated** interaction.

## Context

Phase 2a surfaced the interaction engine in the pharmacist's middle panel with an audited acknowledgment. Phase 2b adds the contraindication escalation: when a Contraindicated interaction is present, the pharmacist generates a **printable physician responsibility letter** for the client to carry to the prescriber, who may assume administering responsibility. **Dispensing is never blocked.**

### Decisions locked (in brainstorming)

- **Non-blocking.** Contraindicated → dispense proceeds AND a letter is produced. No hard stop.
- **PHI-safe LLM composition.** The LLM receives only de-identified clinical content + **placeholder tokens**; identifiers are substituted **locally after** the model returns. Identifiers never reach any model. The existing `phi_scrub` egress gate ([[phi-llm-egress-rule]]) remains as defense-in-depth.
- **Persian by default**, any other language by user choice (LLM translates/paraphrases).
- **Deterministic fallback** (Persian/English template) when no LLM is reachable or output validation fails. Arbitrary languages require the LLM.
- **Verbatim persistence** of the final letter + content hash + provenance, for legal disputes.
- **Physician identity** (name + medical council ID) prefilled from the Rx's `Prescriber`, editable.
- **Output = browser-print of RTL HTML** with a bundled Persian webfont (Vazirmatn). PDF (WeasyPrint) is a future drop-in using the same template; the legal record is the renderer-independent verbatim text + hash.
- **One-way notice with a physician signature line** (no closed-loop return tracking — that would be a later phase).

### Non-goals (Phase 2b)

- No server-side PDF (HTML-now / PDF-later).
- No closed-loop tracking of the physician's signed return.
- No change to the Phase 2a acknowledgment gate or dispensing flow.
- Admin trail of letters is Phase 2c.

## Components

### 1. Letter content builder — `physician_letter/content.py`

`build_clinical_content(findings, drug_names) -> ClinicalContent`: from the Contraindicated
finding(s), assemble **de-identified** clinical text — the warning, the exact mechanism
(`mechanism` + `mechanism_basis`), the predicted effect/direction, and the drug names. No
identifiers. This is the only clinical payload the LLM ever sees.

### 2. Placeholder contract — `physician_letter/placeholders.py`

The allowed token set (the ONLY tokens any composed template may contain):

```
{{PATIENT_NAME}} {{PATIENT_NATIONAL_ID}} {{PHYSICIAN_NAME}} {{COUNCIL_ID}}
{{PHARMACIST_NAME}} {{PHARMACIST_LICENSE}} {{PHARMACY_NAME}} {{DATE}}
```

`ALLOWED_TOKENS` (set) + `substitute(template, values) -> str` (replaces exactly these tokens
from a values dict; leaves nothing unfilled — raises if a value is missing).

### 3. LLM composition — `physician_letter/compose.py`

`compose_via_llm(content, language) -> str | None`: builds a prompt instructing a respectful,
formal physician letter **in `language`** that:
- states the pharmacist identified a contraindicated interaction during verification,
- describes the warning + exact mechanism (from `content`),
- requests the physician confirm/assume responsibility, ending with the signature line,
- uses **only** the placeholder tokens for any person/pharmacy/date — never invents names/numbers.

Calls `local_llm.generate(prompt, system=…, phi=False, task="summarize", max_tokens=900,
temperature=0.2)`. Returns the text, or `None` if `degraded`/empty. **phi=False is correct** —
the payload is de-identified; the egress scrubber is belt-and-suspenders.

### 4. Output validation — `physician_letter/validate.py`

`is_safe_template(text) -> bool`: rejects an LLM result unless
- every `{{…}}` token is in `ALLOWED_TOKENS`, and
- after stripping the placeholders, `phi_scrub.scrub_identifiers(text)` finds **nothing**
  (no stray national-ID-like digit runs, emails, phones — i.e. the model didn't invent an
  identifier or echo one).

Failing validation → discard the LLM output and use the deterministic template.

### 5. Deterministic templates — `physician_letter/templates.py`

Built-in **Persian (RTL)** and **English** placeholder templates covering the same content,
used when no LLM is reachable or validation fails. They use the same `ALLOWED_TOKENS`, so the
substitution step is identical for both paths. (Other languages are only available via the LLM.)

### 6. Render — `physician_letter/render.py` + frontend print view

`render_html(letter_text, language) -> str` wraps the substituted text in a print-tuned,
`dir="rtl"` (for Persian) HTML document with `@page { size: A4; margin: 2cm }`, a bundled
**Vazirmatn** webfont, pharmacy letterhead, and the physician signature/acknowledgment line.
The frontend renders this in the modal and prints it via `window.print()` of a print-only view.
(PDF-later: the same template string can feed WeasyPrint without touching callers.)

### 7. Persistence — `physician_letters` table + migration `0015`

| column | type | note |
|---|---|---|
| id | UUID pk | |
| patient_id | UUID, indexed | |
| rx_id | UUID, nullable | |
| pharmacy_id | UUID, indexed | |
| prescriber_name | str | snapshot |
| prescriber_council_id | str, nullable | snapshot |
| pharmacist_id | UUID | the authenticated pharmacist |
| pharmacist_name | str | snapshot |
| pharmacist_license | str, nullable | snapshot |
| language | str(8) | e.g. `fa`, `en` |
| source | str(40) | provider+model, or `deterministic` |
| model_version | str(40) | letter template/engine version |
| letter_text | Text | **verbatim final** (identifiers substituted) |
| content_hash | str(64) | sha256 of `letter_text` (tamper-evidence) |
| (+ AuditedBase: created_at, created_by, …) | | |

A `ClinicalAuditLog` row (`module="physician_letter"`) also records the issuance referencing
the letter id.

### 8. Endpoints — `cds.py`

- `POST /cds/physician-letter` — body `{patient_id, rx_id?, language, physician_name,
  council_id, findings: [{rule_id, mechanism, mechanism_basis, participants, severity}]}`.
  Builds content → compose (LLM→validate, else template) → substitute identifiers (patient from
  DB, pharmacist from `staff`, pharmacy from the pharmacy record, physician from the body) →
  persist + audit → return `{id, letter_text, language, source}`.
- `GET /cds/physician-letter/{id}` — returns the stored letter (tenant-scoped) for reprint.

### 9. Frontend — `PhysicianLetterModal.tsx` + trigger

- In `InteractionReportPanel`, when any finding is **Contraindicated**, show a "Generate
  physician letter" button.
- The modal: **language selector** (Persian default), **physician name + council ID** (prefilled
  from the Rx prescriber via the report/endpoint, editable), a **live preview** of the rendered
  letter, **Print** (`window.print()` of the print-only letter view) and a Close. Generation
  persists automatically (legal record), so reprint is available via `GET`.

## Data flow

```
Contraindicated finding present → "Generate physician letter"
POST /cds/physician-letter {patient_id, rx_id, language, physician_name, council_id, findings}
  build_clinical_content(findings)                 # de-identified only
  → compose_via_llm(content, language)             # placeholders only; phi=False
       is_safe_template(out) ?  use it  :  deterministic template(language)
  → substitute(template, {patient, physician, pharmacist, pharmacy, date})   # local
  → persist physician_letters (verbatim text + content_hash + provenance) + audit
  → return letter_text → modal preview → window.print() (RTL A4, bundled font)
```

## Error handling

- **No LLM reachable / degraded** → deterministic Persian/English template (recorded
  `source="deterministic"`). Letter still produced.
- **LLM output fails validation** (unknown token or stray identifier) → discard, deterministic
  template. Logged.
- **Requested non-Persian/English language with no LLM** → fall back to Persian template + a UI
  notice that translation was unavailable.
- **Missing physician council ID** → the modal requires the pharmacist to fill it before
  generating (it's prefilled when the prescriber record has it).
- **Substitution missing a value** → hard error before persistence (never emit a letter with an
  unfilled `{{token}}`).

## Testing

- **Content builder** produces text with no identifiers (assert `scrub_identifiers` finds none).
- **LLM prompt** built by `compose_via_llm` contains no identifiers — only de-identified
  clinical text + placeholder instructions (monkeypatch `local_llm.generate`, capture prompt).
- **Validation**: a template with an unknown token or a stray national-ID digit-run is rejected;
  a clean placeholder template passes.
- **Fallback**: when `generate` returns degraded, the deterministic template is used and
  `source="deterministic"`.
- **Substitution**: all identifiers injected; `{{` never remains; a missing value raises.
- **Persistence**: row written with a correct `content_hash`; provenance + pharmacist identity
  recorded; an audit row is added.
- **Endpoint**: returns `{id, letter_text, language, source}`; `GET` reprints the same text.
- **PHI invariant test**: across the whole compose path, the string sent to `local_llm.generate`
  never contains the patient name or national ID.

## Safety invariants

- Identifiers never reach the model — placeholders + local substitution, enforced by test.
- The persisted letter is the verbatim legal record; `content_hash` makes tampering detectable.
- Deterministic, offline fallback guarantees a letter is always producible.
- Reuses `phi_scrub` as the egress backstop; consistent with the project's PHI rule.
