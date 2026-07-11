# Current Handoff

Status: READY_FOR_REVIEW
Workstream: inconsistencies-review-panel
Owner: Fable 5
Worker: opus-implementation-worker
Last Updated: 2026-07-11

## Objective

Add an always-available **"بازبینی ناسازگاری‌ها" (Review Inconsistencies)** panel
so the admin can, at any time (not only right after a harvest), review data
quality problems in the دارونامه coverage AND the NFI catalog. The panel must be
**comfortable to read** — the existing admin panels use `text-[11px]` cramped
rows and are eye-straining; this one must not be.

## Why

Coverage is now loaded (salamat on 23,859 products) but correctness is unproven:
fuzzy links can be wrong, insurer reference prices can conflict with announced
prices (drives مابه‌التفاوت), and the NFI catalog has products missing price /
generic / country. There is currently NO way to see these except transiently in
a fresh run's preview. This gives a durable, on-demand review surface.

## Scope (READ-ONLY — no writes, no mutation of coverage/catalog)

**Backend** — one pure helper + one endpoint:
- Pure helper in `services/core/drug_catalog/coverage_harvest.py`:
  ```python
  def price_conflict(irc, name_fa, announced, reference, threshold_pct):
      """→ {irc, name_fa, announced_price:int, reference_price:int, gap_pct:float}
      when abs((reference-announced)/announced*100) >= threshold_pct, else None.
      None if announced is falsy/<=0 or reference is None. gap_pct signed, round 1."""
  ```
- `GET /pricing/inconsistencies?insurer=salamat&price_threshold_pct=25`
  (permission `inventory:read`), returning:
  ```json
  {
    "insurer": "salamat",
    "coverage": {
      "counts": {"unmatched": N, "review": N, "price_conflicts": N},
      "unmatched": [ up to 200 items from the latest non-failed CoverageRun's `unmatched` JSONB ],
      "review":    [ that run's `review` JSONB (uncertain fuzzy links) ],
      "price_conflicts": [ price_conflict() over every drug_catalog row where
                     coverage ? insurer AND announced_price IS NOT NULL AND
                     coverage->insurer->>'reference_price' present; sorted by
                     abs(gap_pct) desc; cap 500 ]
    },
    "nfi": {
      "counts": {"no_price": N, "no_generic": N, "no_country": N, "no_atc": N, "total": N},
      "no_price":   [ {irc, name_fa} sample<=100 : announced_price IS NULL ],
      "no_generic": [ sample<=100 : generic_name IS NULL OR btrim(generic_name)='' ],
      "no_country": [ sample<=100 : country IS NULL ],
      "no_atc":     [ sample<=100 : atc IS NULL ]
    }
  }
  ```
  "latest non-failed CoverageRun for insurer" = `CoverageRun` where
  `insurer=:insurer AND status IN ('parsed','approved')` order by `started_at`
  desc limit 1; if none, coverage lists empty (counts 0), nfi still populates.

**Frontend** — `frontend/workstation/src/dashboards/CoverageAdmin.tsx`:
- A button **«بازبینی ناسازگاری‌ها»** ALWAYS visible in the panel header (next to
  the `<h2>`), independent of any run. Toggles an inline panel (not a cramped
  disclosure).
- Panel: insurer `<select>` (tamin/salamat/armed_forces) + a price-gap threshold
  number input (default 25). Two top-level tabs **«پوشش بیمه»** / **«کاتالوگ NFI»**
  with count badges.
  - Coverage tab → three labelled sections: نامنطبق (unmatched), نیازمند بازبینی
    (review — candidate name + confidence), مغایرت قیمت (price_conflicts as a
    TABLE: نام | قیمت اعلامی | قیمت مرجع بیمه | اختلاف٪, numbers right-aligned
    `tabular-nums`, gap cell color-graded: |gap|≥50 red, ≥25 amber, else slate).
  - NFI tab → four labelled sections (no_price/no_generic/no_country/no_atc),
    each an IRC | نام table.
- **Readability bar (the point of this task):** row text `text-sm` (NOT
  text-[11px]), row padding ~`py-2`, real `<table>` with sticky `<thead>`, subtle
  row separators/hover, generous section spacing, count badges, and a friendly
  emerald empty state per section ("هیچ ناسازگاری‌ای یافت نشد ✓"). Each section
  scrolls independently (`max-h-…`); the page body must not scroll horizontally.
- `frontend/workstation/src/lib/api.ts`: add to `pricingApi`
  `inconsistencies: (insurer: string, threshold: number) => apiClient.get('/pricing/inconsistencies', { params: { insurer, price_threshold_pct: threshold } })`.

## Out of Scope

