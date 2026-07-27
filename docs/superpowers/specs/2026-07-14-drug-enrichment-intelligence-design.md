# Drug Enrichment Intelligence (هوش‌یار دارو) — Design

**Date:** 2026-07-14 · **Status:** approved by owner · **Vehicle:** Mistral in-app + Claude seed

## Problem

Most formulary↔NFI incompatibilities stem from three gaps: (1) dosage form,
(2) strength, (3) manufacturer/country. Example: the formulary row
«ویتامین آ-تداژل» is actually *Vitamin A-Tedagel softgel, 25,000/50,000 IU,
Tehran Daru* — but nothing in the row says so, so the linker can't match it and
the workbench can't explain it. Today: 501 tamin + 789 salamat unmatched rows,
~1,400 low-confidence links.

## Objective ("resolve it forever")

A per-drug research module that (a) deep-researches each ambiguous drug on the
web, (b) cross-compares sources, (c) extracts the missing structured details,
and (d) persists them as a **canonical, versioned, self-applying reference**
that all future NFI harvests and coverage links consult automatically.

## Owner decisions

- Research runs **in-app via Mistral** (Agents API `web_search` connector +
  forced JSON schema), keyed by `MISTRAL_API_KEY` env — never in chat/memory.
- **Claude seeds** ~10 representative drugs in-session first to validate the
  schema/prompt against real Persian sources.
- Deterministic-first invariant preserved: research output is **suggestion-only**
  (`suggested` status); only owner-**approved** rows have any effect.
- Scope guard: the worklist is the ambiguous set (~1,300 unique names), NOT the
  39k `country=null` products — the NFI monograph re-crawl fills those
  deterministically and stays authoritative.

## Durability guarantees (the "forever" part)

1. **Committed artifact:** approved rows export to
   `data/reference/drug_enrichments.json` (versioned in git, human-readable,
   re-importable). DB is runtime truth; the file is the canonical reference —
   the same pattern as the catalog seed. Export/import round-trips losslessly.
2. **Spelling-proof keys:** `enrich_key(name)` = yeh/kaf fold → ZWNJ/space fold →
   `normalize()` (salt-strip) → `canonical_ingredient()` → lower/trim. Original
   raw name kept alongside. Strengths stored mg-normalized (reuse `_strength_mg`).
3. **Idempotent auto-application:**
   - `upsert_catalog` gap-fills missing fields (country, manufacturer,
     brand_name, dosage_form, strength) from approved enrichments — **never
     overwrites a value NFI provided** (NFI stays authoritative).
   - `link_rows` augments an ambiguous formulary row (form/strengths/generic)
     from approved enrichments before matching — converting unmatched→matched.

## Components

### 1. Store — `drug_enrichments` table (migration 0020)
`id, key (unique, indexed), raw_name, irc (nullable), generic_name, brand_name,
manufacturer, country, dosage_form, strengths JSONB (mg floats + display),
notes, sources JSONB (urls), researched_by ('mistral'|'claude'|'manual'),
confidence float, status ('suggested'|'approved'|'rejected'), decided_by,
decided_at, created_at, updated_at`. Model `shared/models/enrichment.py`,
registered in the shared registry (parity-checked).

### 2. Worklist builder — deterministic
`GET /pricing/enrichment/worklist?insurer=` → dedup by `enrich_key`:
- unmatched rows from the latest non-failed run of EVERY insurer,
- review links with confidence < 0.7,
- catalog products referenced by a formulary but missing dosage_form/strength.
Excludes keys already `approved`/`suggested` (idempotent re-runs).

### 3. Research engine — `services/ai/enrichment/`
- `researcher.py`: provider-agnostic `research(drug_name) -> Suggestion` seam.
- `mistral_researcher.py`: Mistral Agents API + `web_search`, Persian-aware
  prompt (ویتامین آ-تداژل worked example), forced JSON schema
  `{generic, brand, manufacturer, country, dosage_form, strengths[], confidence, sources[]}`,
  temperature 0. Failures recorded per-item, never abort the batch.
- `service.py`: background batch job (same pattern as harvests: module state,
  start/status/stop, rate-limit delay, resumable — skips keys already present).
  No harvest_lock needed (no Iran-proxy dependency).

### 4. Application seams (deterministic, tested)
- `coverage_import.link_rows(..., enrichments=None)`: optional dict
  `key → enrichment`; when a row's drug_name resolves to an approved enrichment,
  merge its form/strengths/generic into `_row_signals` inputs.
- `importer.upsert_catalog(..., enrichments=None)`: gap-fill only-missing fields.
- Loader `enrichment_reference.load_approved(db) -> dict` + file import/export.

### 5. API
`/pricing/enrichment/`: `worklist` (GET), `run` (POST — start batch; 409 if
running), `status` (GET), `suggestions` (GET, filter by status), `decide`
(POST — bulk approve/reject ids), `export` (POST — write the committed JSON
artifact + return path), `import` (POST — load artifact into DB, used for
seeding fresh environments). All `inventory:read`/`write` per verb.

### 6. GUI — workbench tab «غنی‌سازی هوشمند»
Worklist count + شروع پژوهش button + live batch progress; suggestions review
table (drug, found details, confidence, source links, researched_by) with
bulk approve/reject; export button showing the artifact path. Comfortable
(text-sm, tables, tabular-nums) like the workbench.

### 7. Claude seed pass
Fable researches ~10 worklist drugs with in-session web search, inserts them as
`suggested` rows (`researched_by: 'claude'`) via the API, validating schema and
prompt before Mistral runs at scale.

## Security / cost / trust
- `MISTRAL_API_KEY` via backend env only. No PHI anywhere near this module
  (reference drug names only). Suggestion→approval boundary enforced in API
  (only `decide` mutates status; only `approved` rows load).
- Batch cost ≈ a few USD per full worklist on mistral-medium; rate-limited.

## Testing
- Pure: `enrich_key` folding cases; suggestion-schema validation; worklist dedup;
  gap-fill never-overwrite rule; linker augmentation converts a synthetic
  ویتامین آ-تداژل-style row from unmatched→matched; export/import round-trip.
- Mocked researcher for service tests (no network); one live Mistral probe call
  gated behind the key's presence (skip if unset).
- Migration 0020 parity (zero drift). GUI browser-verified.

## Out of scope
- Auto-approval of high-confidence suggestions (revisit after first real batch).
- Enriching all 39k catalog rows via web (NFI re-crawl owns that).
- Any clinical-decision use of enrichment data (reference/linking only).
