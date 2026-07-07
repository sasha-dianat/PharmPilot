# دارونامه Crawler + Full NFI Extraction — Design

**Date:** 2026-07-07 · **Status:** approved by owner · **Approach:** strategy-pipeline acquisition engine (Approach A)

## Problem

Per-insurer coverage (دارونامه) is essentially empty (5 rows) while the catalog now
holds 39k+ products. Insurer formularies change **weekly** and are published in
arbitrary shapes (Excel/CSV downloads, HTML tables, paginated portals, JSON
backends) at URLs that shift over time. Today coverage only enters via a one-shot
file upload that auto-applies immediately and persists nothing for review.

Separately, the NFI harvester leaves data on the detail page unextracted — most
importantly **کشور (country of origin)**, which lives in the brand-registrations
table, plus صاحب برند, license validity, raw composition, and the clinical
monograph — and none of the clinical/extra fields have a home in `drug_catalog`.

## Decisions (owner-confirmed)

1. **Hybrid source model** — config-driven engine; sources pre-seeded with
   best-known defaults per insurer, correctable in the GUI.
2. **Global harvest lock** — one crawl at a time across NFI + all دارونامه jobs
   (the Iran proxy is a system-wide proxy; jobs must not run in parallel).
3. **Preview → approve** — a harvest produces a persisted staged run; nothing
   touches the catalog until the admin clicks اعمال. Weekly re-runs show a diff.
4. **Manual + due badge** — each source has `check_interval_days` (default 7);
   the GUI badges overdue sources ("به‌روزرسانی لازم"); no automatic scheduler.
5. The **coverage upload card moves** from Drug Catalog to the new Coverage
   section. The existing `/pricing/coverage/import` endpoint keeps its current
   instant-apply behavior.
6. **Removals never auto-apply** — products present in current coverage but
   missing from a new list are flagged in the diff; removed only when the admin
   ticks "remove missing" at approve time (default off).
7. Daily **prices** remain the job of the existing price-sync → proposals
   module. This crawler is coverage-only.

---

## Workstream 1 — NFI harvester: extract ALL page data

### 1.1 Single parser

`services/core/drug_catalog/nfi.py:parse_detail` becomes the only
implementation. `scripts/harvest_nfi.py` deletes its private copy and imports
the service one (it already `sys.path.insert(0, ".")`s for ingest). The service
module stays stdlib-only so the script keeps working without the venv.

### 1.2 New extracted fields

From the fixture `nfi_detail_17248.html` (Victoza page), the page offers labels
the parser must now capture in addition to the current set:

| Page label | Output key | Notes |
|---|---|---|
| تولید کننده | `manufacturer` | already extracted; keep |
| صاحب برند | `brand_owner` | currently only a fallback; becomes its own key |
| صاحب پروانه | `license_owner` | already in JSONL; now persisted to DB |
| تاریخ اعتبار پروانه | `license_valid_until` | keep as the page's Jalali string (varchar) |
| ترکیبات | `composition` | raw text, in addition to parsed generic/strength |
| فارماکوکینتیک | `pharmacokinetics` | add to `_PAIR_LABELS` clinical set |
| brands table (نام دارو / صاحب نام تجاری / کشور / صاحب امتیاز / وضعیت) | `brands: [{name, trade_owner, country, licensee, status}]` | new table extractor |
| — | `country` | top level; from the brand row whose status is active/فعال, else the first row |

Existing clinical keys (`indications`, `mechanism`, `warnings`, `side_effects`,
`interactions_text`, `advice`) continue to be extracted.

### 1.3 Storage

New `drug_catalog` columns (Alembic migration, next linear revision — repo rule:
single head):

- `country` varchar(80), indexed
- `license_owner` varchar(200)
- `brand_owner` varchar(200)
- `license_valid_until` varchar(20) — Jalali date string as printed
- `monograph` JSONB — `{indications, mechanism, pharmacokinetics, warnings,
  side_effects, interactions_text, advice, composition, brands}` — everything
  else the page offers, so nothing is discarded.

