# Interaction Bundle Admin Installer — Design Spec (Sub-project #3c)

**Date:** 2026-06-25
**Status:** Approved design, pre-implementation
**Scope:** Sub-project #3c — the admin module that uploads, validates, and installs a Colab-produced interaction knowledge bundle (`.sqlite`) into the engine, then hot-reloads it. Closes the #3 loop on the read/install side (#3b authors the bundle off-machine).

## Context

#3a built the engine read path: a fail-safe SQLite bundle reader + loader merge + `reload_indexes()`. #3c is the **install side**: a pharmacist-admin uploads the bundle the Colab notebook (#3b) produced; the system validates it, atomically installs it to `bundle.BUNDLE_PATH`, and calls `reload_indexes()` so the engine picks it up without a restart.

### Decisions locked (in brainstorming)

- **Validate before install, atomic replace.** A bad upload (corrupt / wrong schema / missing table) returns 422 and never touches the live bundle. Install is `os.replace` (atomic, same filesystem).
- **Stream the upload** to a temp file in chunks — DDInter bundles can be large.
- **`clinical:write` permission** (matches existing ingest endpoints).
- **A new dedicated admin dashboard** for it.
- Install/validate logic lives in **`bundle.py`** (it owns `BUNDLE_PATH`).
- This is a path **separate** from the existing `/knowledge/ingest/sqlite/upload` (which feeds the RAG vector KB).

### Non-goals (#3c)

- No dataset downloading/normalization (that's #3b, off-machine on Colab).
- No edit of bundle contents (the bundle is opaque; it's produced by #3b).
- No change to the engine's matching/severity/merge logic (#3a already handles reads).

## Components

### 1. `bundle.py` additions (stdlib only)

```text
validate_bundle_file(path: Path) -> tuple[bool, str, dict]
    # (ok, reason, stats). ok=False with a human reason if: not a SQLite db,
    # missing any of {interaction_rules, drug_attributes, bundle_meta}, or
    # bundle_meta.schema_version != SCHEMA_VERSION. stats = bundle_meta dict on ok.

install_bundle(temp_path: Path) -> None
    # Atomically move temp_path → BUNDLE_PATH (os.replace; same-fs). Ensures the
    # data dir exists. Raises OSError only on a real filesystem failure.
```

- `validate_bundle_file` reuses `_open` (read-only connect + forced `SELECT 1`) and `_schema_ok`,
  and checks `sqlite_master` for the three required tables. Never raises — returns `(False, reason, {})`.

### 2. Endpoints — `cds.py`

- `POST /cds/interaction-bundle/install` (`require_permission("clinical:write")`):
  1. Stream the `UploadFile` to a `NamedTemporaryFile` in chunks (e.g. 1 MB) — bounded memory.
  2. `ok, reason, stats = validate_bundle_file(tmp)`. If not ok → delete tmp, `HTTPException(422, reason)`.
  3. `install_bundle(tmp)` → `new_stats = reload_indexes()`.
  4. Return `{"installed": true, "stats": new_stats}`.
  Any unexpected error → 500 with a safe message; the live bundle is only ever replaced by a *validated*
  temp file, so a failure mid-stream/validation leaves the engine on its current (good) bundle.
- `GET /cds/interaction-bundle/status` (`require_permission("clinical:read")`):
  `{"installed": bool, "stats": bundle_stats()}` — `installed` is `bool(bundle_stats())`.

### 3. Frontend — `InteractionBundleAdmin.tsx` (new admin dashboard)

Registered in `DashboardShell` (like #2c's `InteractionAuditView`), admin-facing:
- **Current bundle card:** from `GET /cds/interaction-bundle/status` — schema_version, the per-dataset
  versions (parsed from the `datasets` meta JSON), `rule_count`, `attribute_count`, `built_at`,
  `checksum` (short). "No bundle installed — the engine is running on curated rules only" when empty.
- **Install card:** a `.sqlite` file picker + **Install bundle** button → `POST .../install` as
  `multipart/form-data`. On success, refresh the status card + a success note ("Installed N rules,
  M attributes"). On 422, show the validation `reason` inline (e.g. "schema version mismatch").
- A short note that the bundle is produced by the Colab ingestion notebook (#3b).

### 4. API client — `api.ts`

```ts
clinicalApi.getBundleStatus()           // GET /cds/interaction-bundle/status
clinicalApi.installBundle(file: File)    // POST multipart → /cds/interaction-bundle/install
```

## Data flow

```
admin opens "Interaction Bundle" → GET /cds/interaction-bundle/status → current-bundle card
picks bundle.sqlite → Install → POST multipart
  stream upload → temp file (chunked)
  validate_bundle_file(temp)
    invalid → 422 {reason}; temp deleted; live bundle untouched
    valid   → install_bundle(temp) [os.replace → BUNDLE_PATH] → reload_indexes() → new stats
  → {installed:true, stats} → UI refreshes status + success note
```

## Error handling

- Not a SQLite file / corrupt → `validate_bundle_file` → 422 "not a valid SQLite database".
- Missing a required table → 422 "missing table: <name>".
- Schema-version mismatch → 422 "schema version mismatch (got X, expected Y)".
- Upload interrupted → temp incomplete → validation fails → 422; live bundle intact.
- `os.replace` failure (disk full / cross-fs) → 500 "could not install bundle"; live bundle intact
  (temp not yet moved, or move failed atomically).
- After install, `reload_indexes()` is itself fail-safe (#3a) — even a freshly-installed-but-edge-case
  bundle can only degrade to curated-only, never crash.

## Testing

Backend (`tests/unit/test_interaction_bundle_installer.py`):
- `validate_bundle_file`: accepts a good bundle (ok, stats populated); rejects a corrupt file, a
  missing-table db, and a wrong-`schema_version` bundle with the right reasons.
- `install_bundle`: replaces the file at `BUNDLE_PATH` atomically; creates the data dir if absent.
- Endpoint (fake `UploadFile` + monkeypatched `BUNDLE_PATH` to a tmp dir):
  - good upload → `{installed:true}`, stats reflect the new bundle, and `evaluate` then sees a bundle
    rule (end-to-end through `reload_indexes`).
  - bad upload → 422 with the reason; a previously-installed good bundle is **unchanged** (assert its
    rule still loads).
  - `GET status` returns installed=false/{} when none, populated stats when installed.

Frontend (component / preview):
- Status card renders counts/version; "no bundle" empty state.
- File pick + Install posts multipart; success refreshes; a 422 shows the reason inline.

## Safety invariants

- A bad bundle can never replace a good one (validate-before-install) and can never break the engine
  (#3a fail-safe + atomic replace).
- Install is admin-gated (`clinical:write`); status is `clinical:read`.
- The bundle file remains gitignored generated data; #3c only moves it into place.
