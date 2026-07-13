---
name: coverage-jsonb-shapes
description: CoverageRun/drug_catalog JSONB shapes, latest-run selector, JSONB filters, and launchd backend restart for the دارونامه coverage subsystem
metadata:
  type: project
---

Durable facts for the دارونامه coverage / pricing subsystem (verified 2026-07-11).

**CoverageRun JSONB shapes** (produced by `coverage_import.build_coverage`, staged
by `coverage_harvest.stage_run_payload`):
- `unmatched` = `[{row, confidence}]` — `row` is the source dict with a `drug_name`
  key; no irc/name (these are rows that couldn't be linked). Capped at 200 in staging.
- `review` = `[{id, row, irc, name, confidence, entry, accepted}]` — `name` is the
  candidate catalog `name_fa`; `row.drug_name` is the source name.
- Latest usable run for an insurer = `status IN ('parsed','approved')` order by
  `started_at` desc limit 1.

**drug_catalog.coverage** entry shape: `{"<insurer>": {covered, share_pct,
reference_price(int), match_confidence, match_method, ...}}`.

**Working JSONB filters (SQLAlchemy):** `DrugCatalogItem.coverage.has_key(insurer)`
and `DrugCatalogItem.coverage[insurer].has_key('reference_price')`.

**Pure data-quality helper:** `coverage_harvest.price_conflict(irc, name_fa,
announced, reference, threshold_pct)` → signed rounded gap_pct dict or None.

**Read-only review endpoints** in `services/platform/routers/pricing.py`
(permission `inventory:read`): `GET /pricing/inconsistencies` (list) and
`GET /pricing/inconsistencies/drug/{irc}?insurer=` (per-drug workbench detail:
catalog + all-insurer coverage + same-generic siblings + `diagnose_discrepancy`
Persian hints). Pure hint helper: `coverage_harvest.diagnose_discrepancy(catalog,
entry, siblings)` (sits after `price_conflict`).

**Router mount prefix:** platform routers are mounted under `/api/v1` in
`services/platform/main.py` — real paths are `/api/v1/pricing/...` and auth login
is `POST /api/v1/auth/login` (JSON `{username,password}`). Bare `/pricing/...` or
`/auth/login` return 404. admin / PharmPilot2024!.

**Why:** these shapes aren't obvious from the models alone and are needed whenever
building read surfaces over staged coverage runs or the catalog.

**How to apply:** reuse these shapes/filters instead of re-deriving; follow the
other `/coverage/*` routes' style (local imports inside the fn,
`Depends(require_permission(...))`, `AsyncSession = Depends(get_db)`).

**Backend restart:** :8001 is a launchd KeepAlive service — `kill $(pgrep -f
"uvicorn services.platform.main")` respawns it onto new code; health 200 in ~8s.

**Pre-existing broken unit tests on branch feat/darunameh-crawler:**
`tests/unit/test_intake_precompute.py` (6) and `test_integrations_sandbox.py` (1)
fail independently of coverage work — don't treat them as regressions here.
See [[darunameh-coverage-crawler]] in the user auto-memory for pipeline status.