`importer.build_records` maps the new keys; `upsert_catalog` merges them
(non-null wins; `monograph` replaced wholesale per record). The CLI `ingest`
and the GUI harvest path both flow through the importer, so both get the new
fields for free.

### 1.4 Backfill

The existing `nfi_pages.jsonl` predates these fields (`country` was never
extracted), so a **re-crawl** backfills them. Next proxy session: re-run crawl
over ids 1–60000 (resuming behavior: the crawl output is a fresh JSONL; ingest
is an idempotent upsert on `irc`). Finishing ids 53,642–60,000 happens in the
same run.

---

## Workstream 2 — دارونامه coverage crawler

### 2.1 Data model (same migration)

**`coverage_sources`**
- `id` UUID PK, `insurer` varchar(40) (`tamin | salamat | armed_forces | …` free-form allowed),
  `name` varchar(120) (display, e.g. "دارونامه تأمین اجتماعی"), `url` varchar(500),
  `strategy` varchar(20): `auto | file_url | html_table | paginated_html | json_api`,
- `settings` JSONB: `{page_param: "page", page_start: 1, max_pages: 500,
  table_index: 0, record_path: "data.items", delay_sec: 0.5,
  proxy: null, encoding: null, column_overrides: {"<column header>": "<role>"}}`
  (all keys optional; strategy-relevant subset used),
- `check_interval_days` int default 7, `enabled` bool default true,
- `last_run_at` timestamptz nullable, `last_run_status` varchar(20) nullable,
- timestamps.
- **Seed** (in the migration): three enabled rows — tamin / salamat /
  armed_forces — `strategy='auto'`, with best-known portal URLs as defaults
  (unverifiable from the dev machine without the Iran proxy — they are
  starting points the admin corrects in the GUI after a probe, not trusted
  values; a failed probe against a stale seed URL is an expected first-run
  outcome, not a bug).

**`coverage_runs`**
- `id` UUID PK, `source_id` FK, `started_at` / `finished_at`,
- `status` varchar(20): `running | parsed | approved | rejected | failed`,
- `stats` JSONB: `{rows, applied, review, unmatched, products_updated, columns: {header: role}}`,
- `diff` JSONB: `{added: n, changed: n, removed: n, samples: {added: [...], changed: [{irc, name, field, old, new}...], removed: [...]}}`
  (samples capped at 50 per bucket),
- `staged` JSONB: the `build_coverage(...).applied` dict (irc → {insurer: entry}),
- `review` JSONB: persisted review-queue items (row, candidate irc/name,
  confidence, entry) with an `accepted` bool the GUI toggles,
- `unmatched` JSONB: capped sample (200) + full count in stats,
- `error` text nullable, `applied_by` staff FK nullable, `applied_at` nullable.

Runs are kept (audit trail of what changed coverage and who approved it).

### 2.2 Acquisition engine — `services/core/drug_catalog/coverage_harvest.py`

Modeled on `nfi_harvest_service.py` (module-level state + background thread +
status snapshot for polling).

**Strategies** (pure functions `fetch(source) -> list[dict]` rows):
- `file_url` — GET the URL, write to temp with sniffed suffix, parse via
  existing `excel_import.read_table`.
- `html_table` — GET, parse via `read_table` HTML mode, honor `table_index`.
- `paginated_html` — format `url` with `{page}` (or append `page_param`),
  fetch pages from `page_start` until an empty/duplicate page or `max_pages`,
  concatenate table rows; `delay_sec` between requests.
- `json_api` — GET per page, descend `record_path` to the record list, flatten
  each record to a dict; same pagination loop.

**`probe(source) -> ProbeResult`** (synchronous, one fetch):
sniff the body — xlsx/zip magic → `file_url`; JSON-parses → `json_api`;
`<table` present → `html_table` (+ `{page}` in URL ⇒ `paginated_html`);
CSV-like → `file_url`. Returns `{detected_strategy, proposed_settings,
sample_rows (≤20), inferred_columns}` where `inferred_columns` runs
`infer_columns` merged over any saved `column_overrides`. Probe does not
persist anything.