- No writes/edits/resolution actions — review-only. No new DB columns, no
  migration, no new dependency. Do not touch harvest/probe/approve/diagnostics.
  Do not restyle other panels.

## Existing Decisions / Invariants

- `drug_catalog` columns: `irc, name_fa, generic_name, ingredient_key,
  announced_price (Numeric), atc, country, coverage (JSONB)`. Coverage shape:
  `{"salamat": {"covered": true, "share_pct": 70, "reference_price": 3100000, ...}}`.
- Endpoints live in `services/platform/routers/pricing.py` beside the other
  `/coverage/*` routes; copy their exact style (local imports inside the fn,
  `Depends(require_permission("inventory:read"))`, `AsyncSession = Depends(get_db)`).
  Read that file's `list_coverage_runs` / `get_coverage_run` for the CoverageRun
  query pattern before writing.
- Frontend: React 19 + Tailwind, RTL, `Intl.NumberFormat('fa-IR')` via the
  existing `fa()` helper, react-query; reuse `apiErrorText` for errors.
- Graphify is mandatory before reading source: run `graphify query "<q>"` first;
  read raw files only to modify/debug specific lines.
- pytest = miniforge `python3 -m pytest` (NOT `.venv`).

## Acceptance Criteria

1. `price_conflict` unit tests pass (below-threshold→None; above→dict with
   correct signed rounded gap_pct; announced<=0→None; reference None→None).
2. `GET /pricing/inconsistencies?insurer=salamat` → 200 with the documented shape;
   `coverage.counts.price_conflicts == len(price_conflicts)`; a nonsense insurer
   yields empty coverage lists but populated nfi.
3. Button visible with NO run selected; clicking opens the panel; changing
   insurer/threshold refetches; both tabs render real salamat data.
4. Readability: rows `text-sm`, numeric columns right-aligned `tabular-nums`,
   visible empty state for any zero section.
5. `npx tsc -b` adds NO new errors in `CoverageAdmin.tsx` / `api.ts`.
6. No pre-existing unit test regresses.

## Verification Plan

- `python3 -m pytest tests/unit/test_coverage_harvest.py -q` (incl. new tests).
- Restart backend (launchd KeepAlive respawns on `kill`), then
  `curl -s '.../api/v1/pricing/inconsistencies?insurer=salamat&price_threshold_pct=25' -H "Authorization: Bearer <admin>"`
  → paste counts + one price_conflict + one nfi sample.
- `cd frontend/workstation && npx tsc -b 2>&1 | grep -E "CoverageAdmin|api.ts"` → empty.
- Browser: admin/PharmPilot2024! → Insurance Coverage (Alt+B) → click the button,
  screenshot both tabs, confirm no console errors.

## Escalation Conditions

- Latest-run query or coverage JSONB shape doesn't match reality → STOP, report.
- A "resolve/fix" action seems needed to be useful → STOP (Fable scope decision).
- Anything that would write to drug_catalog/coverage → STOP.

## Implementation Result

Status: READY_FOR_REVIEW — done, all acceptance criteria pass.

Files changed (4):
- `services/core/drug_catalog/coverage_harvest.py` — added pure `price_conflict(irc,
  name_fa, announced, reference, threshold_pct)` helper (signed rounded gap_pct;
  None on falsy/<=0 announced, None reference, or non-numeric). No I/O.