**`start_harvest(db_url, source_id)`**:
1. Acquire the **global harvest lock** or raise `LockHeld(owner)`.
2. Background thread: run the strategy fetch (status snapshots: fetching,
   page n, rows so far) → `infer_columns` + apply `column_overrides` →
   `normalize_rows` → `link_rows` against the full catalog →
   `build_coverage(insurer=source.insurer, min_confidence=0.75)`.
3. Compute **diff** vs current DB coverage for that insurer:
   - *added*: irc staged but insurer key absent in current coverage
   - *changed*: insurer entry differs on `covered | share_pct |
     reference_price | ceiling` (record field-level old/new)
   - *removed*: irc has the insurer key now but is absent from staged
4. Persist the run (`parsed`), update `source.last_run_*`, release the lock.
   Any exception → run `failed` with `error`, lock released.

**`apply_run(db, run_id, *, remove_missing=False, accepted_review_ids=[])`**:
transaction — `apply_coverage(staged)` + accepted review entries (spread across
their ingredient group, same as high-confidence rows); if `remove_missing`,
delete the insurer key from `removed` ircs; mark run `approved` (applied_by/at).
`reject_run(db, run_id)` marks `rejected`. Only `parsed` runs can be
approved/rejected; approving a run whose source has a newer `parsed` run is
allowed (runs are independent snapshots; last write wins per irc+insurer).

**Global lock** — new `services/core/drug_catalog/harvest_lock.py`:
`acquire(owner: str) -> bool`, `release(owner)`, `holder() -> str | None`
(threading.Lock + owner tag). `nfi_harvest_service.start()` acquires as
`"nfi"`; coverage harvest as `"coverage:<insurer>"`. Both status endpoints
expose `lock_holder` so either GUI can explain why start is disabled.

### 2.3 API (extend `services/platform/routers/pricing.py`)

All under the existing router; write ops require `inventory:write`, reads
`inventory:read` (matching the NFI endpoints).

- `GET  /pricing/coverage/sources` — list; each item includes computed
  `due: bool` (`enabled && (last_run_at is null || now - last_run_at >
  check_interval_days)`) and the current `lock_holder`.
- `POST /pricing/coverage/sources` / `PUT /sources/{id}` / `DELETE /sources/{id}` — CRUD.
- `POST /pricing/coverage/sources/{id}/probe` — synchronous ProbeResult.
- `POST /pricing/coverage/sources/{id}/harvest` — start background run;
  `409 {detail: holder}` when the lock is held.
- `GET  /pricing/coverage/harvest/status` — live snapshot (running, source,
  phase, pages, rows, error, lock_holder).
- `GET  /pricing/coverage/runs?source_id=&limit=` — run history (no payloads).
- `GET  /pricing/coverage/runs/{id}` — full preview: stats, columns, diff,
  review items, unmatched sample.
- `POST /pricing/coverage/runs/{id}/approve` — body `{remove_missing: bool,
  accepted_review_ids: [..]}`.
- `POST /pricing/coverage/runs/{id}/reject`.

Unchanged: `POST /pricing/coverage/import` (one-shot upload, instant apply).

### 2.4 GUI — new dashboard section

`frontend/workstation/src/dashboards/CoverageAdmin.tsx`, registered in
`DashboardShell` as `{id:'coverage', label:'Insurance Coverage',
description:'دارونامه sources, harvest & review', shortcut:'Alt+B'}` (icon:
ShieldCheck/Database family). RTL, dark-slate, `Intl.NumberFormat('fa-IR')`,
react-query polling — same idiom as `DrugCatalogAdmin`.

Layout (top to bottom):
1. **Source cards** — one per `coverage_source`: insurer name, URL (editable),
   strategy select + strategy-relevant settings fields, `check_interval_days`,
   **"به‌روزرسانی لازم"** amber badge when `due`, last-run status/date.
   Buttons: **تشخیص** (probe), **برداشت** (harvest — disabled with tooltip
   "قفل برداشت: <holder>" when locked), edit/save, add-source card at the end.
2. **Probe modal** — detected format + proposed strategy, sample-rows table,
   inferred column roles each with a role dropdown (`irc, gtin, drug_name,
   covered, share_pct, reference_price, ceiling, inpatient, — ignore`);
   "ذخیره تنظیمات" persists overrides to the source.
3. **Live harvest strip** — progress (phase, page, rows) while a run is active;
   polls `harvest/status` at 2s when running (15s idle).
4. **Run preview panel** (latest `parsed` run, or via run-history list):
   stats tiles (rows / matched / review / unmatched), **diff summary**
   (added / changed / removed counts + expandable sample tables with
   field-level old→new), review-queue list with per-item accept checkbox,
   `remove_missing` checkbox (default off, warning copy), **اعمال** and
   **رد** buttons.
5. **Coverage upload card** — moved verbatim from `DrugCatalogAdmin`
   (which keeps NFI harvest + official-list import + stats).

### 2.5 Error handling

- Proxy down / unreachable host → probe returns / run fails with
  "پروکسی ایران در دسترس نیست یا مقصد پاسخ نمی‌دهد" + the transport error.
- Parse yields 0 rows → run `failed` ("جدولی شناسایی نشد — تنظیمات یا آدرس را
  بررسی کنید"); catalog untouched.
- No `drug_name`/`irc` role resolvable → run `failed` listing detected columns
  (mirrors the upload endpoint's 422).
- Oversized/malformed rows skipped and counted (`stats.skipped`), never fatal
  (NFI multivitamin lesson).
- Lock contention → 409 with holder; GUI disables rather than errors.
- `apply_run` transactional; partial failure rolls back, run stays `parsed`.

### 2.6 Testing (TDD)

Fixtures under `tests/fixtures/coverage/`: tiny synthetic دارونامه as `.xlsx`,
`.csv`, single-page HTML, 3-page paginated HTML (page 3 empty), JSON API pages.

- **Sniffer**: bytes → detected strategy for each fixture type.
- **Strategies**: each `fetch` against a fake fetcher (no network); pagination
  stops on empty page and `max_pages`; delay honored (mock sleep).
- **Overrides**: `column_overrides` beat `infer_columns`; "ignore" drops a column.
- **Diff**: added/changed/removed math incl. field-level old→new; changed
  detects each of covered/share_pct/reference_price/ceiling independently.
- **Lock**: NFI holder blocks coverage start and vice versa; release on failure.
- **Apply**: staged→`apply_coverage` writes; accepted review items spread to
  ingredient group; `remove_missing=False` leaves removed entries; `=True`
  deletes exactly the insurer key; run states enforce `parsed`-only approval.
- **Router**: source CRUD + due computation; 409 on locked harvest.
- **NFI parser**: `nfi_detail_17248.html` asserts `manufacturer`,
  `brand_owner`, `license_owner`, `license_valid_until`, `composition`,
  `pharmacokinetics`, `country`, `brands[]`, and monograph keys.
- **Importer/migration**: new columns round-trip through
  `build_records`→`upsert_catalog`; migration up/down; single Alembic head.

### Out of scope

- Headless-browser (JS-rendered / login) portals — the strategy interface
  leaves the slot; revisit if a real portal requires it.
- Automatic scheduling of harvests (manual + due badge only).
- Changing the one-shot upload endpoint's instant-apply behavior.
- PDF دارونامه parsing.
- Live insurer eligibility/claims (Phase 2 roadmap).

### Rollout

1. Migration (new catalog columns + coverage tables + seeds) — single head.
2. Backend engine + lock + routers (TDD).
3. GUI section + card move.
4. Next proxy session: full NFI re-crawl 1–60000 → ingest (backfills country
   et al., finishes the id range), then first real probe/harvest of an insurer
   source and receipt-level validation (roadmap Phase 1 exit).