- `services/platform/routers/pricing.py` — added `GET /pricing/inconsistencies`
  (`inventory:read`), local imports inside the fn, matching the other coverage
  routes' style. Coverage lists come from the latest `status IN ('parsed',
  'approved')` CoverageRun (unmatched capped 200, review as-is); price_conflicts
  scan drug_catalog rows where `coverage ? insurer AND announced_price IS NOT NULL
  AND coverage->insurer ? 'reference_price'`, sorted by abs(gap) desc, cap 500.
  NFI section = 4 count+sample(≤100) pairs + total. READ-ONLY.
- `frontend/workstation/src/lib/api.ts` — added `pricingApi.inconsistencies`.
- `frontend/workstation/src/dashboards/CoverageAdmin.tsx` — always-on
  «بازبینی ناسازگاری‌ها» toggle button next to the `<h2>`; new
  `InconsistenciesPanel` (insurer select + threshold input + two tabs with count
  badges). Comfortable readability: real `<table>` with sticky `<thead>`,
  `text-sm` rows, `py-2` padding, right-aligned `tabular-nums` numerics, hover +
  row separators, per-section `max-h` scroll, gap-cell color grading
  (|gap|≥50 red / ≥25 amber / else slate), and emerald empty states.

No writes, no migration, no new columns, no new deps, no other panels touched.

## Verification Evidence

- Unit (TDD, red→green): `python3 -m pytest tests/unit/test_coverage_harvest.py -q`
  → **28 passed** (8 new price_conflict tests: below-threshold→None, above→signed
  rounded dict, negative gap, boundary =threshold, announced 0/None/Decimal(0)→None,
  negative announced→None, reference None→None, Decimal+string numerics).
- Full unit suite: `python3 -m pytest tests/unit -q` → 7 failed, **674 passed**.
  The 7 failures (`test_intake_precompute.py` ×6, `test_integrations_sandbox.py` ×1)
  are PRE-EXISTING/environmental — reproduced identically with my edits `git stash`ed,
  and none reference coverage_harvest/pricing/frontend. Zero regressions from this change.
- Endpoint (backend respawned via `kill $(pgrep -f "uvicorn services.platform.main")`,
  health 200 after 8s):
  `GET /api/v1/pricing/inconsistencies?insurer=salamat&price_threshold_pct=25` → 200.
  - `coverage.counts` = `{unmatched: 200, review: 684, price_conflicts: 500}`;
    `len(price_conflicts) == counts.price_conflicts` (500) ✓.
  - top price_conflict: `{irc:'4263712643109596', name_fa:'رانیتیدین 150',
    announced_price:650, reference_price:269156300, gap_pct:41408561.5}` (genuine
    data-quality outlier — announced 650﷼ is implausible); tail gap_pct 3014.7.
  - `nfi.counts` = `{no_price:4257, no_generic:0, no_country:39044, no_atc:23,
    total:39044}`; sample e.g. no_price `{irc:'0000000537447574', name_fa:'قرص
    آزاتیوپرین 50 میلی گرم'}`.
  - Nonsense insurer `zzz` → coverage lists all empty (counts 0), nfi still
    populated (no_price:4257 …) ✓. Default (no query args) → 200 ✓.
- Frontend typecheck: `cd frontend/workstation && npx tsc -b 2>&1 | grep -E
  "CoverageAdmin|api.ts"` → **empty** (no new errors in the two files). Remaining
  tsc errors are all pre-existing in unrelated files (PatientAdherence,
  SecuritySurveillance, DashboardShell, useAIProvider, MedReconciliationPage).
- Browser (admin/PharmPilot2024! → Insurance Coverage): button visible with NO run
  open; clicking opens the panel; Coverage tab renders نامنطبق(200)/بازبینی(684)/
  مغایرت قیمت(500) as real tables with right-aligned Persian numerals and red
  gap grading; NFI tab renders the four sections, with the emerald empty state
  "همهٔ اقلام نام ژنریک دارند ✓" for no_generic(0). No console errors.
- `graphify update .` run (7301 nodes rebuilt).

## Remaining Risks / Questions

- The price_conflicts scan pulls all matching salamat rows into Python each request
  (filtered by has_key + announced-not-null + reference-present, so a subset of the
  ~39k catalog, not all). Fine for an on-demand admin endpoint; if it ever feels
  slow, push the gap math into SQL. No caching added (intentional — always fresh).
- Data findings surfaced (NOT code bugs, for Fable's awareness): (a) many salamat
  reference_price values dwarf announced_price by absurd factors (e.g. رانیتیدین
  650 vs 269M) — unit-mismatch or bad announced data worth investigating; (b)
  `no_country == total (39044)` → the country column is unpopulated for the whole
  catalog (NFI enrichment never filled it). Both are exactly what this panel is
  meant to expose; no fix attempted (out of scope, read-only).
- `counts.unmatched`/`counts.review` reflect the stored (unmatched-capped-at-200)
  list lengths, matching what the panel displays, rather than the run's original
  stat totals.

## Durable Learnings

- CoverageRun JSONB shapes (confirmed in `coverage_import.build_coverage`):
  `unmatched` = `[{row, confidence}]` (row has `drug_name`, no irc/name);
  `review` = `[{id, row, irc, name(=candidate catalog name_fa), confidence,
  entry, accepted}]`. Latest usable run = `status IN ('parsed','approved')`.
- `drug_catalog.coverage` entry shape includes `reference_price` (int) alongside
  `covered`, `share_pct`, `match_confidence`, `match_method`.
- JSONB filters that work here: `DrugCatalogItem.coverage.has_key(insurer)` and
  `DrugCatalogItem.coverage[insurer].has_key('reference_price')`.
- Backend :8001 is launchd KeepAlive — `kill $(pgrep -f "uvicorn
  services.platform.main")` respawns onto new code; health 200 in ~8s.
- Pre-existing broken unit tests on this branch: `test_intake_precompute.py` (6)
  and `test_integrations_sandbox.py` (1) — ignore when gating this workstream.
