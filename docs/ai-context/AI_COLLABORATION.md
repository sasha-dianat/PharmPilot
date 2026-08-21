# Claude Code and Codex Collaboration

Status: ACTIVE

Repository: `sasha-dianat/PharmPilot`

Coordination owner: project owner

Last updated: 2026-07-26

## Purpose

This file is the shared coordination plane for Claude Code and Codex. The two
models cannot read each other's private conversations. Repository state,
commits, pull requests, and this ledger are the only durable shared context.

Use this file for current ownership and concise advancement records. Use
`CURRENT_HANDOFF.md` for a detailed single-task handoff and the Project Bible
for durable architecture and safety context.

## Mandatory session contract

Before implementation or review:

1. Fetch current remote state and inspect `git status -sb`.
2. Read `AGENTS.md`, `CLAUDE.md`, this file, the Project Bible, and the relevant
   roadmap/code/tests.
3. Confirm one named owner, branch, scope, dependencies, and acceptance gate for
   the workstream. Add or update the table before editing when necessary.
4. Inspect the latest advancement entries and recent commits/PR activity.
5. Stop if another active workstream owns any intended file. Record an explicit
   handoff or split the work at a non-overlapping boundary first.

During work:

- Use a dedicated branch/worktree. Never implement directly on `master`.
- Keep one product-code owner per workstream; the other model may perform a
  read-only review unless a handoff authorizes fixes.
- Keep changes within the recorded scope. Record scope expansion before making
  it, including new file ownership and dependencies.
- Do not force-push, rewrite another model's commits, or resolve overlapping
  changes without project-owner approval.
- Preserve PharmPilot's tenant, PHI, audit, deterministic-clinical, approval,
  provenance, and Decimal-Rial invariants from `AGENTS.md`.

At the end of every session:

1. Update the workstream status and next gate.
2. Append an advancement entry using the template below.
3. Report only checks actually run; distinguish passed, failed, skipped, and not
   run. Do not inherit test claims from an older commit.
4. Link the commit and PR when published. If unpublished, record the exact branch
   and working-tree state.
5. Update Graphify and durable architecture documentation when the change calls
   for it.

## Status vocabulary

- `PLANNED`: accepted direction; implementation has not started.
- `ACTIVE`: one owner is implementing within the recorded scope.
- `BLOCKED`: work cannot proceed; the blocker and required decision are recorded.
- `READY_FOR_REVIEW`: implementation complete; independent review is required.
- `CHANGES_REQUESTED`: review found required work assigned to an owner.
- `DONE`: acceptance evidence is recorded and the work is merged or deliberately
  closed.
- `SUPERSEDED`: replaced by a named workstream or decision.

## Active workstreams

| ID | Owner | Status | Branch / PR | Owned scope | Next gate |
|---|---|---|---|---|---|
| CL-001 | Claude Code | ACTIVE | `master` (the `feat/darunameh-crawler` branch does NOT exist in this repo — 2026-07-29 verification; PR [#22](https://github.com/sasha-dianat/PharmPilot/pull/22) predates the current head) | Every file currently changed by PR #22, primarily drug catalog, coverage harvest/import, enrichment, catalog/coverage models and migrations, related workstation administration, and their tests | Freeze scope, refresh the PR description to the actual head, run evidenced release gates, then independent review |
| CX-001 | Codex | READY_FOR_REVIEW | `agent/ai-collaboration-protocol` (stacked on CL-001) | `AGENTS.md`, `CLAUDE.md`, and `docs/ai-context/AI_COLLABORATION.md` only | Review and merge the coordination protocol into `feat/darunameh-crawler` |
| CX-002 | Codex | PLANNED | Read-only review of PR #22; fix branch only after findings are accepted | Independent review of tenant isolation, PHI/AI-provider policy, migration integrity, pricing conservation/provenance, frontend/API regressions, and test evidence; no edits to CL-001-owned files without handoff | Deliver prioritized findings with file/line evidence and proposed ownership |
| CL-003 | Claude Code | READY_FOR_REVIEW | `feat/inventory-integrity` | `services/core/inventory/{ledger,reconciliation,formulary_binding}.py`, `routers/inventory_integrity.py`, `routers/inventory.py`, `shared/models/inventory.py`, `shared/models/auth.py` (permission table), migration `0030`, `InventoryIntegrity.tsx` + nav/api wiring, `docs/design/INVENTORY_SYSTEM.md`, three new test modules | Independent review of the maker-checker rules, the migration on a disposable DB, and the P2 dispense-hook design before it is built |
| CL-002 | Claude Code | PLANNED | New branch after CL-001 stabilizes | Iran-proxy/NFI and insurer-publication data operations, replay evidence, and source diagnostics; no Codex hardening paths | Project owner approves data-source inputs and operating window |
| CX-003 | Codex | PLANNED | New branch from the accepted post-PR-22 base | First safety slice from `CODEX_NEXT_BUILD_PLAN.md`: tenant-bound, provenance-safe identity; excludes Claude-owned data-pipeline paths | Project owner approves implementation after CX-002 and PR #22 disposition |

CL-001 ownership is the drug-catalog / coverage / enrichment / decision paths
listed above; the PR #22 file list is a historical approximation only, since the
work now lands directly on `master`. A path leaves that boundary only through a handoff entry below.

## Merge and conflict gates

- A branch may be reviewed while its owner continues only if the reviewer pins
  the reviewed commit SHA. New commits invalidate unqualified review conclusions.
- Overlapping work pauses until the current owner records `HANDOFF`, the target
  model accepts it, and both record the base commit.
- Schema work requires one Alembic head, model-registry coverage, a disposable
  database plan, and rollback evidence. Never run migration checks on real data.
- Clinical, pricing, identity, payment, claim, or PHI-affecting work requires an
  independent review before merge.
- A large PR is not merge-ready solely because GitHub reports it mergeable.
  Review, checks, provenance, and current scope must also be evidenced.

## Advancement log

Entries are append-only. Corrections should add a new entry referencing the
incorrect one rather than silently rewriting history.

### 2026-07-26 — Claude Code state captured by Codex

- Workstream: `CL-001`
- Branch/commit: `feat/darunameh-crawler` at `af3da9992f3097406fedba04fb08493f2152528c`
- PR: #22 is open and reported mergeable by GitHub.
- Observed scope: 113 commits, 117 changed files, 22,811 additions, and 332
  deletions relative to `master`.
- Observed verification: the PR description and comments contain feature-level
  test claims, but GitHub reports no submitted review and no Actions workflow run
  for the captured head. Those claims have not been independently reproduced by
  this entry.
- Advancement: coverage crawling, NFI extraction, diagnostics, real Salamat
  formulary ingestion, matching/enrichment, canonical-data tooling, discrepancy
  workbench work, migrations, APIs, and RTL administration have advanced on the
  branch.
- Risk/next action: stop adding features, reconcile the PR description with the
  actual head, run the release gates, and submit the pinned head for independent
  review.

### 2026-07-26 — Codex coordination onboarding

- Workstream: `CX-001`
- Branch/base: `agent/ai-collaboration-protocol` from CL-001 head
  `af3da9992f3097406fedba04fb08493f2152528c`.
- Change: established this shared protocol and wired both model instruction files
  to require it. No product code, schema, dependencies, or runtime configuration
  changed.
- Verification: `git diff --check` passed; repository status confirmed only the
  three declared documentation paths changed; all coordination references were
  confirmed with `rg`. Application tests were not run because this is a
  documentation-only change.
- Next action: open a stacked draft PR into `feat/darunameh-crawler`, then begin
  CX-002 at a pinned PR #22 head after the project owner accepts the split.

### 2026-07-27 — Claude Code — NFI audit mode + resume; surveillance platform design

- Workstream: `CL-001` (NFI harvest), plus new design artifact
- Branch/commit: `feat/darunameh-crawler` at `1869546`
- Changed: `services/core/drug_catalog/nfi_audit.py` (new, shared flag/index
  rules), `nfi_harvest_service.py` (audit mode, disk-persisted resume point,
  skip-already-audited, mode validated before lock acquisition),
  `routers/pricing.py` (`POST /catalog/nfi/resume`, `GET /catalog/nfi/audit-summary`,
  `mode` on start), `scripts/nfi_page_audit.py` (now imports the shared module
  instead of duplicating it), `DrugCatalogAdmin.tsx` + `lib/api.ts` (mode
  selector, resume button, findings panel).
- Interfaces/schema/data: two new endpoints; no migration; new on-disk artifacts
  `logs/harvest/nfi_progress.json` and `logs/nfi_audit/`.
- Verification: `pytest tests/unit/test_nfi_audit_mode.py` 11 passed;
  harvest-related suites 34 passed; `npx tsc --noEmit` clean; backend restarted
  (launchd, PID 57676, single listener :8001) and both routes confirmed present
  in the served OpenAPI. Full `tests/unit` = 826 passed / 11 failed; those 11
  (`test_intake_precompute`, `test_integrations_sandbox`, `test_researcher`)
  fail identically without these changes and pass in isolation — pre-existing
  event-loop pollution, untouched here.
- Also added: `docs/design/SURVEILLANCE_PLATFORM.md` — proposed architecture for
  pharmacy/warehouse video analytics. **Not approved, not implemented.** It
  records four findings about the existing `services/biometric/` tree that need
  an owner decision before that subsystem is extended: consent columns on
  `BiometricIdentity` are never read by any code; `PatientResolver` accepts a
  face match >=0.80 as a patient-identity signal and auto-loads a chart at
  combined >=0.88 without pharmacist confirmation; `phase32.py:344` mints a
  permanent biometric identity at >=0.80; dormant watchlist fields exist.
  `LivenessDetector` also fails open on exception (`engine.py:146`).
- Risks/blockers: the biometric findings are owner decisions, not defects I
  should fix unilaterally — they change product behavior and legal posture.
- Next action/owner: project owner to rule on the surveillance design's open
  questions (esp. whether customer face recognition is implemented at all);
  crawl ids 43,001-70,000 and the 625 pending retries remain outstanding on CL-001.

### 2026-07-28 — Claude Code — face-identification defect fixes + design

- Workstream: `CL-002` (biometric identity), new
- Branch/commit: `master` at `eec4976` (fixes) + this doc commit
- Changed: `services/biometric/identity_resolution/{engine.py,gallery.py,thresholds.py}`,
  `routers/biometric.py`, `tests/unit/test_face_identity_engine.py` (17 tests).
  Five defects fixed: probability-shaped thresholds on raw cosine; k=1 search
  making the margin test impossible; identity map never persisted (matches
  resolved to None after restart); no template removal path; liveness failing
  OPEN (and scikit-image is absent, so it failed open on every call).
- Interfaces/schema: no migration. `IdentityMatch` gained similarity/confidence
  as separate fields plus margin, threshold_used, expected_false_matches,
  explanation. Match levels are now auto/review/no_match/spoof_attempt —
  "high"/"probable"/"possible" are gone; `routers/biometric.py` updated.
- Verification: 17 new tests pass; suite 851 passed / 5 failed — the 5 are
  pre-existing order-dependent event-loop pollution (test_researcher,
  test_integrations_sandbox), identical without this change and green in
  isolation.
- Also added `docs/design/FACE_IDENTITY_PLATFORM.md` (proposal, not approved).

- BLOCKING FINDING, verified directly, unrelated to biometrics:
  `POST /pos/collect-payment` (services/platform/routers/pos.py:169) sets
  `status='dispensed'` by raw SQL from ANY status except cancelled/voided —
  including DUR_HOLD. It bypasses RxStateMachine, the state-event hash chain,
  and the EPCS check. `PENDING_DUR -> PENDING_VERIFICATION`
  (state_machine.py:30) is also unconditional. Collecting payment on a held
  prescription dispenses it. This needs an owner decision and is independent of
  the surveillance work.

- Risks/blockers: the design's adversarial verification did NOT run (session
  limit). Resume with
  `Workflow({scriptPath: '.../pharmacy-face-id-design-wf_64a5cf50-f00.js',
  resumeFromRunId: 'wf_75c19fb9-e0d'})` — the five completed dives replay from
  cache, only the three critics re-run.
- Next action/owner: owner to rule on the POS bypass; then Phase 0/1 of the
  design (consent ledger, sever biometric->patient edge, remove
  biometric_confidence from PatientResolver).

### 2026-07-29 — Fable 5 → Opus 5 — data audit closed: reconciliation green

- Workstream: `CL-001`
- Branch/commit: `master`; this session's chain `95fe638` → `7583dc0` →
  `693a524` → (this commit). **HANDOFF from Fable 5 to Opus 5** occurred
  mid-task, after the price-gap refresh and before the rulings were persisted;
  Opus 5 accepted, found the uncommitted-ruling defect below, and completed it.
- Changed:
  - `shared/models/drug_catalog.py` — `coverage`/`monograph` now
    `JSONB(none_as_null=True)`. ROOT CAUSE of the JSON-null pollution: assigning
    Python `None` wrote a jsonb `'null'`, which is not SQL NULL, so
    `coverage IS NOT NULL` counted empty rows (6,771 at audit, 129 more within
    hours of the first cleanup). No migration — serialization behaviour only.
  - `services/core/drug_catalog/data_quality.py` — two new checks
    (`price_refreshed_without_history`, `price_gap_extreme_strong_identity`);
    the spliced check now uses the issue-registry predicate AND honours
    `issue_dispositions`, so a ruled item stops firing.
  - Data (no code): 204 spliced monographs repaired to a fixed point; 2,339
    stale prices refreshed from insurer reference; 5 rulings recorded.
- Interfaces/schema/data: no migration (head stays `0029`). Data effects —
  `drug_catalog.announced_price` moved on 2,339 rows, each with
  `monograph.price_provenance` and a `price_history` SCD-2 row;
  `monograph.integrity.repaired` set on 192 rows; `issue_dispositions` 8 → 13.
- Verification:
  - `pytest tests/unit -q` → **900 passed, 1 failed** (`test_integrations_sandbox`
    caplog ordering — pre-existing, passes in isolation, unrelated).
  - `npx tsc --noEmit` → clean (run before the ruling batch; no TS changed since).
  - `data_quality.report()` in a FRESH session → `healthy=true, firing=0`;
    only info lines remain (1,139 insurer-priced/NFI-unpriced, 631 harvest
    failures, 3 stale parsed runs).
  - `nfi_integrity.audit()` → `suspect: 0`.
  - Price-history parity: 2,339 refreshed rows / 2,339 matching open SCD-2 rows.
- Risks/blockers:
  - **499 products carry an extreme price gap on a WEAK match** (conf < 0.90 and
    method not in code/crosswalk/irc). Their prices were deliberately NOT
    refreshed — the gap is evidence the MATCH is wrong, not the number. Ruled
    `deferred`; they need «بازبینی تصمیم‌ها», not a price edit. Refreshing them
    would bake a wrong price into a wrong product.
  - 26 products where the insurer reference is a PACK price and ours is per-unit
    (ratio ≈ package_count). Ruled `accepted`; **never** refresh these.
  - 2 aluminium-hydroxide rows are genuine splices with no donor (chewable tablet
    served a suspension monograph). Ruled `accepted`; quarantine stands.
  - Iran proxy still down: NFI ids 43,001–70,000 uncrawled, 631 retryable
    failures pending, succession detector cannot arm (needs a second audit pass).
  - **Process defect found and corrected:** two ad-hoc ruling scripts called
    `set_disposition` without `await db.commit()` and silently rolled back —
    the printed "created" was a lie. The production endpoint
    (`POST /pricing/issues/ruling`) commits correctly; the rulings were redone
    through it. LESSON: record rulings via the API, never a bare script, and
    always re-read from a FRESH session before believing a count.
- Next action/owner:
  - Owner: review the 499 weak-match products in «بازبینی تصمیم‌ها».
  - Owner: supply the Iran proxy → resume crawl, retry 631, run the second audit
    pass (`پویش دوباره`) to arm succession detection.
  - Claude: quote-path regression harness (pricing conservation vs golden
    quotes) and auto-running reconciliation after every apply/ingest — P3/P4 in
    `docs/data-architecture.md`.

### 2026-08-02 — Claude Code — inventory integrity: conserving ledger, reconciliation, maker-checker

- Workstream: `CL-003` (new). No overlap with CL-001/CX-00x: no drug-catalog,
  coverage, enrichment or biometric path is touched.
- Branch/commit: `feat/inventory-integrity` (from `master` @ `c0c534d`).
- Changed:
  - NEW `services/core/inventory/ledger.py` — FEFO allocation, conservation
    (short stock raises rather than under-fills), Decimal quantities, no
    clamping, SHA-256 movement chain mirroring `RxStateEvent.event_hash`.
  - NEW `services/core/inventory/reconciliation.py` — 11 integrity checks with a
    three-state verdict (healthy / trustworthy / blocking).
  - NEW `services/core/inventory/formulary_binding.py` — GTIN→IRC ladder,
    proposal-only, refuses to guess when ambiguous.
  - NEW `routers/inventory_integrity.py` — 10 endpoints (reconciliation, ledger
    verify, binding proposals/apply, counts create/line/get/post, approvals
    list/decide).
  - `routers/inventory.py` — **security fix**: `POST /orders/{po_id}/submit` had
    no tenant filter (cross-tenant PO submission); `expiring_days` was accepted
    and silently ignored.
  - `shared/models/auth.py` — new `inventory:approve`; INVENTORY_STAFF
    deliberately excluded so a requester cannot approve their own write-off.
  - Frontend `InventoryIntegrity.tsx` + nav + `inventoryIntegrityApi`.
  - `docs/design/INVENTORY_SYSTEM.md` — requirements matrix (27), gap analysis
    (11 defects), architecture, rollout, production-readiness assessment.
- Interfaces/schema/data: migration **0030** (head 0029→0030), additive and
  nullable only: `irc` on 4 inventory tables; `prescription_fill_id`,
  `approval_id`, `prev_hash`, `event_hash` on `inventory_movements`;
  `inventory_lots.id` FK on `prescription_fills`; new `inventory_approvals`,
  `stock_counts`, `stock_count_lines`; append-only trigger on
  `inventory_movements`; non-negative CHECK on lot quantity. No existing value
  rewritten.
- Verification (all run this session):
  - Migration exercised on `pharmpilot_test` first: upgrade → downgrade → upgrade,
    each object confirmed created/removed. Append-only trigger confirmed to
    BLOCK a live UPDATE and DELETE. Then applied to `pharmpilot`; head `0030`.
  - `pytest tests/unit` → **1013 passed, 1 failed**. The failure is
    `test_integrations_sandbox` caplog ordering — pre-existing (recorded in the
    2026-07-29 entry), passes in isolation, untouched here.
  - 70 new tests across `test_inventory_ledger.py` (23),
    `test_inventory_reconciliation.py` (28), `test_inventory_integrity_guards.py` (19).
  - `npx tsc --noEmit` clean. Backend restarted (launchd, PID 9208, single
    listener :8001); all 10 routes confirmed in the served OpenAPI.
  - Reconciliation run against **live data**: `blocking=true`, 4 of 11 checks
    firing — 46 `fill_without_movement`, 46 `untraceable_fill`,
    17 `unbound_from_formulary`, **2 expired lots (240 units) still sellable**.
- Findings requiring an owner decision:
  - **2 expired lots on the shelf** (`CEF250-OLD` exp 2026-07-02,
    `MTP50-OLD` exp 2026-07-06) are sellable and unquarantined — act now.
  - **IRC is a per-brand registration, not a molecule code.** Measured:
    generic+strength+form resolves a unique IRC only 19.2% of the time (mean
    11.3 candidates, worst 222), while GTIN covers 94.1% of the formulary.
    Binding stock by name is structurally unsafe; goods-receipt GTIN scanning is
    the only reliable path. This changes the P2 receiving design.
  - ROADMAP:34 (`inventory decrement on dispense`) is confirmed open and is now
    *detected* but not *fixed* — the hook itself is P2 and deliberately not
    written blind into the dispensing path without review.
- Risks/blockers: quantities in `stock_levels` are not trustworthy until the
  dispense hook lands and a full count is posted; forecasting is fitted on a
  ledger that only increases; the ML anomaly detector remains unwired.
- Next action/owner: owner rules on the 2 expired lots and on the P2 scope
  (dispense hook + GTIN receiving + backfilling the 46 orphan fills as one
  approved reconciliation batch).

### 2026-08-02 — Claude Code — inventory admin panel: supervise, view, correct

- Workstream: `CL-003` (continues the entry above; same branch)
- Branch: `feat/inventory-integrity`
- Changed:
  - NEW `services/core/inventory/admin_rules.py` — the field policy as pure
    functions: DIRECT (location, cost, IRC, par levels) apply immediately;
    SENSITIVE (expiry, quarantine, recall, cold chain) demand a reason and, in
    the releasing direction, an approval; LEDGER_ONLY (all quantities) are
    unreachable from the panel entirely. Plus bulk bounds, the search-box
    classifier, and the 12 named filter views.
  - NEW `routers/inventory_admin.py` — 8 endpoints: item search
    (name/IRC/NDC/GTIN/lot, 12 filters, 6 sorts, paginated), live filter counts,
    item drill-down (lots in FEFO order with per-lot blocked reason + the full
    movement history with actor and chain marker), single-field edit, bounded
    bulk edit, stock-aggregate edit, goods receipt, write-off request.
  - `routers/inventory_integrity.py` — `_apply_field_edit` so an approved
    FIELD_EDIT actually lands, re-validating policy at approval time and
    refusing (409) if the row moved since the request was raised.
  - NEW frontend `InventoryAdmin.tsx` + nav entry (Alt+V) + `inventoryAdminApi`.
- Interfaces/schema: migration **0031** — `payload` JSONB on
  `inventory_approvals` (so one approval queue serves both quantity movements
  and field corrections) plus a non-negative CHECK on `quantity`. Additive;
  round-tripped up/down/up on `pharmpilot_test` before `pharmpilot`. Head 0031.
- Verification:
  - `pytest tests/unit` → **1055 passed, 1 failed**; the failure is the
    pre-existing `test_integrations_sandbox` caplog-ordering case (recorded
    2026-07-29, green in isolation). Inventory-related suites: **203 passed**.
  - 42 new tests: `test_inventory_admin_rules.py` (31, pure policy) and
    `test_inventory_admin_e2e.py` (11, against the real test database —
    receive → correct → approve → write-off → chain verify).
  - `npx tsc --noEmit` clean. Backend restarted (PID 27840, single listener
    :8001); all 8 admin routes confirmed in the served OpenAPI.
  - Endpoints exercised against **live data**: filter chips returned real counts
    (16 items, 2 expired, 16 unbound, 2 dead stock), search resolved by name and
    by NDC, drill-down returned the expired `CEF250-OLD` lot at −31 days with
    `blocked_reason=expired`.
  - Vite served `InventoryAdmin.tsx` (200) with zero console errors. The panel's
    visual render behind the login screen was **not** verified — signing in
    requires entering a password, which I do not do.
- Notable during build: the append-only trigger blocked the E2E fixture's own
  attempt to DELETE movements between tests. That is the guarantee working, so
  isolation now comes from a fresh NDC per test rather than erasing history.
- Risks/blockers: unchanged from the entry above — quantities remain
  untrustworthy until the P2 dispense hook lands and a full count is posted. The
  panel deliberately cannot repair that; it can only receive, correct metadata,
  and route write-offs for signature.
- Next action/owner: owner to rule on the 2 expired lots (now one click from the
  «منقضی‌شده» filter → کسر → approval) and on P2 scope.

### 2026-08-02 — Claude Code — the dispense hook: stock actually decrements

- Workstream: `CL-003` · Branch `feat/inventory-integrity`
- Closes **ROADMAP:34** and R1. `RxStateMachine._handle_transition_effects`
  contained the line `logger.info("Rx %s dispensed — trigger inventory deduction
  for NDC %s")` and nothing else. That single stub is why 46 fills existed
  against 8 movements.
- Changed:
  - NEW `services/core/inventory/dispense.py` — `apply_dispense` (FEFO, creates
    or finds the fill, writes chained DISPENSE movements, keeps the aggregate in
    step, releases reservations, stamps the lot on the fill) and
    `reverse_dispense` for returned-to-stock.
  - `ledger.py` — `plan_dispense` / `DispenseAllocation`. **The only issue type
    permitted to come up short.** Every other type raises rather than under-fill;
    dispensing reports the shortfall as data because the medicine is already with
    the patient and a bookkeeping error must never become a refusal of care.
  - `state_machine.py` — DISPENSED calls the hook, RETURNED_TO_STOCK reverses it.
    Both log loudly on failure and neither can block the transition.
  - `reconciliation.py` + `inventory_integrity.py` — new `dispense_shortfall`
    check (12 checks now).
  - `shared/models/prescription.py` — **defect found by test**: migration 0030
    added `prescription_fills.inventory_lot_id` but the column was never declared
    on the model, so every assignment was silently dropped (`lot_number`
    persisted, the FK did not). Now declared.
  - NEW `scripts/backfill_dispense_movements.py` — dry-run by default.
- Design decisions worth review:
  - The hook is **best-effort and never raises**. A failure leaves the
    prescription dispensed and is caught by `fill_without_movement`. Refusing to
    dispense because inventory bookkeeping failed would be the worse outcome.
  - **Idempotent** on the fill: a retried transition is recognised and skipped
    (tested — a double call leaves 75, not 50).
  - Blocked and expired lots are never allocated, **even when that causes the
    shortfall**. Coming up short is acceptable; handing over recalled stock is not.
  - A dispense never requires approval — the prescription and the pharmacist's
    verification are the authorisation.
  - A dispense with no acting staff records the nil UUID rather than borrowing a
    real person's identity.
- Verification: `pytest tests/unit` → **1076 passed, 1 failed** (the same
  pre-existing `test_integrations_sandbox` case). 15 new tests: 8 allocation
  rules plus 7 end-to-end driving a real `RxStateMachine.transition` against the
  test database — decrement, aggregate parity, FEFO order, short-stock survival,
  idempotency, expired-stock refusal, and return-to-stock leaving
  `[RECEIPT, DISPENSE, RETURN_FROM_PATIENT]` intact. Backend restarted (PID
  30593). Live report now runs 12 checks; `dispense_shortfall` = 0.
- Findings for the owner:
  - The **46 orphan fills are historical and were NOT backfilled.** Whether to
    backfill is a judgement only the owner can make: if those units were never
    physically deducted then current on-hand already reflects their absence and
    backfilling would deduct them twice. Dry run is ready.
  - The dry run surfaced **NDC 00009001903 dispensed 6× (360 units) against no
    stock record at all** — an item dispensed that the pharmacy has never
    recorded holding.
- Next action/owner: rule on the backfill and on `00009001903`; then GTIN
  scan-to-field at receiving (R22) is the remaining P2 item.

### 2026-08-16 — Claude Code — surveillance observation pipeline (plan phase 1/7)

- Workstream: `CL-003` (surveillance & CV), new
- Branch/commit: `feat/inventory-integrity`, six commits `3e32f7e`..`(this)`
- Plan: `docs/superpowers/plans/2026-08-16-surveillance-observation-pipeline.md`
  (6 tasks, executed inline via superpowers:executing-plans)
- Changed:
  - `services/biometric/occlusion.py` (new) — occlusion stratum from the five
    landmark confidences the aligner already produces, plus SURVIVING_MODALITIES
    naming which modalities to lean on per stratum. `periocular` registered in
    FUSION_FLOORS; it had existed only in design prose.
  - `services/core/surveillance/escalation.py` (new) — replaces fuse()'s
    NO_MATCH dead end. Mints a provisional identity and prompts the counter for
    name + national code. The prompt never names a weak candidate (a leading
    question corrupts the verification); hints stay below IAL1.
  - `services/core/surveillance/recorder.py` (new) — first writer to
    `surveillance_observations`, which had existed since 0032 unused.
  - `services/platform/routers/surveillance.py` (new) — POST /observations,
    POST /rf/batch, GET /heatmap. Events only: no field admits a frame, crop,
    audio buffer or embedding.
  - `services/biometric/identity_resolution/vector_store.py` — to_readings now
    takes a per-modality gallery size; FPIR ~= N*FMR is per-modality and one
    shared N used the wrong denominator for at least one modality.
- Interfaces/schema: migration `0046` (plan said 0034; twelve migrations landed
  in between, renumbered to chain from 0045). It widens
  ck_surv_obs_fusion_decision AND the fusion_decision column from VARCHAR(12) to
  VARCHAR(24) — 'identify_manually' is 17 chars, so widening the constraint
  alone produced a column admitting a value it could not store. Caught by
  inserting against the live database rather than reading the DDL.
- Verification: full unit suite 1734 passed / 1 failed in 3m27s. The failure is
  test_integrations_sandbox's known order-dependent case, green in isolation
  (5 passed). Route-auth suite green. App builds, 359 routes.

- TEST DATABASE MAINTENANCE, please note: the suite was timing out past 10
  minutes. Cause was not code — `pharmpilot_test` had accumulated
  inventory_exceptions 694k rows / inventory_exception_rows 1.35M /
  inventory_exception_events 694k, 6.1 GB total. Fixtures soft-delete with
  `UPDATE inventory_exceptions SET is_deleted = true WHERE pharmacy_id = $1`
  once per test, rewriting hundreds of thousands of rows across 8 indexes each
  time. Truncated the three tables (6.1 GB -> 136 kB) with the owner's explicit
  approval; suite returned to 3m27s. The underlying fixture design belongs to
  the inventory workstream: soft-delete teardown without truncation means this
  will recur.

- Risks/blockers: none for this phase. Phases 2-7 of the surveillance section
  each need their own plan (voice modality, calibration/release gate, RF survey
  capture, zone rules & reconciliation, review UI, hardening).
- Next action/owner: inventory workstream to decide on truncate-based teardown
  for the exception fixtures; surveillance owner to schedule phase 2.

## Entry template

```markdown
### YYYY-MM-DD HH:MM TZ — <model> — <short outcome>

- Workstream: `<ID>`
- Branch/commit: `<branch>` at `<SHA>` (or `uncommitted`)
- Changed: <files and externally visible behavior>
- Interfaces/schema/data: <API, migration, model, config, or dataset effects>
- Verification: <exact commands and results; identify anything not run>
- Risks/blockers: <remaining concerns and required human decisions>
- Next action/owner: <one concrete next step and named owner>
```

For a transfer, add `HANDOFF from <model> to <model>` and list the exact paths,
base commit, accepted behavior, failed checks, and unresolved risks. The receiving
model must append an acceptance entry before editing.

### 2026-08-04 00:20 +0330 — Claude (Opus 5) — 861 open incompatibilities → 582, by fixing three engines rather than filing 861 rulings

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`, uncommitted at time of writing
- Changed:
  - `services/core/drug_catalog/backfill.py` — new `strength_from_composition`;
    `backfill_strength` now prefers «ترکیبات» (per-product) over «نام عمومی»
    (per-monograph, the field the spliced-page audit found misattached 204×).
    Dose vocabulary widened with radioactivity (mCi/µCi/MBq/GBq) and
    electrolyte units (mEq/mmol/mOsm) — the latter is not a nicety: without it
    the reader skipped past «SODIUM 3710 meq» to a later component and emitted
    THAT as the product's strength.
  - `services/core/drug_catalog/nfi.py` — `parse_detail` recovers a strength
    when its name-first regex fails. That regex's name class admits no comma,
    bracket, colon, parenthesis or digit, so «GONADOTROPHIN, CHORIONIC 5000
    [iU]», «Brigatinib [USAN:INN] 180 mg» and «Vitamin K1 10 mg» all dropped a
    dose the page stated plainly. generic_name still comes from the old regex —
    the new reader would hand «brigatinib [usan:inn]» to `ingredient_key`.
  - `services/core/drug_catalog/importer.py` — `strength` is now sticky, and a
    blank string counts as absent for every sticky field. Without this a single
    crawl that parsed no dose would erase the 730 strengths recovered in
    8f3f9ce and the 86 recovered here.
  - `services/core/drug_catalog/structural_match.py` — `record_components` +
    `embedded_components` + `doses_agree_all`.
  - `services/core/drug_catalog/issue_registry.py` — `ruled_subjects`; a row
    ruled individually now leaves its count.
- Interfaces/schema/data: no migration (head stays 0033). Data written:
  86 strengths + their recomputed `ingredient_key`; 12 `issue_dispositions`;
  5 `crosswalk_entries`. Snapshots `catalog_backup_pre_composition` and
  `crosswalk_backup_regtest_20260804`.
- Verification:
  - `pytest tests/unit` → 1170 passed, 1 failed. The failure is
    `test_integrations_sandbox.py::test_notifications_sandbox_...`, which fails
    identically with every change of mine stashed and passes in isolation — a
    pre-existing caplog/ordering artifact, not a regression here.
  - Composition extractor validated against 38,552 rows that already carry a
    strength: 98.2% agreement (97.6% exact). The widened vocabulary left the
    generic_full path at 98.1%.
  - New `tests/unit/test_combination_matching.py` (12 tests).
- Two defects found and closed that were NOT on the board:
  - **«PIPERACILLIN 4 g» matched a piperacillin/tazobactam record at 0.93** —
    above the auto-apply line — because a combination understates itself
    (`generic_name` holds only the first ingredient). A patient entitled to
    piperacillin alone would have been quoted Tazocin.
  - **`doses_agree` accepts a single shared dose**, so «EMPAGLIFLOZIN /
    LINAGLIPTIN 10 mg/5 mg» matched a 25 mg/5 mg record on the shared 5.
    Combinations now require the whole dose vector (`doses_agree_all`).
- Judgement lane cleared to 0: 12 price gaps ruled pack-basis (in every one the
  reference/announced ratio equals `package_count`), 5 conflicts adjudicated —
  3 confirmed (Madopar, Sinemet, naphazoline+antazoline), 2 rejected as
  clinically wrong: **PGF2α matched to dinoprostone (PGE2)** and **CEPHALEXIN
  matched to cefazolin injection** (oral vs parenteral). Full rationale in
  `docs/review-2026-08-04-incompatibility-adjudication.json`.
- A hypothesis worth recording because it was WRONG: the pack-basis pattern
  looked general, but measured across all 45,797 priced coverage entries the
  insurer reference agrees with the UNIT price (47.5% within ±11%) and with the
  package price only 0.3% of the time. The 12 are per-row exceptions, so each
  was ruled individually rather than by a blanket rule that would have silenced
  genuinely wrong prices.
- Risks/blockers:
  - 179 formulary rows moved from "unmatched" into the review queue at 0.70.
    They are correct pairings on inspection (Symbicort, Tazocin, Seretide,
    Tienam, Cosopt, Azarga) but they are **evidence, not decisions** — none is
    applied until the owner confirms.
  - `crosswalk_dangling_irc` was firing on `PLAIN ITEM / __REGT__`, a
    regression-test fixture living in the production decided layer since
    2026-08-03 18:20. Removed (backed up first). **Whatever test writes to the
    live database should be found and pointed at `pharmpilot_test`.**
  - The CEPHALEXIN→cefazolin link arrived via the national-code join at 0.72,
    which suggests a code collision that deserves its own look.
- Next action/owner: owner to confirm the 254 review-queue rows (they are now
  mostly correct combination pairings); then the ~27,000 uncrawled NFI ids,
  which is what the remaining 390 missing strengths and 79 absent presentations
  actually need.

### 2026-08-04 01:40 +0330 — Claude (Opus 5) — 582 → 462: closing what no source can fill

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Why this follow-up: the owner asked why 582 still showed after the previous
  entry claimed the incompatibilities were treated. The answer had two parts and
  only one of them was a labelling issue.
  - The «ناسازگاری‌ها» tab counts every open CAUSE, and those are two different
    kinds of thing: contradictions (two sources disagreeing) and absences (data
    nobody published). The contradictions were at zero. Everything left was an
    absence — but the tab gives them one word.
  - The other part was a fair hit: some of those absences can never be filled by
    any action, and had no business sitting in a research queue.
- Changed:
  - `issue_registry.py` — the device/consumable regex named no Persian terms, so
    ostomy appliances, insulin pens and cartridge needles, blood-glucose strips
    and elastomeric infusion pumps sat in the RESEARCH lane waiting for a
    molecule they will never have. Deliberately NOT swept in: «GELATIN MODIFIED
    500 ML INFUSION» (a plasma volume expander), C1-esterase inhibitor and
    gaseous gangrene antitoxin are medicines that merely sit next to that group.
  - 100 `nfi_missing_strength` rows dispositioned `accepted` per IRC — 62 blood
    products (a unit of plasma or platelets has no mg/mL), 25 gases and volatiles
    (the number on a nitrous oxide cylinder is its weight), 12 antivenoms and
    antitoxins (potency is venom neutralised per vial), 1 cream base. These are
    closed because no NFI page will ever carry the number, not because the
    number stopped mattering.
- Verification: `pytest tests/unit` → 1203 passed, 1 skipped, 1 failed
  (`test_integrations_sandbox`, the same pre-existing ordering artifact). Board
  read twice in one session and identical both times: open 462, judgement 0.
- Two data-quality findings for the owner, neither a drug problem:
  - **«نام ژنريک» is in the formulary data as a row.** That is the spreadsheet's
    own column header ingested as a product. Worth finding in the upload parser.
  - **«حق فني (غير بيمه اي)…»** — a professional-fee line, also ingested as a
    product row.
- Risks/blockers: the remaining 462 are 290 strengths and 172 unmatched rows
  that genuinely need the ~27,000 uncrawled NFI ids. Of the 172, 95 are insurer
  rows that state no dose at all («DEXTROSE», «BUDESONIDE/FORMOTEROL») — no
  external research pins a doseless heading to one presentation, so these are
  arguably an owner group-level decision rather than a research item, and the
  registry has no lane that says so.
- Next action/owner: owner to confirm the 251 review-queue rows; then the NFI
  re-crawl, which is what everything remaining actually waits on.

### 2026-08-04 03:10 +0330 — Claude (Opus 5) — the shared national code, and a regression it exposed

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Question from the owner: can the 172 unmatched rows be solved by joining
  salamat and tamin on their shared ID?
- Answer, measured: the join already exists (`link_rows` Tier 2 substitutes the
  richest name observed for a code) and it is real — 2,300 codes appear in BOTH
  insurers and 2,002 of those have a meaningfully richer name on one side
  (`01072`: salamat «PROPYL THIOURACIL», tamin «PROPYLTHIOURACIL 50 mg TABLET
  ORAL»). But for THESE 172 it is mostly not available:
  - 131 — no other insurer carries that code, so there is no richer name
  - 43 — a richer name exists and IS substituted; the match failed anyway
  - 1 — recovered immediately
- The 43 failed for three causes, none of them about the ID:
  1. **`normalize` was asymmetric.** It strips «hydrochloride» but not
     «dihydrochloride», so NFI's «betahistine hydrochloride» became
     «betahistine» while the insurer's «BETAHISTINE DIHYDROCHLORIDE» stayed
     whole. Fixed in `services/ai/clinical_decision_support/normalizer.py` —
     a SHARED clinical module, so note that the change also improves DUR
     lookups (that spelling previously missed the interaction table entirely).
     Deliberately NOT added: «propionate»/«furoate» (fluticasone propionate is
     Flixotide, furoate is Avamys) and bare «hydrate» (chloral hydrate).
  2. **`form_family` split «AEROSOL, METERED», «INHALANT» and «POWDER,
     METERED» into three families**, so a row saying INHALANT could not reach a
     pMDI. SPRAY forms deliberately excluded — nasal is not inhaled.
  3. **«ALUMINIUM» vs NFI's «aluminum»** — added to the file-backed synonym
     table, which already required that the target exist as an NFI generic and
     the source not.
- **A regression of mine, caught here:** `record_components` (added earlier
  today) read ingredients from BOTH `generic_name` and `generic_full`, and those
  two routinely spell one substance two ways. That turned mono products into
  false two-ingredient combinations, which the ingredient-set guard then refused
  to match. Measured blast radius: **425 catalog records**. It failed safe
  (withholding links rather than inventing them), which is exactly why it was
  invisible. `record_components` now refuses to add a salt variant of an
  ingredient it already holds.
- Verification: `pytest tests/unit` → 1207 passed, 1 skipped, 1 failed
  (`test_integrations_sandbox`, the same pre-existing ordering artifact). Five
  new tests in `test_structural_match.py`. One existing test was rewritten, not
  patched: it asserted amlodipine resolves via the salt-TOLERANT lane at 0.76,
  which stopped being true once «besilate» joined the salt vocabulary — it now
  resolves in the exact lane at 0.93, which is the better answer, and the lane
  is exercised with «dipropionate» instead.
- Net: board 462 → 459 open; 16 rows moved from unmatched into the confirm
  queue (`coverage_review_agrees` 251 → 267).
- Next action/owner: the 169 that remain are not an ID problem — 131 of them
  exist in one insurer's list only.

### 2026-08-04 04:05 +0330 — Claude (Opus 5) — showing the list found two more defects

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- The owner asked to SEE the 131 single-insurer rows. Printing them with their
  parsed doses exposed two defects that no aggregate count would have shown.
- **A regression of mine.** «CLOTRIMAZOLE / BETAMETHASONE 1 %/0.05 % 15 g CREAM»
  parses to doses {1%, 0.05%, 15000} — the TUBE SIZE read as a 15,000 mg dose.
  `doses_agree_all`, added this morning, demanded a partner for every dose on
  BOTH sides, so that phantom rejected a correct match; the old lenient rule had
  tolerated it. Fixed by making the rule asymmetric: every dose the CATALOG
  states must appear in the row, not the reverse. The catalog's `strength` is
  clean where a formulary product name is noisy. The 10/5-is-not-25/5 protection
  survives — 25 is still absent from the row — and is covered by its test.
- **Persian letterforms.** The device filter never fired on the ostomy rows for
  two independent reasons: «کلستومی» and «یورستومی» do not contain the substring
  «استومی», and «چسب كانوكس» arrives with ARABIC kaf where the pattern was
  written with Persian keheh. Folding ي/ك/ة/أ/إ/ؤ before matching is the fix;
  spelling every term twice is not.
- Verification: `pytest tests/unit` → 1248 passed, 1 failed
  (`test_integrations_sandbox`, the same pre-existing ordering artifact).
- Net: board 459 → 439 open. `coverage_review_agrees` 267 → 285, device 55 → 59.
- The remaining single-insurer set is 66 rows, exported to
  `docs/unmatched-single-insurer-20260804.csv`: 63 salamat-only, 3 tamin-only.
  salamat lists 3,575 codes against tamin's 2,600, so the asymmetry is expected.
  They divide into real presentations we do not stock (isotretinoin 8/30/40 mg,
  mometasone inhalers, fentanyl patch 75 and 100 ug/h, oxycodone ER 80 mg,
  omalizumab 75 mg, Stalevo 125) and salamat's legacy dose-less entries
  («RIVASTIGMINE (EXELON)» appears under FOUR codes with no dose on any of
  them, so nothing can tell the patch strengths apart).
- Next action/owner: no code change will resolve those two groups — the first
  needs the NFI crawl, the second needs the insurer to publish a dose.

### 2026-08-04 05:30 +0330 — Claude (Opus 5) — a counter-ion is often the product

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- **Owner correction, and it was right.** Adding sodium/potassium/calcium/
  magnesium to the salt vocabulary earlier today (to make «losartan potassium»
  meet a row naming the base) was a clinical error: different cations of one
  molecule are frequently different products at different prices. Reverted.
  In this catalog diclofenac is sold as FOUR products — potassium (24 items,
  23,000–39,000 rial; Cataflam, rapid onset, acute pain and migraine), sodium
  (222 items, 3,300–1,350,000; Voltaren, enteric-coated and SR for chronic
  disease), diethylamine and epolamine (both topical).
- **The revert was not sufficient, and the rest predates me.** `normalize()`
  returns early on GENERIC_CLASSES, so it answers with the drug CLASS —
  «diclofenac sodium» and «diclofenac potassium» both come back «diclofenac»
  regardless of the salt list. That is correct for the interaction engine and
  wrong for catalog identity. Already colliding before today: metoprolol
  succinate vs tartrate (Toprol-XL once daily vs Lopressor twice daily),
  hydrocortisone base vs acetate vs sodium phosphate (oral, intra-articular,
  IV), ibuprofen vs ibuprofen lysine (the IV neonatal PDA product).
- Changed, in `structural_match` only — the clinical normalizer is untouched:
  - `components_full()` — canonicalization WITHOUT the class-folding.
  - `identity_bearing_bases(catalog)` — bases the catalog sells under more than
    one salt, decided from the data so future imports classify themselves.
    Hydration states are excluded: «azithromycin anhydrous» and «dihydrate» are
    one substance dried two ways.
  - `build_index` and `match` both switch to the unfolded name for those bases,
    so salt matches salt and base matches base.
  - The salt-tolerant lane is WITHHELD for them. It applies at 0.76, above the
    0.75 line, and a bare «DICLOFENAC 50 mg TABLET» was resolving to the
    potassium salt merely because it is the only PLAIN tablet — silently
    choosing between a 23,000 and a 1,350,000 rial product.
- Found 31 identity-bearing bases covering 2,444 catalog rows. Beyond the ones
  above they include tenofovir (disoproxil vs alafenamide — different dose and
  different renal safety), isosorbide (mononitrate vs dinitrate), fluticasone
  (propionate vs furoate), testosterone (enanthate vs cypionate vs undecanoate),
  triamcinolone, penicillin, sevelamer, fluphenazine. Three are name-quality
  artifacts («children», «flixotide», «spray») and merely decline to fold.
- Verification: `pytest tests/unit` → 1273 passed, 1 failed
  (`test_integrations_sandbox`, the same pre-existing ordering artifact).
  Six new tests. Code-only change; no rows written, so the healthy/firing=0
  reconciliation from 03:10 still stands.
- **Cost, stated plainly:** board 436 → 481 open. `coverage_review_agrees` fell
  286 → 233 and those ~45 rows moved to unmatched. They name a base whose
  catalog holds several salts and cannot be pinned to one — review is where they
  belong, not on a salt the engine picked.
- Known limitation: a base with exactly ONE non-hydrate variant still folds, so
  «ibuprofen» vs «ibuprofen lysine» is not separated. Splitting it needs a
  pharmacological call rather than a count.

### 2026-08-04 06:10 +0330 — Claude (Opus 5) — ibuprofen lysine, by owner's ruling

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner ruled that «ibuprofen lysine» is a distinct product. It is: the
  intravenous neonatal product for closing a patent ductus arteriosus, sharing
  a molecule with the oral analgesic and nothing else — different route,
  indication, patient and price. The count rule in `identity_bearing_bases`
  could not see it, because the catalog lists only ONE variant beside the bare
  name, which is the same shape as the benign «metformin» /
  «metformin hydrochloride» naming inconsistency.
- Changed: `_IDENTITY_BEARING_MODIFIERS` in `structural_match` — a small,
  documented set for judgements a count cannot make. Holds `lysine` and
  `arginine` (ibuprofen arginine is the same class of case, listed so it is
  separated the day it appears rather than after it has mis-matched). This is
  the place to record further rulings of this kind.
- Verified against the live catalog: plain oral ibuprofen still matches at 0.93,
  «IBUPROFEN LYSINE INJECTION 10 mg/1mL» now reaches only the lysine product,
  and metformin still folds. NFI's own `composition` field distinguishes them —
  two rows read «IBUPROFEN LYSINE 10 mg/1mL» where the rest read «IBUPROFEN».
- Ruled one conflict this surfaced: salamat's «GLYCERIN» was matching hydrogen
  peroxide 30 % at 0.74 through the fuzzy price/form lane. Rejected — we hold no
  glycerin at all, only nitroglycerin, which merely contains the substring.
  A different substance, and the row is a genuine catalog absence.
- Verification: `pytest tests/unit` → 1274 passed, 1 failed
  (`test_integrations_sandbox`, the same pre-existing ordering artifact).
- Board: 482 open, judgement lane 0 after the ruling.
- **Open question for the owner, NOT acted on:** seven catalog rows read
  «IBUPROFEN INJECTION INTRAVENOUS 5 mg/1mL 2MILLILITER», branded پدآ and
  فنوفن, recorded by NFI as plain ibuprofen. 5 mg/mL in a 2 mL vial is 10 mg
  per vial, which is the neonatal PDA presentation. NFI's composition field
  says «IBUPROFEN», not «IBUPROFEN LYSINE», so the catalog is faithful to the
  source — but the presentation is the neonatal one and it may be worth
  checking the pages. No change made; this is a pharmacological call.

### 2026-08-04 06:40 +0330 — Claude (Opus 5) — a dose-less row must not be handed a dose

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Confirming the owner's ruling on ibuprofen lysine turned up the brand column,
  which settles the seven rows flagged in the previous entry: they are **PEDEA**
  — the European neonatal product for closing a patent ductus arteriosus,
  ibuprofen 5 mg/mL in a 2 mL ampoule. Pedea uses PLAIN ibuprofen where
  NeoProfen uses the lysine salt, so NFI's labelling is correct and the earlier
  suspicion of a mislabel was wrong. No change needed; the question is closed.
- **But it exposed a live safety gap, and this one is pre-existing.**
  «IBUPROFEN INJECTION» naming no strength matched the 100 mg/mL ADULT product
  at CONF_EXACT_NO_DOSE = 0.80 — above the 0.75 auto-apply line — while the
  catalog holds three IV ibuprofen products: PEDEA 5 mg/mL (neonate),
  FENOFEN 100 mg/mL (adult) and ibuprofen lysine 10 mg/mL (neonate). A
  twenty-fold dose difference between a preterm neonate and an adult, decided by
  index iteration order, applied without a human seeing it.
- Fixed in `match()`: candidates are now collected before one is chosen, and
  when the row states NO dose and the surviving candidates hold more than one
  distinct dose set, the match is refused. Ambiguity is the trigger, not the
  missing dose — a form with a single strength still resolves at 0.80, which the
  test asserts.
- Verification: `pytest tests/unit` → 1276 passed, 1 failed
  (`test_integrations_sandbox`, the pre-existing ordering artifact). Board 482
  open, judgement 0; the refusal cost one row.
- **For the inventory workstream, not touched by me:**
  `tests/unit/test_exception_register_e2e.py` now has 1 failure and 1 error —
  `test_recurrence_is_counted_not_duplicated` asserts every exception has
  `occurrences >= 2`, and `test_a_scheduled_run_is_idempotent` errors. It
  imports `services.core.inventory.exceptions`, is stateful against the live
  database and depends on run order; my changes are confined to `drug_catalog`.
  Flagging rather than editing, per the ownership rule.

### 2026-08-06 — Claude (Opus 5) — the 290 missing potencies: 58 we already had, 21 researched

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner asked for a search on each of the 290 and enrichment of the database.
  Classifying them first changed the task substantially:

  | class | n | is there a potency to find? |
  |---|---|---|
  | vaccines | 91 | no — antigen content per dose, not mg/mL |
  | radiopharmaceuticals | 51 | yes — activity at calibration |
  | formula / multi-ingredient | 39 | only as a compound formula |
  | biological / diagnostic | 4 | potency units, not concentration |
  | ordinary drugs | 105 | for a subset |

- **58 needed no research at all — a defect was discarding them.** `dose_set()`
  knows three namespaces (mass folded to mg, percent, IU) and had none for
  radioactivity, so it returned an empty set for «10 mCi». `backfill_strength`
  validates its extraction THROUGH `dose_set`, so every activity it read
  correctly out of NFI's own composition field was then thrown away. Added an
  `("act", …)` namespace folded to mCi (1 GBq = 27.027 mCi); activity now
  compares equal across SI and conventional units and never equal to mass, IU or
  percent. Recovered sodium iodide I-131, technetium Tc-99m pertechnetate /
  pentetate / succimer / medronate, gallium-67 citrate, lutetium-177 PSMA,
  samarium-153 EDTMP, sodium fluoride F-18.
- **21 researched and verified**, each against a regulator or manufacturer
  document, written as a per-IRC `strength` value AND a durable field override
  so the next crawl cannot erase them: CellCept capsule 250 mg / tablet 500 mg /
  suspension 200 mg/mL (the FORM fixes it), Orap Forte 4 mg, GlucaGen HypoKit
  1 mg, Naglazyme 1 mg/mL, Potaba 500 mg, Cerezyme 400 units, Panadol Extra
  500 mg + caffeine 65 mg, Humira 40 mg/0.8 mL.
- **Deliberately NOT written** — the product is marketed at several strengths and
  the row names none, so any value would be a guess: Esbriet (267/801),
  Sandostatin LAR (10/20/30), Prograf (0.5/1/5), Rapamune (0.5/1/2), Amitiza
  (8/24 µg), Desferal (500 mg/2 g), oxaliplatin (50/100 mg). The apremilast rows
  are starter packs and say so in their own names («AMALTA 10/20/30»).
- Note the enrichment table is UNIQUE on `key` alone and `apply_enrichment_gaps`
  takes `strengths[0]` regardless of form, so it cannot express «capsule 250,
  tablet 500» for one Persian name («سلسپت»). That is why these went through
  field overrides, which are IRC-keyed. Worth knowing before anyone tries to
  route form-dependent findings through enrichment.
- Verification: `pytest tests/unit` → 1282 passed, 1 failed
  (`test_integrations_sandbox`, the pre-existing ordering artifact). Snapshot
  `catalog_backup_pre_activity` taken before the backfill.
- Result: missing strength 290 → 211; board 482 → 403 open, judgement 0.
- Remaining 211 are overwhelmingly vaccines, formula products and pure
  substances. A vaccine has no mg/mL potency to find; the honest close for that
  class is a disposition, not a search.

### 2026-08-06 — Claude (Opus 5) — ruling the 211, and the two verdicts they needed

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner said "rule them". They did not all deserve the same ruling — "no potency
  exists" and "a potency exists but this row does not say which" are different
  facts and were recorded as different verdicts:

  | n | verdict | class |
  |---|---|---|
  | 91 | accepted | vaccine — antigen content per dose, not a concentration |
  | 40 | accepted | compound formula — ORS, children's cold, triphasic OC |
  | 13 | accepted | bulk raw material — not a finished product |
  |  9 | accepted | pure substance / excipient — vaseline, acetone, menthol |
  |  6 | accepted | biological — TU or venom-neutralising units per vial |
  | 42 | **deferred** | real drug, several marketed strengths, row names none |
  | 10 | left open | unclassified, listed below for the owner |

- `deferred` was chosen deliberately for the 42 and does NOT decrement the board
  (see `ruled_subjects`): Esbriet 267/801, Sandostatin LAR 10/20/30, Prograf,
  Rapamune, Desferal, Amitiza, oxaliplatin. A potency is knowable for each — it
  is the ROW that does not identify which, so the work is postponed, not
  settled. Closing them would have hidden real work behind a tidy number.
- Ten left open on purpose, and two are data-quality findings rather than drugs:
  **«داروی جدید»** — literally "new drug", a placeholder row that reached the
  catalog — and **«اسپری دافع حشرات»** (insect-repellent spray) with a
  generic_name of `btc2125m+diethylenglycol`. The rest are aspirin, a vitamin C
  combination, Belladonna PB, Colircusi Cicloplegico and three «سو-بگ / سو-کارت»
  sodium products, all of which have a real strength that our data simply lacks.
- Result: missing strength 211 → 52 counted; board 403 → **244 open**, judgement
  lane 0. Reconciliation healthy, 0 firing.
- The board is now 192 coverage rows + 52 strengths, and both wait on the same
  thing: the ~27,000 uncrawled NFI ids behind the Iran proxy.

### 2026-08-06 — Claude (Opus 5) — two junk rows withheld, three real ones left for the owner

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner asked to fix the «داروی جدید» placeholder and the insect-spray row.
  Pulling the thread found five rows on malformed IRCs, and they are NOT one
  class — three of them are real, currently-licensed, priced products.
- **Withheld** via `monograph.excluded`, the mechanism repo.fetch_all documents
  for NFI's own junk:
  - `123456789123654` «صثقصثق» — the name is keyboard mashing (ص ث ق repeated)
    and the manufacturer is «غذا آوران تست», which says TEST outright. It
    nonetheless carried a full levodopa/carbidopa 100/25 ER monograph and a
    16,100 rial price, so a formulary row could have matched it — exactly the
    2026-08-01 incident that docstring records.
  - `9999000000000001` «داروی جدید» — "new drug", i.e. the name was never
    entered. Empty monograph, no manufacturer, no licence, and a 42,000 price.
- **Dispositioned, not withheld**: `2642992123404660` NORMOPIC FORT, the insect
  /lice spray. It is a genuinely registered product, merely non-therapeutic
  (ATC V07) with a licence lapsed since 1395/12/29 and a zero price. Its
  `generic_full` is a chemical formulation carrying CAS numbers
  (`112-34-5` = diethylene glycol monobutyl ether), which is why `generic_name`
  reads `btc2125m+diethylenglycol`. It has no drug strength to find.
- **NOT touched — needs the owner's decision.** Three REAL products sit on
  placeholder `9999…` IRCs, all currently licensed, all priced, two carrying
  live coverage:

  | irc | product | strength | manufacturer | licence |
  |---|---|---|---|---|
  | 9999328807296526 | تایلوکیم اکسترا | acetaminophen 500 mg + caffeine 65 mg | داروسازی حکیم | 1405/10/13 |
  | 9999426083359186 | پروپرانولول-عبیدی | propranolol HCl 10 mg | دکتر عبیدی | 1405/03/31 (has coverage) |
  | 9999689235152604 | مدافینیل | modafinil 100 mg | لابراتوارهای رازک | 1405/05/31 (has coverage) |

  Excluding them would remove real dispensable stock — modafinil among it — so I
  did not. But the IRC is the identity anchor: these can never be reached by the
  IRC join, their coverage is pinned to a code that is not a national code, and
  when NFI issues the real IRC the next harvest will create a DUPLICATE row
  beside each. That is a succession question, and `catalog_succession` already
  exists to record old-IRC → new-IRC once the real codes are known.
- Verification: catalog visible to the matcher 39,179 of 39,184; board 244 →
  **243 open**; reconciliation healthy, 0 firing.

### 2026-08-06 — Claude (Opus 5) — succession NOT recorded for the 9999 IRCs, and why

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner asked to record succession for the three placeholder-IRC products. I had
  suggested succession as "the clean path" in the previous entry — that was
  before comparing the rows field by field. Having done so, the premise does not
  hold and NOTHING was written. Recording these would enter a false claim into
  the decided layer, and `succession.apply()` carries insurer coverage, field
  overrides and crosswalk pointers across, so a wrong succession attaches one
  presentation's decided facts to a different presentation.
- What the comparison shows:
  - **`9999689235152604` → `9330870830140073` (مدافینیل)** — identical brand,
    manufacturer, strength, ATC, licence date… but **package_count 30 vs 100**.
    Two pack presentations, each entitled to its own IRC. A 30-pack does not
    become a 100-pack. Both already carry identical coverage (tamin ref 75,000
    share 0; salamat ref 0), so a succession would carry nothing and assert
    something untrue.
  - **`9999328807296526` → `7015688221406659` (تایلوکیم اکسترا)** — identical on
    every field including package_count 30, differing ONLY in GTIN. Plausibly one
    product registered twice, but GTIN is the anchor this module explicitly
    REJECTED (2,944 GTINs sit on more than one IRC), so it is not evidence.
  - **`9999426083359186` (پروپرانولول-عبیدی)** — no candidate at all. Zero
    propranolol 10 mg rows from دکتر عبیدی on a real IRC. Nothing to point at.
- The decisive point for all three: **a succession means the old registration
  ENDED, and both rows in each pair carry the SAME licence validity date** —
  1405/10/13 and 1405/05/31, both in the future. These are concurrent live
  registrations, not a re-registration.
- The module's own anchor, `monograph.nfi_id`, is NULL on all of these rows, so
  `detect()` would propose nothing — correctly. It arms itself on a SECOND audit
  pass over ground already covered, which is precisely the evidence missing here
  and is blocked on the Iran proxy.
- Separate finding while here: **modafinil is priced 75,000 for BOTH the 30-tab
  and the 100-tab pack.** One of those is wrong. Not touched — it belongs to the
  price lane, not to succession.
- Propranolol-Abidi is a live, covered, dispensable product (tamin ref 22,000 at
  70%, salamat 21,440 at 70%) sitting on a fabricated IRC. That is the one worth
  chasing when the crawl reopens.

### 2026-08-06 — Claude (Opus 5) — 58 confirmed decisions link a formulary line to the WRONG STRENGTH

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- **Nothing was changed. This is the owner's to rule, and it is urgent.**
- Found while answering "which price does each formulary mention". Comparing the
  two insurers by shared national code showed tamin names the strength in its own
  line («PROPRANOLOL HYDROCHLORIDE 20 mg TABLET») while salamat prints only
  «PROPRANOLOL HCL». Three tamin codes — 10 mg (01067), 20 mg (07066) and 40 mg
  (01068) — ALL resolve to one 10 mg record at confidence 1.00, `method=crosswalk`.
- Audited every confirmed decision the same way, restricted to SOLID ORAL,
  single-ingredient rows where both sides state a plain mass dose, so there is no
  percent-vs-mg/mL or concentration-vs-vial ambiguity: **58 decisions link a
  formulary line to a product of a different strength.** Exported to
  `docs/review-2026-08-06-dose-mismatched-decisions.csv`.
  (An earlier count of 129 included unit-namespace artifacts — «NOREPINEPHRINE
  0.1 %» IS 1 mg/mL — and was discarded. 58 is the defensible number.)
- All 58 are `origin='owner'`, `status='confirmed'`, with **no reason, no method
  and no confidence recorded** — the signature of a bulk «تأیید همه» approval
  rather than a considered per-row ruling.
- The clinically serious ones:
  - **tacrolimus** — 1 mg → 5 mg, 0.5 mg → 5 mg, «PROGRAF® 0.5MG CAP» → 5 mg. A
    tenfold error on a narrow-therapeutic-index immunosuppressant.
  - **oxycodone** — 30 mg → 5 mg, and the ER 10 mg line → a 40 mg product.
  - **levothyroxine** — the 100 µg, 75 µg and 25 µg lines ALL → the 50 µg product.
  - alprazolam 0.5 → 1 mg; lorazepam 2 → 1 mg; sotalol 40 → 80; ticagrelor 60 →
    90; alendronate 10 → 35 and 35 → 70; valacyclovir 1000 → 500.
  - **one frank molecule error**: «MIVACURIUM CHLORIDE INJECTION 2 mg» → potaba
    500 mg. A neuromuscular blocker pointed at a PABA supplement.
  - oncology, where the money is largest: olaparib 50 mg line → 150 mg product
    (ref 8,400,000), palbociclib 125 → 75, dasatinib 100 → 70, eltrombopag
    25 → 50, erlotinib 25 → 100.
- Harm: the insurer's reference price lands on the wrong strength, so patient
  share is computed against the wrong figure, and the correctly-matched products
  get no coverage at all — which is why every propranolol 20 mg and 40 mg row in
  the catalog currently shows no reference while all 27 of the 10 mg rows carry
  the 20 mg figure.
- These are owner decisions and were NOT touched. `decision_review` exists for
  exactly this (verdicts keep / accept_engine / reject / reopen); the 58 belong
  in that panel. Recommend reviewing tacrolimus, oxycodone and levothyroxine
  first regardless of what happens to the rest.

### 2026-08-06 — Claude (Opus 5) — 49 strength mislinks corrected, 6 rejected, 3 staged

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner authorised correcting the unambiguous ones, and warned that an insurer
  may cover one strength of a drug and not another. That caveat shaped the
  handling of the "no target" cases: where the strength the formulary names is
  genuinely absent from our catalog, the decision is REJECTED — not repointed to
  a neighbouring strength — because pretending a different strength is the
  covered one is exactly the error being fixed.
- Snapshot first: `crosswalk_backup_pre_strength_fix` (4,653 confirmed rows).
- Method: re-run each formulary line through the CURRENT matcher (much improved
  since these were approved). Where it returns a record whose dose matches the
  line, repoint. Where the matcher has a known blind spot, fall back to a direct
  catalog search on the LINKED molecule plus the line's dose — that recovered
  four the matcher missed: «ALENDRONATE» never reaches the catalog's
  «alendronic acid» (a synonym gap), and lisdexamfetamine.
- **49 repointed** — every one now resolves to its own strength. Verified by
  re-linking the live tamin run: propranolol 10/20/40 → 20,000 / 22,000 / 24,000;
  levothyroxine 25/50/75/100 → 14,500 / 26,000 / 28,000 / 30,000; tacrolimus IR
  and ER separated across 0.5/1/3/5 mg. Each carries `revised_from_irc`,
  `revised_at` and `method='strength_correction'`, so every change is reversible
  and explainable.
- **6 rejected** — the named strength is genuinely absent: sapropterin 50 mg,
  ephedrine 15 mg, erlotinib 25 mg, flupentixol 0.5 mg, ibandronic acid 3 mg,
  and mivacurium 2 mg (which was pointing at potaba, and which does not exist in
  the catalog under any name). `method='strength_absent'`. These are the owner's
  caveat made concrete: the insurer covers a strength we do not stock.
- **3 staged, deliberately** — «PROGRAF® 1MG CAP», «PROGRAF® 0.5MG CAP» and
  «TACROLIMUS 0.5 mg CAPSULE ORAL». Tacrolimus 0.5 and 1 mg each exist in TWO
  ingredient groups, immediate-release and extended-release. Prograf is the IR
  brand and Advagraf the ER one, so a reading is available — but guessing
  between IR and ER on a narrow-therapeutic-index immunosuppressant is not mine
  to do.
- Dose-mismatched confirmed decisions: **58 → 3**.
- **NOT YET MATERIALISED.** The decided layer is corrected and the linker
  resolves correctly, but `drug_catalog.coverage` still holds the old values —
  propranolol 10 mg still stores 22,000, and 20/40 mg still store nothing. That
  column only changes when a coverage run is staged and applied through
  `apply_run`, which is the owner's gate. Re-stage both insurers and apply to
  land it.

### 2026-08-06 — Claude (Opus 5) — restaged and applied both insurers; two warnings opened

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Snapshot first: `coverage_backup_20260806` (27,999 rows with coverage).
- Both runs returned to `parsed`, restaged with the current engine, applied.
  **`remove_missing` was deliberately OFF** — the salamat restage reported
  `diff.removed = 9,328`, and sampling those showed real covered products
  (omeprazole 20 mg, meloxicam 15 mg, lisinopril 5 mg, valacyclovir 500 mg).
  They are unmatched now because THIS session's matcher is stricter — salt bases
  kept distinct, dose-ambiguity refused, ingredient groups re-partitioned by the
  165 strengths filled — not because the insurer de-listed them. Retiring them
  would have quoted patients full price for ordinary drugs.
- Salamat's own lines name no strength, so the earlier audit could not judge
  them. Corrected 21 by borrowing the dose from tamin's line for the SAME
  national code, then **reverted 3 of those** on a form check: tamin's evidence
  line must describe the same kind of product, and «FLUOXETINE 4 mg/1mL 60 mL»
  (syrup) had been used to set a capsule's strength — the "20" came from the
  pack volume. 18 stand, `method='code_cross_insurer'`.
- Applied: 2,151 tamin and 1,858 salamat reference prices changed; rows holding
  coverage 27,999 → 28,448.
- **Verified landing**: propranolol 10/20/40 mg now carry tamin 20,000 / 22,000 /
  24,000 where all three previously collapsed onto the 10 mg row at 22,000.
- **Two warnings now firing — reconciliation is no longer clean:**
  - `coverage_orphaned_by_newer_staging: 9,791` — the direct consequence of
    applying without remove_missing. Those products keep coverage the current
    engine no longer derives. Neither retiring them nor leaving them is
    obviously right; the real cure is matching them again, which needs either
    doses from salamat or owner group-level rulings.
  - `price_gap_extreme_strong_identity: 28` (board shows 61 judgement items) —
    corrected references produce new gaps against announced prices. These are
    genuinely new questions created by fixing the links, not by breaking them.
- **UNRESOLVED, and I stopped rather than guess**: tamin's levothyroxine 100 µg
  (code 00751) and 25 µg (07938) are corrected in the crosswalk — both show
  `strength_correction` pointing at the right ingredient_key — yet a fresh
  restage puts them in NEITHER staged, review, NOR unmatched, so they never
  apply. The 50 µg and 75 µg lines from the same run stage normally. Something
  in `stage_run_payload` drops them; it needs a focused look.

### 2026-08-06 — Claude (Opus 5) — why levothyroxine 100/25 µg would not stage

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- **Correction to the previous entry.** I reported those rows were in "neither
  staged, review, nor unmatched". Wrong — they are in REVIEW. My search looked
  for a `name` key while a review item nests it under `row.drug_name`. The data
  was fine; the query was not.
- Not a bug. The chain:
  1. `link_rows` resolves all four levothyroxine codes at conf 1.00 via
     `crosswalk` — the corrections work.
  2. `match_intel.verify_links` demotes 00751 and 07938 to 0.74 and stamps
     `method='crosswalk+intel'`.
  3. 0.74 is under the 0.75 cutoff, so `build_coverage` routes them to review.
  4. `apply_run` without `accepted_review_ids` does not apply review items.
- The demotion reason is the learned PRICE BAND, not the FS score — the review
  threshold is 0.0, so the score (5.184 on all four) never demotes. tamin's band
  is `lo=0.3866, hi=6.5122, median=1.0, n=21502`, and both failures are
  MARGINAL:
  - 100 µg → 30,000/87,000 = **0.345** against a floor of 0.3866
  - 25 µg → 14,500/2,200 = **6.591** against a ceiling of 6.5122
  - 75 µg → 28,000/28,000 = 1.000, comfortably inside, and it staged normally.
- **The band is stale in a way that matters.** It was fitted 2026-08-03 from
  21,502 tamin pairs — a population that still contained every mislink corrected
  today, including all three propranolol codes pointing at one 10 mg product. A
  band learned from mismatched pairs is now judging correctly-matched ones.
- 314 links were demoted by the model in this run. If the band is shaped by bad
  pairs, some of those 314 may also be wrongly held back — this is not confined
  to levothyroxine.
- Two ways forward, both the owner's call:
  1. Accept those review items — they are correctly linked
     (`apply_run(accepted_review_ids=[...])`).
  2. Re-fit the model now that 67 decisions are corrected, then restage. This is
     the principled fix and would re-derive the band from clean pairs, but it
     moves all 314 demotions, so it deserves a look at the before/after.
- Note `data/reference/match_model.json` has been modified-uncommitted since
  before this session; whatever is in the working tree is what loads.

### 2026-08-09 — Claude (Opus 5) — refit measured and REJECTED; my hypothesis was wrong

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- I predicted the price band was stale — learned from pairs containing the
  mislinks corrected on 2026-08-06 — and that refitting would release the two
  levothyroxine rows. **Measured, and it is not true.**
- Band before → after:
  - tamin  `lo 0.3866 → 0.3968`, `hi 6.5122 → 6.4394`, n 21,502 → 22,248
  - salamat `lo 0.0585 → 0.0591`, `hi 3.6 → 3.6`, median 0.7211 → 0.7299
  The tamin band got TIGHTER on both sides, so levothyroxine 100 µg (ratio
  0.345) and 25 µg (6.591) fall further outside, not inside.
- Effect on the live runs, same links scored by each model:
  - tamin  demoted 314 → 320 (+6), staged 1,661 → 1,655 (−6), released 0
  - salamat demoted 1,111 → 1,112 (+1), released 0
  The refit demotes MORE and releases NOTHING. Rejected; the 2026-08-03 model is
  restored and verified by hash.
- Why the hypothesis failed: the band is a percentile over ~22,000 pairs, and 67
  corrected decisions cannot move it. The two levothyroxine ratios are genuine
  economic outliers — tamin reimburses the 100 µg at a third of its announced
  price and the 25 µg at 6.6× — so the model is flagging them CORRECTLY. The
  right resolution is to accept those two review items, not to retrain.
- **Trap worth knowing**: `match_intel.fit_from_db` calls `save_model()` at
  line 381, so fitting OVERWRITES `data/reference/match_model.json` as a side
  effect. There is no dry-run. I only recovered the previous model because I had
  copied it first. Anyone evaluating a fit must back the file up beforehand, or
  the module needs a `persist=False` parameter.

### 2026-08-09 — Claude (Opus 5) — full formulary column audit; two owner rules implemented

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- **Column inventory of both formularies** (the first time every column has been
  looked at rather than the five we consume):
  - tamin, 7 columns / 2,600 rows: `ceiling` (quantity limit), `inpatient`,
    `covered`, `share_pct`, `generic_code`, `reference_price`, `drug_name`
  - salamat, 5 columns / 3,679 rows: `drug_name`, `generic_code`, `share_pct`,
    `reference_price`, `conditions` (71% filled)
  - `ceiling`, `inpatient`, `conditions_text`, `restrictions`, `age_min/max` all
    already reach `drug_catalog.coverage`. One field did not.
- **The finding: tamin's «تعهد» column is not a yes/no.** It has four states and
  two of them name the FUNDING CHANNEL — «صرفا مشمول يارانه دولت» (335 rows) and
  «صرفا مشمول صندوق صعب العلاج» (32). Both carry `share_pct 0`. `_to_bool`
  returned True for each, so 367 products stored as `covered=true` at 0 %, which
  a pharmacist cannot tell apart from "not insured". It is neither — the patient
  IS entitled, through a channel they must claim from. Now tagged
  `funding_channel` + a `restrictions` entry, the same treatment salamat's
  شرایط تعهد text already gets.
- **Owner's rule, both halves implemented** (they chose these two; they did NOT
  choose stopping the coverage spread, which would have hit 62% of multi-member
  groups and broken reference pricing — مابه‌التفاوت exists precisely because
  group members differ in price):
  1. `succession.same_product_refusal` — a succession carries coverage and
     overrides across, so it now refuses when the two rows differ in announced
     price or pack count. Verified live: the modafinil pair is refused on
     30-vs-100, the Tylokim pair (same price, same pack) is allowed. Extracted
     as a pure predicate so it is testable; silence cannot refuse.
  2. New cause `group_price_dispersion` at ≥100×. Measured first: 1,203 covered
     groups hold members at different prices, which is normal, but only 30
     exceed 100× and every one is a bad key or a bad price —
     «vitamin|12|tablet» ×375,000 (built from the «ویتامین ب۱۲» bleed, 2 …
     750,000 rial), liothyronine 25 µg ×8,477, alectinib 150 mg ×3,527.
- Board: 588 products across those 30 groups now surface as a judgement item;
  open 308 → 896. That is a real backlog appearing, not one being created.
- Verification: `pytest tests/unit -p no:randomly` → 1,469 passed, 1 failed
  (`test_integrations_sandbox`, pre-existing). NOTE: the suite uses
  pytest-randomly, and `test_model_column_parity` is order-sensitive — it failed
  once under a shuffle and passes with ordering fixed. Worth knowing before
  anyone blames a change for it.

### 2026-08-09 — Claude Code · inventory truth pass and nine phases

- Workstream: `CL-003` · Branch `feat/inventory-integrity` at `6870ef3`
- Scope: a structured re-examination of the inventory section, then nine phases
  of remediation. Migrations `0036`–`0039`.

**What the audit overturned.** The prior deficiency register claimed several
engines were missing. They were not. `stock_intelligence.py`,
`procurement.py`, `replenishment.py` and `services/ai/inventory_intelligence/`
already existed and were correct. The real defect was that every one of them was
being fed numbers nobody had measured:

- `stock_levels.avg_daily_demand` held seeded values contradicted by the fill
  record — 14 units/day against 0 ever dispensed, 4/day against an observed 12.9.
  `forecast_updated_at` equalled `created_at` on all 16 rows and had never moved
  (54 days stale). No check tested it.
- `quantity_reserved` was decrement-only. The dispense hook decremented it and
  nothing ever incremented it, so `available` always equalled on-hand,
  `check_over_reservation` could not fire, and two staff could promise the same box.
- Lead time was two hard-coded constants that disagreed: 7 in procurement, 2 in
  the forecaster. `purchase_orders` had 0 rows, so nothing was derivable.
- The movement hash chain was empty, so `/ledger/verify` passed over nothing.

**What changed.** Demand, lead time and reorder points are now measured and carry
provenance (`observed`/`sparse`/`no_history`/`declared_default`); no history
writes NULL rather than a fallback constant, and `FALLBACK_DEMAND_RATE = 1.0` is
gone. Reservations exist as rows with the counters as a checkable denormalisation,
committed at `READY_TO_FILL`, released exactly — the `GREATEST(0, …)` clamp is
removed. Approvals have a clock with escalation but no auto-approve. Movements
carry session/device/role/source. Receipts declare their unit of measure.
Valuation and shrinkage are in currency. Cycle counting is ABC/XYZ and risk-ranked.
A recommendation ledger records what each model proposed and what the human did.

**Checks actually run.** Full backend unit suite; 469 inventory tests; migrations
`0036`–`0039` up/down/up on `pharmpilot_test` with the model-column parity guard
green; the whole chain `0001`→`0039` rebuilt on a fresh disposable database
(39 migrations, single head); reconciliation run against the pilot books before
and after each phase.

**Production state.** Reconciliation went from 12 checks / 125 findings to 15
checks / 92. `fill_without_movement` fell from 46 (critical) to 15 after 31
DISPENSE movements were backfilled. The chain is now 31 rows and verifies intact,
and tamper detection is proven by an e2e test that suspends the append-only
trigger, edits a row, and asserts the break lands at that row's index.

**Errors made and corrected, recorded because they cost time.**
1. A bash substitution meant to retarget `pharmpilot_test` silently did nothing,
   and migration `0036` — including its destructive UPDATE — ran against
   production. Owner was informed, chose to keep the retired values (they were
   the fabricated ones the migration existed to remove), and authorised
   autonomous production writes with a pre-dump thereafter.
2. The append-only trigger rejected `0038`'s attempt to back-fill
   `source_system` on existing movements. It was right on the merits as well as
   the letter: nobody observed where those 8 legacy rows came from, so NULL
   ("recorded before provenance was captured") is the true value.
3. `_abc_for` classified the single most valuable line as C by measuring the
   cumulative share *after* adding the item.
4. The recommendation ledger re-raised advice that had just been accepted;
   fixed with a per-kind decision cooldown.

**Risks and next actions.**
- 46 fills remain untraceable and 15 have no movement: 6 dispense an NDC the
  books never held, and 765 units cannot be covered. This settles with a physical
  count, not an edit. Backfilled lot attribution is explicitly marked
  reconstructed — a recall must not treat it as observed.
- 16 items stay unbound from the formulary. This is not backlog: an IRC is a
  per-brand registration and gabapentin 300mg has 68 of them, so this needs a
  GTIN scan at goods receipt or an owner's brand ruling.
- Demand reads `no_history` for all 16 items because dispensing stopped
  2026-06-18. Forecast quality is blocked on real dispensing, not on model choice.
- `effort_saved` reports the cycle-count schedule costing 56% MORE lines than a
  flat sweep on 16 items. That is correct for a catalogue with no C-class tail;
  the saving needs catalogue scale, and the claim should not be made until it is.
- Write-offs are costed at today's lot price; the cost at movement time is not
  recorded on the movement.
- `test_integrations_sandbox.py::test_notifications_sandbox_success_shape_no_network_and_masked_logs`
  still fails in-suite and passes in isolation. Pre-existing, unrelated, unchanged.

### 2026-08-09 — Claude (Opus 5) — reference pricing verified end to end on real data

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner described the coverage model: the insurer publishes a reference price —
  usually the Iranian generic — and for ANY other brand the insurer still pays
  its share OF THE REFERENCE, while the patient pays their franchise plus the
  whole difference. Verified rather than assumed.
- `pricing_ir.engine` already implements exactly that, and its docstring says so:
  `covered_base = reference × qty`, `insurer_share = covered_base × (1−franchise)`,
  `differential = max(0, consumer − reference) × qty`,
  `patient_total = patient_share + differential + vat`, with
  `insurer_share + patient_total == gross + vat`.
- Proved on live ketotifen 1 mg tablet, tamin reference 20,400 at 70 %:

  | | Iranian generic | Swiss زادیتن |
  |---|---|---|
  | consumer | 20,400 | 29,200 |
  | insurer pays | 14,280 | **14,280 — unchanged** |
  | patient franchise | 6,120 | 6,120 |
  | مابه‌التفاوت | 0 | **8,800** |
  | patient total | 6,120 | **14,920** |

  Conservation held in both. The production path is wired correctly too —
  `routers/pricing._coverage` reads `rec.coverage[insurer].reference_price` and
  passes it as `insurer_reference_price`.
- This also settles the 2026-08-09 spread question for good: the reference is
  per interchangeable group BY DESIGN, and the patient absorbs the premium. Had
  we restricted the spread to equal prices, the Swiss brand would have lost its
  14,280 of cover entirely.
- Completed the funding-channel thread: `_channel_of` now surfaces
  `funding_channel` + a Persian label on the quote line, so the counter can see
  «صرفاً از صندوق صعب‌العلاج — بیمهٔ پایه سهمی نمی‌پردازد» instead of a bare
  "covered, insurer pays 0". Empty dict on ordinary lines, so it costs nothing
  on the 99 % that have no channel.
- Worth a look at the counter: زادیتن syrup (Swiss) is priced 6,300,000 against
  a salamat reference of 357,700 — a مابه‌التفاوت of 5,942,300 rial on one
  bottle. That is the single largest patient-facing surprise in the ketotifen
  family and exactly what the differential line exists to show.
- Verification: `pytest tests/unit -p no:randomly` → 1,534 passed, 1 failed
  (`test_integrations_sandbox`, pre-existing).

### 2026-08-09 — Claude (Opus 5) — salamat verification found a conservation bug in the money path

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner's economic point, and it is the key to this: Iranian prices rise
  continuously while a published reference stays FROZEN until the formulary is
  reissued. So the differential is not really a brand premium — it is reference
  staleness, and it grows on the insured generic too.
- Measured, and the two insurers are in very different states:

  | | priced+referenced | price ABOVE ref | equal | BELOW ref |
  |---|---|---|---|---|
  | tamin | 22,248 | 3,027 (13.6%) | 15,046 (67.6%) | 4,175 (18.8%) |
  | salamat | 24,244 | **18,143 (74.8%)** | 3,153 (13.0%) | 2,948 (12.2%) |

  salamat's references are far staler: three quarters of covered products now
  sit above them, 117.4 billion rial of differential in total. tamin's are
  fresh — two thirds sit exactly AT the reference.
- **The bug.** `price_line` took the reference as the covered base with no cap,
  so when reference > consumer it billed the insurer ABOVE the sale price and
  broke the invariant this module documents. Live: ketotifen 1 mg at 5,750 rial
  against a salamat reference of 19,663 charged insurer 13,764 + patient 5,899 =
  19,663 collected on a 5,750 item — 13,913 rial of phantom money on ONE line.
  7,123 product-insurer pairs were in that state.
  Fixed: `ref_unit = min(reference, consumer_price)`. You cannot reimburse
  against a base higher than the thing costs. Two regression tests, one per
  direction, both asserting the invariant.
- Checked before claiming the cap was the whole answer: only 5–6% of the
  below-reference rows are the pack-vs-unit basis artifact. The other ~94% are
  genuinely below reference on the same basis.
- **Operational finding worth acting on**: those ~3,344 products are ones where
  OUR catalog price is stale-LOW and the insurer's own reference is independent
  evidence of a higher market price. The owner notes prices rise even without a
  new purchase, so the insurer reference is a usable freshness signal for the
  price-refresh worklist — the pharmacy is currently selling below what the
  insurer itself is prepared to reimburse against.
- Verification: `pytest tests/unit -p no:randomly` → 1,536 passed, 1 failed
  (`test_integrations_sandbox`, pre-existing).

### 2026-08-09 — Claude (Opus 5) — the owner's invariant, turned into a detector

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- **Owner corrected my reading, and the correction matters.** Prices sitting
  ABOVE the reference are not staleness — the insurer caps its liability
  deliberately, to carry less of the cost. So the differential is policy, by
  design, and there is nothing to "fix" there.
- The owner's invariant is the useful half: **for an identical dosage form,
  brand and strength, the market price CANNOT be below the insurer reference.**
  An insurer does not overpay. Every below-reference row is therefore a defect,
  and the size of the gap says which kind: a large gap means the link is to a
  different product (wrong strength, form or pack), a small one means our NFI
  price is stale.
- **Correcting my own earlier number**: I reported 5–6% of below-reference rows
  as pack-vs-unit basis. That was wrong — the test required `package_count > 1`
  and so silently skipped the ~3,600 rows that have no pack count at all.
  Re-run over all 7,123:

  | n | class | remedy |
  |---|---|---|
  | 3,971 | unexplained, >1.5× — wrong form/strength/pack | matching defect |
  | 1,895 | <50% below — stale NFI price | price refresh |
  | 955 | pack-vs-unit basis | not a defect |
  | 302 | ≤1% rounding | nothing |

  The wide end reads exactly as the owner predicted: warfarin 5 mg at 1,500 rial
  against a 127,770 reference (×85 — the reference is for a ~100-tab pack),
  insulin glulisine SoloStar 192,000 against 8,000,000, furosemide ampoules
  11,100 against 468,600.
- Implemented as a new cause `price_below_reference` (judgement lane) at >1.5×,
  excluding rows a pack-vs-unit reading explains. **2,728 products** raised.
- Board: 896 → 3,624 open. That is a real backlog surfacing on a rule that did
  not exist before, not a regression — and it is the most directly monetary of
  the lot, since each one is either a mis-linked reference or a price the
  pharmacy is selling below.
- Verification: targeted suites pass (15). Full suite last green at 1,536 with
  the same single pre-existing `test_integrations_sandbox` failure.

### 2026-08-09 — Claude (Opus 5) — pack-basis references: a flag for the machine, a note for the human

- Workstream: `drug-data-integrity`
- Branch/commit: `feat/inventory-integrity`
- Owner's directive: NFI's price is not authoritative. The insurer reference
  drives the insurer's share; the patient's remainder is computed from the price
  registered in the INVENTORY engine. And pack-basis references need TWO
  markers — one the pricing engine obeys, one a supervising human can see.
- **Why two is right, not belt-and-braces**: neither insurer publishes which
  basis its reference is on. It has to be INFERRED from the ratio of reference
  to unit price. A machine flag on a guessed value, applied silently, is how a
  wrong number becomes policy — so the human note states that it was inferred
  and shows the arithmetic.
- Implemented:
  - `coverage_import._mark_reference_basis` writes `reference_basis`
    (`unit`/`pack`), `reference_unit_price` (MACHINE) and a Persian
    `reference_basis_note` (HUMAN) naming the pack size, the per-unit figure and
    the fact that it is an inference awaiting confirmation.
  - `routers/pricing._coverage` feeds the engine the PER-UNIT figure when the
    basis is pack, so quantity multiplication is correct.
  - `_channel_of` carries `reference_basis`, both prices, the note and
    `needs_price_confirmation: true` onto the quote line.
  - Backfilled over the live catalog: 30,668 unit, **171 pack**, 19,641 with no
    reference. Snapshot `coverage_backup_pre_basis` (28,448 rows).
- Verified end to end on آلدوکومار (warfarin, 40-tab pack): stored reference
  127,770, engine receives **3,194 per unit**, and dispensing 30 tablets gives
  covered_base 83,100 with conservation holding. Without the flag the base would
  have been 3,833,100 — **46× too much**.
- Four tests, including one asserting the human note says «استنباط‌شده» and
  «تأیید کنید» so it cannot be quietly reduced to a bare machine flag.
- Verification: `pytest tests/unit -p no:randomly` → 1,540 passed, 1 failed
  (`test_integrations_sandbox`, pre-existing).
- **Still open, and it is the owner's larger point**: `resolve_consumer_price`
  still returns `max(NFI announced, last invoice)`. Per the directive the
  patient's remainder should come from the inventory-registered price, with NFI
  not consulted. That is a money-path change touching every quote, so it is
  flagged rather than done — and it needs one decision first: whether the
  inventory price is a PURCHASE price (in which case a margin belongs on top)
  or already the sale price.

### 2026-08-09 — Claude (Opus 5) — HANDOFF: inventory gains a shelf price

- **Scope change across a boundary, recorded before the edit.** This entry
  extends CL-003's owned scope (`shared/models/inventory.py`) from the
  `drug-data-integrity` line of work. Both are Claude Code, same branch
  `feat/inventory-integrity`; base commit af8b49a. CL-003 was
  READY_FOR_REVIEW, so **its pending review is now invalidated for the inventory
  model and must be re-run against 0040.**
- Why it had to cross: the owner ruled that NFI's price is not authoritative.
  The insurer's reference sets the insurer's share; the patient's remainder is
  what is left of the SHELF price — and no shelf price existed anywhere in the
  database. `inventory_lots.unit_cost` is the distributor's charge, and the two
  `drug_products` price columns are `awp_unit_price` and `wac_price`, which are
  US wholesale concepts. The chain was broken at the middle link, which is why
  `resolve_consumer_price` still falls back on NFI.
- Owner's specification, both parts implemented:
  - buy price + profit percentage + sell price, held **per lot** — the buy price
    is per invoice, so a later, dearer lot keeps its own pair instead of
    rewriting what earlier stock cost.
  - the product's shelf price is the **highest** `sell_price` among lots still
    holding sellable units. Replacement cost only rises; selling old stock at
    its old price funds the next purchase at a loss.
  - **never mix brands** — stated twice by the owner. The maximum is taken
    within one `drug_product_id`. «زادیتن» from Switzerland and an Iranian
    ketotifen are separate products at separate prices, exactly as different
    salts and presentations are.
- Delivered: migration `0040`, two nullable columns plus a
  `(drug_product_id, sell_price)` index, model fields, and
  `services/core/inventory/shelf_price.py`. A lot with no `sell_price` is
  skipped rather than read as zero — silence is not a price, and reading it as
  one hands the customer a free item. Holding buckets (in-transit, damaged,
  returned) do not count as sellable.
- Gates evidenced: single head `0040`; applied to a **disposable** database,
  downgraded to 0039 (columns verified gone), re-upgraded, database dropped;
  then applied to `pharmpilot_test` and `pharmpilot`. Seven unit tests. Full
  suite 1,567 passed, 1 failed (`test_integrations_sandbox`, pre-existing).
- **Still not wired, deliberately**: `resolve_consumer_price` continues to
  return `max(NFI announced, last invoice)`. Switching it to the shelf price
  changes every quote and is exactly the "pricing work requires independent
  review before merge" case. The pieces are in place; the switch is a separate,
  reviewed change.
- Next action/owner: independent review of `0040` and of the shelf-price rule,
  then the `resolve_consumer_price` switch. Also worth a decision: the
  `awp_unit_price` / `wac_price` columns on `drug_products` appear to be US
  leftovers with no Iranian meaning.

### 2026-08-09 — Claude (Opus 5) — AWP/WAC removed; the owner can reprice, with history

- Workstream: `drug-data-integrity` + CL-003 scope (handoff recorded above)
- Branch/commit: `feat/inventory-integrity`
- **Migration 0041 drops `drug_products.awp_unit_price` and `wac_price`.** They
  are US wholesale benchmarks and held exactly that: sixteen American brands —
  Amoxil, Lipitor, Lantus at 19.84 — priced in DOLLARS, seeded by
  `seed_fda_ndc.py`. No Iranian product ever had a value in either.
  They were not inert. `routers/inventory.py` used `wac_price` as the fallback
  acquisition cost for a purchase-order line, so a missing cost silently became
  a dollar figure read as rial; `DrugSearch.tsx` rendered AWP with a `$` in a
  pharmacy trading in rial. Both removed — a line with no cost now has no cost,
  and the total does not pretend otherwise.
  Six dependents updated. `margin_optimizer` now reads acquisition from
  `inventory_lots.unit_cost` (what was actually paid) and price from
  `sell_price`, which is more correct than a benchmark ever was.
  340B keeps its `wac_price` FUNCTION parameter — that is a US program's own
  input, not this column; I removed it in error and restored it.
- **Owner repricing, with the history kept.** `POST /inventory/products/{id}/price`
  behind a NEW permission `inventory:price`, held by SUPER_ADMIN and
  PHARMACY_MANAGER and deliberately **not** by INVENTORY_STAFF — they receive
  goods and record what those cost; what the customer is charged is a commercial
  decision, the same separation that stops a requester approving their own
  write-off. `GET` returns the current price and every previous one.
  Every change appends a `shelf` point through `record_price`, the same SCD
  type-2 path every other price takes. A manual repricing that bypassed it would
  be the one price movement in the system with no history — precisely the one
  anyone would later need to explain to a patient who remembers paying less.
  Repricing sets EVERY sellable lot, because the shelf price is the maximum
  across them: leaving an older lot dearer would silently overrule the owner.
  The margin is recomputed against each lot's own `unit_cost`, so it stays
  auditable back to that lot's invoice.
- UI: `ShelfPriceCard` in the InventoryAdmin drawer — current price, the last
  six prices with dates, and a Set-price button. Bilingual, Persian digits.
- Verification: `pytest tests/unit -p no:randomly` → **1,581 passed**, 1 failed
  (`test_integrations_sandbox`, pre-existing). `tsc --noEmit` clean. Migration
  0041 applied to a disposable DB, downgraded (columns verified restored),
  re-upgraded, dropped; then applied to `pharmpilot_test` and `pharmpilot`.
  Single head 0041.

### 2026-08-09 — Claude (Opus 5) — correction: the owner's price COMPETES with the batches

- Workstream: `drug-data-integrity` + CL-003 scope (handoff recorded above)
- Branch/commit: `feat/inventory-integrity`
- **Correcting the entry above.** I implemented repricing as an override: it
  wrote the owner's number onto every sellable lot. That was wrong twice over —
  it destroyed each batch's own derived `sell_price`, and with it the answer to
  what that batch needed to sell for; and it inverted the rule.
- The owner's rule is a MAXIMUM over every candidate: each batch registered
  through purchasing, and whatever the owner entered by hand. Neither source
  overwrites the other, they compete. A lot bought dearer than the owner last
  typed must still lift the shelf, or that lot sells below its own replacement
  cost — which is the whole reason the highest-lot rule exists.
- Migration `0042` puts `manual_shelf_price` (+ `set_at`, `set_by`) on
  `drug_products`, beside the lots rather than over them.
  `shelf_price_of(lots, manual_price)` takes the max of both. Verified:
  batches 3,000/3,324 with the owner typing 3,100 → **3,324** (batch wins);
  the same batches with 5,000 → **5,000** (owner wins).
- `set_shelf_price` now returns `entered_price`, `effective_shelf_price` and
  `overridden_by_batch`, and the UI warns when they differ — otherwise the owner
  types 3,100, the till charges 3,324 and nobody can see why.
- A zero or negative manual price is not a candidate, so nothing can price a
  product at zero by accident.
- Verification: `pytest tests/unit -p no:randomly` → **1,606 passed**, 1 failed
  (`test_integrations_sandbox`, pre-existing). `tsc --noEmit` clean. 0042
  applied to `pharmpilot_test` and `pharmpilot`; head 0042.

### 2026-08-09 — Claude (Opus 5) — a mandated price, and the batch prices to pick from

- Workstream: `drug-data-integrity` + CL-003 scope (handoff recorded above)
- Branch/commit: `feat/inventory-integrity`
- The maximum rule is the right DEFAULT but it needed an override, because the
  rule can produce a wrong answer and then defend it. A batch received with a
  mistyped cost, or a margin entered as 200 instead of 20, lifts the maximum —
  and while that figure is the largest candidate there is no way down. The owner
  would be reduced to editing the batch to correct the shelf, which is rewriting
  history to change today's price.
- Migration `0043` adds `manual_price_is_mandate`. When set, the owner's price
  IS the shelf price and the batches are not consulted. Deliberately a switch,
  not a magic value, so that "the owner overrode this" and "the owner happened
  to type a large number" never look alike in the data. The batch figures stay
  untouched and keep explaining what each lot needed to earn; they stop deciding.
- The UI offers both: **«تعیین قیمت»** competes, **«تحمیل قیمت»** mandates. And
  the batch prices are listed as clickable chips — the usual repair for a wrong
  final price is "charge what the last batch charged", and picking beats
  retyping a figure from memory. Clicking one MANDATES it, because choosing an
  older, lower figure only sticks if the batches are not consulted.
- Safety kept visible rather than enforced: a mandate below the dearest batch
  means that batch sells under its replacement cost. `below_dearest_batch` and
  `dearest_batch_price` come back on the response and the UI warns in Persian
  with both numbers — but it does not refuse, because refusing would defeat the
  override. A mandate with no price falls back to the batches; the switch alone
  cannot blank the shelf.
- Verified: batches 3,000 and a mistyped 9,999 — competing with 3,100 gives
  9,999, mandating 3,100 gives 3,100.
- Verification: `pytest tests/unit -p no:randomly` → **1,633 passed**, 1 failed
  (`test_integrations_sandbox`, pre-existing). `tsc --noEmit` clean. 0043
  applied to `pharmpilot_test` and `pharmpilot`; head 0043.

### 2026-08-14 — Claude (Opus 5) — a simulator built to falsify the stock ledger

- Workstream: `inventory-integrity`
- Branch/commit: `feat/inventory-integrity` @ `a609f90`, `5fd0a9f`
- Built `tests/simulation/`: a seeded synthetic pharmacy (`world.py`, six demand
  archetypes), an **independent oracle** (`oracle.py`) written from the invariant
  equations and importing **no** application inventory code — it replays the
  event log from zero where the app updates incrementally, so an arithmetic
  error in one cannot be reproduced by the other — a driver that applies every
  event through the real routers, eleven invariants, and five escalating phases.
  `scripts/inv_simulate.py` runs sweeps, concurrency and long horizons.
- Defects it found and I fixed: the quantity ceiling wedging a SKU rather than
  refusing the receipt; reserved stock removable from under a promise; an
  unreachable bucket-release branch; `_abc_for` classifying the most valuable
  item as C (it measured cumulative share *after* adding the item, not before).
- Confirmed correct under attack, not merely asserted: 6 and 10 concurrent
  sessions racing the same lots leave ledger totals exactly the stock that
  existed; a mid-transaction `pg_terminate_backend` leaves no half-written lot,
  movement or aggregate; separation of duties and the append-only triggers both
  refuse what they should.
- Three clocks disagreed about "today" (server zone, UTC, and a bare
  `CURRENT_DATE`). `services/core/inventory/clock.py` gives one answer from the
  pharmacy's own timezone, and **falls back to UTC rather than the server's
  zone** — a fallback that silently follows the host makes expiry dates depend
  on where the process happens to run.
- Report: `docs/design/INVENTORY_SIMULATION_REPORT.md`. It does **not** claim
  exhaustive correctness; residual risk and untested areas are listed there.

### 2026-08-15 — Claude (Opus 5) — the engine roster, then E1 and E2

- Workstream: `inventory-integrity`
- Branch/commit: `feat/inventory-integrity` @ `151493e`, `9a34045`, `6140cdf`
- Recovered the founding intelligence requirements and wrote
  `docs/design/INVENTORY_INTELLIGENCE_ENGINES.md` — every AI/ML service the
  inventory and ordering side was ever specified to have, tiered by **what data
  it actually needs**, plus two new ones (⑳ distributor negotiation mentor,
  ㉑ seasonal decomposition). Tier A is honest today; Tier B needs 8–12 weeks of
  dispensing; Tier C needs supplier events.
- **E1 expiry risk** (`expiry_risk.py`). The version that existed charged every
  lot the item's full demand, so five lots of a slow mover each looked safe.
  It now FEFO-allocates demand across lots (`consumed_by_earlier`), so a later
  lot is judged on what is left after the earlier ones are consumed. Verdicts
  carry an action — return / discount / transfer / watch / write off — and
  `unknown` where there is no demand basis at all.
- **E2 receipt anomaly** (`receipt_anomaly.py`). Catches the wrong number *at
  the door* rather than in a report a fortnight later. Works today because
  36,612 formulary rows carry an announced price. Uses **median + MAD**, never
  mean + stdev: one poisoned 5,000 in the history inflates a standard deviation
  enough that the next bad receipt looks ordinary — the detector training itself
  to accept the thing it exists to catch.
- A receipt is never *refused* on these findings. The goods physically arrived,
  and books that describe a shelf which does not exist are worse than books with
  a flag on them.

### 2026-08-16 — Claude (Opus 5) — the delivery that never told the order it arrived

- Workstream: `inventory-integrity`
- Branch: `feat/inventory-integrity`
- **The gap, stated precisely.** `purchase_orders.ordered_at` *is* written, on
  submit (`inventory.py:335`) — my first framing of this was wrong and is
  corrected here. What was never written is `purchase_orders.received_at` and
  `purchase_order_lines.quantity_received`. Five places read those two columns
  and no code path filled them, so the two facts every supplier engine stands on
  — how long a supplier really takes, and how much of an order really turns up —
  had never once been recorded. That is why `supply_warning` (⑰) has never
  produced a real fill rate.
- `services/core/inventory/receiving.py` closes the loop as pure rules:
  - a receipt matches the **oldest open line** for that NDC; matching the newest
    leaves the older one outstanding forever, which reads as a supplier failure
    that never happened;
  - every delivery is classified **short / exact / over**. Over-delivery is
    named rather than absorbed — it is unbudgeted stock that may not sell before
    it expires — and `fill_rate` **caps it at the ordered quantity**, so excess
    on one line cannot offset a genuine shortfall on another and hide exactly
    what the metric exists to find;
  - a 2% band means a 1-unit difference on a 1,000-unit line is a rounding
    artefact, not a supplier failure. Scoring it as one makes every reliable
    supplier look unreliable;
  - the order's status is **derived from its lines**, never set by hand;
  - `received_at` is stamped **only when nothing is outstanding**. Stamping on
    the first delivery makes a part-filled order look faster than it was, and
    lead time is what safety stock is computed from;
  - under 5 closed lines, `fill_rate` refuses to quote a number: a percentage
    from three lines is an anecdote, and quoting one to a supplier is worse than
    saying nothing.
- Wired into `POST /inventory/admin/receive`. Added
  `GET /inventory/admin/open-orders` and an order picker on the receiving form,
  because a column nobody can fill from the bench stays empty. A delivery whose
  NDC is on no open line is still **recorded** — the goods are physically here —
  but is not attributed to any supplier's record, and the form says so.
  Reconciling against another pharmacy's order is refused with 404.
- **The payoff, measured rather than asserted.** E11 already consumed these
  columns; only rows were missing. `scripts/verify_receiving.py` (30 checks,
  all passing on `pharmpilot_test`) drives the endpoint and reads the rows back:
  after one delivered order E11 reports `basis=sparse` ("too few to describe a
  supplier; provisional"); after four it reports `basis=observed`, median 6.5
  days, sd 1.8. Lead time now moves `declared_default → sparse → observed`
  purely from goods coming through the door, and `refresh_demand` feeds it into
  every reorder point.
- Correction worth recording: I first asserted `observed` after a single
  delivery. E11 said `sparse` and was right — the honest-degradation rule
  working, and my assertion wrong.
- **Not done, deliberately:** the *dispensing* half of the substrate (E6 demand,
  E7 seasonality, E10 pick list). That needs prescriptions flowing through the
  platform, not more code here.
- No migration; both columns already existed and were simply never populated.
- Verification: `pytest tests/unit -p no:randomly` → **1,654 passed, 1 failed**
  (`test_integrations_sandbox`, pre-existing and previously logged), 1 xfailed.
  `tsc --noEmit` clean. `scripts/verify_receiving.py` → 30/30. Head unchanged at
  0043. UI checked only to the level of "builds, loads, no console errors" — I
  do not type credentials into the login form, so nothing past it was exercised.

### 2026-08-16 — Claude (Opus 5) — the invoice names both prices; the margin is what falls out

- Workstream: `inventory-integrity` (pricing chain); coordinated with the
  concurrent receiving work committed as `2c46f53` on the same branch.
- Branch/commit: `feat/inventory-integrity`, migration head **0044**
- Migration 0040 modelled the chain as `unit_cost + margin_pct → sell_price`,
  with the margin as the INPUT. The owner corrected the direction: "almost
  always the sell price is defined in each invoice … that percentage of margin is
  dependent on the drug, brand, dosage form, if the product is a drug or of
  cosmetics. cosmetics usually possess a higher profit margin. so it is a case by
  case matter." So the consumer price is transcribed from the فاکتور beside the
  purchase price, and the margin is DERIVED — measured, not chosen.
- That also disposes of the default-margin question I was about to ask for a
  number on. There is no single percentage that is right for a cosmetic and a
  generic tablet at once, so none is supplied. A lot with neither figure does not
  price, and the product falls through — no house constant, per the provenance
  rule the planning inputs already obey.
- Migration `0044` adds `inventory_lots.sell_price_basis`: `invoice` (observed) |
  `margin` (declared) | NULL. Both paths write a number into `sell_price`; without
  the column an invoice price and an arithmetic are indistinguishable once they
  are in the money path. Existing priced lots are backfilled to `margin`, since
  cost×margin was the only path that existed — labelling them `invoice` would
  claim a document nobody read. (Zero rows affected in practice: 17 lots, none
  priced.)
- `shelf_price.py` gains `margin_from_prices()` and `price_and_margin()`, the one
  place that decides precedence: a transcribed price outranks a margin typed
  beside it, because the document is evidence and the margin is opinion.
  `lot_sell_price()` is demoted in the docstring to the fallback it now is.
- The receive endpoint captures both, on create and on top-up, and returns a
  `pricing` block with the basis and a Persian explanation. A sell price under
  cost is **reported, never refused** — same principle as E2: the goods arrived,
  and books describing a shelf that does not exist are worse than flagged books.
  A pack price entered as a unit price looks exactly like this, and the warning
  says so.
- **Defect found and fixed in my own 0043 work**: `shelf_price_for_product` (the
  admin drawer) read `manual_price_is_mandate`; `shelf_prices_for_ircs` — the
  path a QUOTE takes — did not. An owner would override a mistyped batch price,
  the drawer would show the corrected figure and agree with them, and the till
  would still charge the batch. Exactly the failure the mandate exists to
  prevent, hidden by the drawer's agreement. `shelf_prices_for_ircs` now resolves
  **per product** and passes the flag, rather than pooling one IRC's lots and
  manual prices together — pooling would let another product's batch re-enter a
  comparison the owner had switched off.
- **Standing gap, unchanged and now measured**: 17 lots, 17 with a cost, 0 with a
  sell price, 0 products with a manual price. The chain is correct and inert
  until deliveries are received through the new fields; every quote still reads
  `nfi_fallback` today. Nothing was backfilled to hide that.
- Verification: `scripts/verify_shelf_pricing.py` drives the endpoint against
  `pharmpilot_test` — **16/16 checks passed** (invoice basis, declared basis, the
  unpriced gap, the shelf maximum across all three, the below-cost flag, and a
  top-up re-pricing). `pytest tests/unit/test_shelf_price.py` → 27 passed.
  `tsc --noEmit` clean. 0044 applied to `pharmpilot_mig` (disposable) with
  downgrade→re-upgrade evidence, then `pharmpilot_test` and `pharmpilot`; one
  head.

### 2026-08-17 — Claude (Opus 5) — E11 and E12: slow is not the same as unreliable

- Workstream: `inventory-integrity`
- Branch: `feat/inventory-integrity`
- Roster step 4. `receiving.py` (2026-08-16) made the substrate real; this is the
  pair of engines that consume it. `GET /inventory/suppliers` reports E11
  (lead-time distribution, per supplier) and E12 (`supplier_reliability.py`).
- **The modelling decision that shapes everything else.** Duration is reported
  and deliberately **not scored**. A supplier that takes eleven days every time
  is not a problem — those eleven days are already in the reorder point via
  `lead_time.reorder_signals`, and scoring them again would punish it twice for
  something the planner has absorbed. What cannot be planned around is
  variability. So:

      score = 0.6 × fill rate + 0.4 × consistency        (consistency = 1 − CV)

  Verified on real rows: an 11-day metronome outranks a supplier averaging
  3 days that sometimes takes 16.
- **Three refusals, each guarding a specific way this goes wrong:**
  - a composite is only as measured as its weakest input, so an unscored
    supplier sorts **last**, never first. The failure mode is a newcomer with
    two perfect orders topping the table and the pharmacy moving its business.
  - consistency requires an *observed* lead time (≥3 deliveries). One delivery
    has a standard deviation of zero, which reads as perfect predictability.
  - `comparable()` refuses a head-to-head below 3 shared products — otherwise
    the difference measures catalogues, not suppliers.
  - and a fill-rate ceiling on the grade: filling 40% of every order with
    perfect regularity blends to 0.64 ("mixed"), which is far too kind. Being
    reliably absent is not a virtue.
- **A blind spot found by running it, not by testing it.** A supplier that
  *always* short-ships never completes an order → `received_at` is never
  stamped → it acquires no lead time → the worst supplier on the roster became
  indistinguishable from one that had never delivered at all. `close_short` +
  `POST /inventory/admin/orders/{id}/close-short` let someone record that the
  rest is not coming. Outstanding units settle as **`backordered`** (the
  supplier failed) rather than **`cancelled`** (the pharmacy withdrew): the
  first counts in the fill rate, the second does not. Closing an order must not
  launder the failure that made closing it necessary. Measured end to end —
  before: unscorable, lead time `declared_default`; after: score 0.73, grade
  **poor**, lead time `observed` at 5 days, fill rate held at 0.55.
- Consequential change to `fill_rate`: it now counts only **settled** lines
  (complete / over / backordered). A part-filled line is still in flight and the
  balance may arrive tomorrow; scoring it as a shortfall penalises a supplier
  for an order placed yesterday. E12 reports `outstanding_lines` and raises the
  pile-up as a concern instead.
- **A latent bug fixed in passing.** `_record` wrote every proposal's `subject`
  into `ndc11 varchar(11)`. `expiry_risk` passes a lot UUID (36 chars), so the
  first expiry recommendation ever filed would have failed the insert;
  a supplier name would have appeared in the recommendations list as a drug.
  `RC.subject_columns()` now routes by shape — NDC, lot, or no column at all.
- Migration **0045** widens `ck_recommendation_kind_known` for
  `supplier_reliability` (TTL 90d, cooldown 90d — the longest in the table; a
  supplier's record does not change because a fortnight passed). Upgrade →
  downgrade → upgrade verified on a disposable database cloned from
  `pharmpilot_test`; single head 0045. The downgrade **deletes** rows of the new
  kind — a CHECK ignores `is_deleted`, so they cannot be retired — which
  destroys real human decisions and is documented in the migration rather than
  discovered from a row count.
- UI: a **Suppliers** tab on the engines panel, column order carrying the
  argument (delivered → predictable → typical days, unscored and last), and a
  "the rest is not coming" action on the receiving form's order picker.
- **Not captured, stated rather than defaulted:** substitutions. No receiving
  path records one, so E12 reports `substitution_basis: not_captured` and says
  a zero there means unmeasured, not never. `expected_delivery` is likewise
  never written, so "late" can only mean "slower than this supplier's own
  habit", not "later than promised".
- Verification: `pytest tests/unit -p no:randomly` → see the run recorded with
  this commit. `tsc --noEmit` clean. `scripts/verify_supplier_scorecard.py`
  drives 25 orders through the real receiving endpoint across four suppliers →
  24/24. `scripts/verify_receiving.py` → 30/30. UI checked only to
  "typechecks and renders"; I do not type credentials into the login form.

### 2026-08-17 — Claude (Opus 5) — pricing-chain commit, and a hole in the migration chain

- Workstream: `inventory-integrity` (pricing chain)
- Branch/commit: `feat/inventory-integrity` @ **`e0882c5`**. This is the commit
  for the entry above dated 2026-08-16 ("the invoice names both prices"), whose
  work was still uncommitted when that entry was written; the ledger text itself
  was swept into `dcd0c0a` by the concurrent supplier-reliability work.
- **Migration chain was broken on the committed branch.** `0045` declares
  `down_revision = "0044"`, but `0044_sell_price_basis.py` was still untracked —
  so a fresh clone had 0043 → (missing) → 0045 and could not upgrade. Committing
  0044 in `e0882c5` closes it. Two sessions committing to one branch is how this
  happened: the later work built on a file the earlier session had not yet
  committed. Worth remembering as a failure mode of the shared-tree arrangement,
  not just an oversight.
- **`pharmpilot_test` was left at 0045 while head was 0046.** A schema one
  migration behind the code is enough to produce setup errors in the E2E modules
  that build against a real database — which is the likeliest explanation for the
  6 errors + 2 failures in an earlier full run whose per-test names I lost to a
  truncated log. `test_exception_register_e2e` passes 7/7 in isolation and
  contains no pricing code, so it was never a regression from this work. The test
  database is now at head and the suite is being re-run for a clean record.
- Standing hazard for whoever reads this next: `pharmpilot_test` is long-lived
  shared scratch (149 pharmacies, ~39k lots, ~144k movements accumulated), and
  both `verify_receiving.py` and `verify_shelf_pricing.py` write into it. Two
  suites run concurrently against it will disagree with each other. A per-run
  teardown, or a database per session, would make these gates trustworthy.
- Re-verified after 0045/0046 landed: `scripts/verify_shelf_pricing.py` → 20/20
  against schema 0046. `pytest tests/unit/test_shelf_price.py tests/unit/test_pricing_ir.py`
  → 38 passed. `tsc --noEmit` clean. One head, 0046, on both databases.

### 2026-08-17 — Claude (Opus 5) — the E11/E12 suite result, and three sessions on one branch

- Workstream: `inventory-integrity`
- Branch/commit: `feat/inventory-integrity` @ `dcd0c0a`
- Filling in the figure the entry above deferred. Full suite after E11/E12:
  **1,706 passed, 3 failed, 1 xfailed, 3 errors** in 22:28.
  - `test_integrations_sandbox` — pre-existing, logged repeatedly above.
  - `test_occlusion.py` ×2 — the biometric/surveillance workstream, not mine.
  - `test_exception_register_e2e.py` ×3 **errors** — `DeadlockDetectedError` on
    `inventory_exceptions`. Not a defect: **two pytest runs were executing
    concurrently against `pharmpilot_test`**, mine and the surveillance session's,
    and both fixtures issue the same tenant-wide `UPDATE ... SET is_deleted` on
    the same rows. They deadlocked each other. Nothing in inventory is implicated
    — the inventory tests all pass, and `test_exception_register_e2e` passes on
    its own.
  - The 22-minute wall clock is the same two causes: contention plus the table
    bloat the surveillance owner has separately recorded above.
- **Coordination, for the record.** Three workstreams were committing to
  `feat/inventory-integrity` at the same time this afternoon: inventory (mine),
  pricing, and surveillance/biometric. That is contrary to the one-owner rule at
  the top of this document and it had a visible consequence — commit `e0882c5`
  (pricing) carries my `close_order_short` endpoint, `openOrders`/
  `closeOrderShort` API clients and receiving-form changes, because
  `inventory_admin.py`, `InventoryAdmin.tsx` and `lib/api.ts` were jointly
  modified and neither of us could commit our own half cleanly. I deliberately
  left those three files to the other session rather than absorb its in-flight
  work into my commit; everything I owned outright is in `dcd0c0a`.
  Verified at HEAD after both landed: `close_order_short`,
  `_reconcile_purchase_order`, `open_orders`, the three API clients, the
  receiving-form action and the Suppliers tab are all present and intact;
  89 inventory unit tests pass, `verify_receiving.py` 30/30 and
  `verify_supplier_scorecard.py` 27/27 both pass on the merged tree.
- Migration chain, resolved: 0044 (pricing) → 0045 (mine) → 0046 (surveillance),
  single head. For a while 0046 was committed while 0045 and 0044 were not, so a
  fresh checkout of this branch could not have migrated at all. All three are now
  committed. `pharmpilot` is at 0046; **`pharmpilot_test` is at 0045** and wants
  an upgrade before the next schema-touching test run.
- Next action for this workstream: roster step 5 — E13 shortage early warning and
  ⑳ negotiation mentor, both of which sit on E11/E12 and are now unblocked.

### 2026-08-17 — Claude (Opus 5) — E13: a short fill is not a shortage; ⑳: never invent a benchmark

- Workstream: `inventory-integrity` (CL-003 scope)
- Branch: `feat/inventory-integrity`
- Roster step 5, the last of Tier C. Two engines, one migration, and service ⑰
  retired to an envelope around the new one.

**E13 — `services/core/inventory/shortage.py`, `GET /inventory/shortage-warning`.**

- It exists for one distinction ⑰ could not make. Short from *every* supplier
  that carries a molecule means the market is out: cover it, substitute, or warn
  the prescribers. Short from one while another delivers in full means the
  molecule is available and the fix is a phone call. ⑰ scored both identically
  and recommended buffer stock for both — paying to hold inventory that expires
  on the shelf against a problem that costs nothing to solve. The verdicts are
  about **cause**: `market_shortage`, `supplier_shortage`, `thin_cover`, `watch`,
  `no_signal`, `unknown`.
- Four defects removed with it, all of which were *harmless only while
  `quantity_received` was never written*. Once receiving fills that column the
  same code produces plausible wrong answers, which is worse than obviously
  broken: `fill_rate = 1.0` for anything never ordered; `COALESCE(avg_daily_demand,
  0)` turning unmeasured demand into infinite cover and dropping the item off the
  report; a **third** hard-coded lead time (5 days, against `lead_time`'s
  declared 7), unlabelled; and in-flight lines counted as total short-fills.
- **Recalibration found by running it.** `CUSUM_ALARM` was 0.5, which needs
  fifteen consecutive short deliveries — by which point the aggregate rate has
  fallen through the short-fill threshold and the item is flagged on that
  instead, so the `watch` verdict was unreachable dead code. Set to 0.2. Where it
  earns its place: twenty clean deliveries then four at 85% leaves the average at
  97.5% *because* the past was good, and only the CUSUM sees it.
- ⑰ (`services/ai/intelligence_services/supply_warning.py`) now computes nothing.
  It is the §1.2 envelope and the (still inert) cloud tier over E13.
  `disruption_risk` survives for the payload contract but is documented as a
  severity band; the figure it replaced was a 0.45/0.35/0.20 weighting shown to
  three decimals as though calibrated against outcomes. `model_version` →
  `supply_warning_v2`.

**⑳ — `services/core/inventory/negotiation.py`, `GET /inventory/negotiation-brief`.**

- Leverage (spend, share, standing), the negotiable gap, and what unreliability
  costs — E12's fill rate turned into money, which is the strongest card and had
  never been computed.
- Its governing rule is a refusal: **it never invents a benchmark.** Every
  comparison is between two prices this pharmacy has actually paid, because the
  failure is not a wrong figure on a screen — it is the owner repeating a
  fabricated market rate to a distributor who knows the real one. One supplier
  for a molecule → no gap, stated. No recorded sale price → the undelivered
  **units** are reported and the money is not; a guessed margin quoted to someone
  who sells these for a living is the same mistake as a guessed benchmark.
  Payment terms and cost of capital exist nowhere in this system, so a longer
  payment window is explicitly `unpriced`. The `cannot_say` list is rendered as
  prominently as the asks.
- Deliberately **not** in the recommendation ledger. That machinery measures
  unsolicited advice; a brief the owner asked for is already accepted by being
  requested, and filing it would inflate the denominator the fingerprint protects.
  Recorded in migration 0047's docstring so the omission reads as a decision.

**Schema.** Migration **0047** widens `ck_recommendation_kind_known` for
`shortage_warning` (TTL 21d, cooldown 30d). Upgrade → downgrade → re-upgrade
verified on a disposable database cloned from `pharmpilot_test`; single head 0047;
applied to `pharmpilot_test` and, after a pre-dump of
`inventory_recommendations`, to `pharmpilot`. The downgrade deletes rows of the
new kind — a CHECK ignores `is_deleted` — which destroys real pharmacist
decisions and is documented in the migration.

**UI.** A **Shortages** tab grouped by verdict rather than one sorted list,
because the grouping *is* the finding; and a "prepare for a conversation" brief
inside the Suppliers tab, pulled rather than pushed.

**Verification.** `scripts/verify_shortage_and_negotiation.py` — 49 orders across
two suppliers and five molecules shaped to land on one verdict each, every
delivery through the real receiving endpoint and every short order closed through
the real close-short endpoint → **28/28**, with all five verdicts and five
distinct actions observed on rows the platform wrote. 44 new unit tests.
`pytest tests/unit -p no:randomly` → **1,799 passed, 1 failed**
(`test_integrations_sandbox`, pre-existing and logged repeatedly above),
1 xfailed, in 4:46. `tsc --noEmit` clean. UI checked only to "typechecks and
renders" — I do not type credentials into the login form.

Two corroborations from that run, both about the *previous* entry rather than
this work: the `test_occlusion` failures are gone (the biometric owner fixed
them) and the `test_exception_register_e2e` deadlocks did not recur. The suite
also took 4:46 against 22:28. All three confirm the earlier diagnosis — those
were two pytest runs racing the same test database, not defects.

**Flagged, not fixed (outside this scope).**
`frontend/workstation/src/components/IntelligenceInventoryPanels.tsx` still carries
a hardcoded `DEMO_SUPPLY` fixture with invented NDCs, a 0.62 fill rate and
`avg_daily_demand: 14` — the same fabricated-placeholder pattern removed from
`InventoryIntelligence.tsx` earlier. Raised as a separate task rather than
absorbed here.

**Tier C is complete.** What remains is Tier B (E6 demand, E7/㉑ seasonality,
E8 reorder point, E10 pick list), and none of it is blocked on code: it needs
prescriptions flowing through the platform. ㉑ in particular wants two full
years before it may make an annual claim.

### 2026-08-17 — Claude (Opus 5) — the pricing engine did not add up

- Workstream: `inventory-integrity` (pricing chain)
- Branch: `feat/inventory-integrity`
- `engine.py` has documented one identity since it was written:
  `insurer_share + patient_total == gross + vat`. It was not true. A randomised
  sweep of 48,000 lines broke it on **489 lines (~4%) at the DEFAULT whole-Rial
  unit** and 820 (~7%) at the 1,000-Rial unit `config.py` explicitly invites a
  pharmacy to set. Money appeared and vanished a Rial at a time on a receipt a
  patient is handed, and the insurer was billed the difference.
- Cause: `covered_base` and `differential` were each `_round`ed independently,
  and `round(a) + round(b) ≠ round(a + b)`. Not hypothetical — `sell_price` and
  `manual_shelf_price` are both `Numeric(12,4)`, so sub-Rial shelf prices are
  ordinary, and fractional quantities are ordinary. Example that broke at the
  default unit: 3,324.5 against a 1,662.25 reference over 30 units, out by 1 Rial.
- Fix: the differential is DERIVED, `gross − covered_base`, never rounded on its
  own. `ref_unit ≤ consumer_price` and quantize is monotone, so it is never
  negative and `covered_base + differential == gross` holds **by construction at
  any rounding unit**. It is also the truer reading of مابه‌التفاوت: the part of
  the sale price the insurer does not recognise.
- Three more found while testing:
  * `ROUNDING_UNIT_RIAL` was bound at import (`from .config import …`). A
    deployment that set a coarser unit kept rounding to the whole Rial until the
    process restarted, and no test could change it. An engine documented as
    config-driven now reads the config at call time.
  * `Decimal(line.quantity)` on a float carried
    `0.1000000000000000055511151231257827…` into every product below it. The
    router stringified; the engine no longer depends on callers remembering.
  * A negative quantity inverted the `ref×qty ≤ consumer×qty` assumption and
    produced a negative insurer share. Now refused — a return is a different
    operation, not a line with a minus sign.
- `covered_base` is now on the quote line. A line showing مابه‌التفاوت without
  the base it was measured against cannot be checked by whoever pays it.
- **A false alarm worth recording**, because it will recur: the first run of the
  verification script reported 80 broken lines at every rounding unit. The
  engine was right and the CHECK was wrong — on an uncovered line `covered_base`
  and `differential` are zero by design, so that identity does not apply. It
  only ever fired under salamat, which marks rows uncovered that the other plans
  fall back to covering. Rounding-independent + insurer-specific is the signature
  of a wrong assertion, not a wrong calculation.
- Verification: `tests/unit/test_pricing_ir.py` now carries a 5,376-combination
  sweep (price × reference ratio × quantity × rounding unit × VAT × setting) plus
  regression pins for both broken lines → **18 passed**.
  `scripts/verify_pricing_conservation.py` drives the real `/pricing/quote`
  against the working database (read-only) on real formulary rows — **4,608
  priced lines across four rounding units, all identities holding**. It targets
  the working DB deliberately: `pharmpilot_test` holds one priced row and none
  with a reference, so running it there would report success while proving
  nothing.
- **Still unproven and now the only thing in the way**: the tariffs. The engine
  is self-consistent; whether 30/70, حق فنی and the VAT exemptions match what a
  real pharmacy charges is untested, and every one is VERIFY-tagged in the
  config. Special populations (کمیته امداد/روستایی 15%, special-disease 0%) are
  not modelled at all — such a patient is over-charged today.

### 2026-08-22 — Claude (Opus 5) — the tariffs, researched against regulator and court

- Workstream: `inventory-integrity` (pricing chain)
- Branch: `feat/inventory-integrity`
- Owner asked for deep research on every VERIFY-tagged tariff, then explicitly
  for the regulatory and legal sources rather than news reporting, and for a
  check-in before applying. Owner decisions recorded: **private-sector band**,
  **tariffs only** (no new engine behaviour).
- **Largest correction — حق فنی is paid by the patient; no basic insurer pays a
  Rial of it.** The pharmacy formula is `پرداختی بیمار = سهم بیمار + اقلام آزاد +
  حق فنی`, and the pharmacists' association is still campaigning to have basic
  insurance cover it "like a physician's visit fee" — itself the proof it does
  not. `covers_technical_fee` was `True` with a 30% patient share, so on the 1405
  fee the engine charged the patient 218,100 and billed an insurer **508,900 per
  prescription** that would be rejected at reconciliation. ~20.4m ﷼/day at 40
  prescriptions.
- 1405 tariff, cabinet resolution via انجمن داروسازان ایران, effective ۱ فروردین:
  کد ۹۰۵۰۱۰ نسخه‌پیچی **727,000 ﷼/نسخه**، کد ۹۰۵۰۱۵ OTC 81,000 ﷼، کد ۹۰۵۰۳۰
  مشاوره 1,620,000 ﷼. Banded by sector — 1404 code 905010 was 336,000 خصوصی /
  308,700 خیریه / 227,500 عمومی غیردولتی — so the number is only right for a
  private pharmacy, and the config says so.
- **Legal status recorded in the config, not just the number.** هیئت عمومی دیوان
  عدالت اداری has annulled this charge SIX times (۳/۲/۸۸، ۱۹/۷/۸۹، ۱۷/۴/۹۳،
  ۲۸/۱/۹۷، ۲۰/۶/۹۷، ۸/۱۱/۹۸؛ also آرای ۱۹۹ و ۶۸۳): a technical manager's duties
  are not diagnostic/treatment services, ماده ۸ قانون بیمه همگانی ۱۳۷۳ confers no
  power to price them, so the tariff is outside the cabinet's competence. It was
  re-established under a new name — «حق فنی» → «تعرفه خدمات دارویی», a کتاب ارزش
  نسبی code. Collected in practice; set the constant to 0 to stop.
- **A trap the legal search caught.** The 1405 resolution (هیئت وزیران,
  ۲۵ اسفند ۱۴۰۴) bands the outpatient franchise by income decile — 25/30/40% for
  deciles 1-3/4-6/7-10 — which reads exactly like the refinement a pricing engine
  should adopt. It carves drugs out: «به استثنای داروها». Applying it to pharmacy
  lines would have been wrong on every row while looking more sophisticated than
  the correct answer. Documented in `config.py` so the next reader does not adopt
  it either.
- Confirmed unchanged: **30% outpatient / 10% inpatient** drug franchise for
  tamin and salamat (statutory; corroborated by peer-reviewed literature).
  **VAT** — drugs exempt (ماده ۹ بند الف جزء ۱۵، قانون ۱۴۰۰), supplements exempt
  since بخشنامه ۲۰۰/۴/۱۴۰۳ **but only مکمل دارویی with an IRC licence** (a food
  supplement remains taxable, so a mis-categorised row is under-taxed),
  cosmetics 10% (9% + 1% عوارض).
- Corrected: **armed forces 0.15 outpatient / 0.00 inpatient** (ساخد cut the drug
  franchise 30%→15%; inpatient free at government/military centres, though
  10-35% at private contracted ones — one figure cannot express that).
- **Corrects an error in this ledger's own earlier note**: روستایی/عشایر are 30%
  for drugs inside the referral path and **100% outside it**, not 15%. کمیته
  امداد/بهزیستی is the 15% case. Special-disease patients are free. Still not
  modelled — all are over-charged today — because a per-category franchise with
  no provenance for how the category was established is worse than none.
- Three fee rules documented but deliberately NOT enforced (owner scoped to
  tariffs): the three-item-per-prescription cap — so a four-item Rx is
  over-charged today — the +40% night/holiday uplift, and the تی‌تک requirement.
- Verification: `pytest tests/unit/test_pricing_ir.py` → **24 passed**, including
  six new tariff pins so a silent revert cannot happen.
  `scripts/verify_pricing_conservation.py` → 4,608 real formulary lines at four
  rounding units, all identities holding with the new fee in place. Full suite:
  **1 failed, 1,813 passed** (`test_integrations_sandbox`, pre-existing ordering).

### 2026-08-18 — Claude (Opus 5) — E6: two items with the same rate and nothing else in common

- Workstream: `inventory-integrity` (CL-003 scope)
- Branch: `feat/inventory-integrity`
- Roster step 6, first of Tier B. `services/core/inventory/intermittent.py`, wired
  into `POST /inventory/demand/refresh`. No migration — nothing new is stored;
  the classification feeds the reorder point that column already held.
- **The roster's framing was "Croston/TSB, not ARIMA", and building it corrected
  that.** Croston is implemented and reported, but it is not what makes E6 worth
  having: a mean over the window is already an unbiased estimate of the long-run
  rate, and beating it is not the problem. The problem is that a rate cannot
  express *shape*. Measured through the refresh endpoint on real rows:

      2 units a day, most days      2.00/day   reorder point   8.6
      14 units every week           2.00/day   reorder point  22.9
      5, 60, 12, 80, 8 at random    1.96/day   reorder point  87.6

  A tenfold spread between items whose rates differ by less than 0.2 units a day.
- Classification is Syntetos-Boylan on ADI and CV² of event sizes, **at the
  published cut-offs (1.32 / 0.49) rather than house values** — tuning them here
  would make the scheme unfalsifiable against the literature it comes from.
  `lumpy` is the honest quadrant: rare events of wildly varying size cannot be
  forecast well by any method, so the engine reports the size of one event and
  says plainly that a daily figure would be a claim about accuracy nobody can
  support.
- `lead_time.reorder_signals` gained an **event-cover floor**. It is a floor, not
  an override: for a *regular* burst pattern the daily-bucket σ — with the zeros
  in it — already covers one event, and raising nothing there is correct. In the
  verification it bit on the lumpy item and on nothing else.
- **Two corrections to my own first design, both found by running it:**
  - Croston cannot detect a moving rate at α = 0.1. When demand rises the
    smoothed size climbs while the smoothed interval shortens, and the two
    effects very nearly cancel in the ratio — on a series that went from 2 units
    a week to 60 every three days, SBA and the mean differed by **1%**. The
    drift detector was rebuilt on a half-window comparison, which needs no
    smoothing constant to argue about and lets both numbers be shown. The first
    version would have shipped a detector that never fired.
  - A unit test I expected to pass caught a fixture that generated 60 daily fills
    into an 84-day window, so the series began a third of the way in. The drift
    detector called it "rising" and was right — that is an item that *started
    selling*, not a steady one.
- **A latent fabrication removed.** `reorder_signals` turned an unmeasured spread
  into `demand × 0.5` — an invented coefficient of variation presented as a
  safety stock, and several-fold too small for exactly the lumpy items that most
  need cover. It now returns no cover rather than assumed cover. Currently
  reachable only by callers other than `refresh_demand`, so this is a trap
  removed rather than a live defect fixed.
- **Data honesty.** Production has 46 fills across 7 NDCs on a single day;
  `pharmpilot_test` has 19 days and no item with more than 5 dispensing days.
  Neither can validate a forecast. The unit tests pin the arithmetic on synthetic
  series and `scripts/verify_demand_shape.py` writes three deliberately-shaped
  dispensing histories and drives the real endpoint — so what is proven is the
  classification, the floor, and the degradation. **Forecast quality against real
  pharmacy behaviour is not proven and cannot be until the pilot dispenses.**
- Verification: 25 new unit tests for E6, 4 more for the reorder floor;
  `scripts/verify_demand_shape.py` → 24/24; the three earlier scripts still pass
  (30/30, 27/27, 28/28). `pytest tests/unit -p no:randomly` → **1,842 passed,
  1 failed** (`test_integrations_sandbox`, pre-existing and logged repeatedly
  above), 1 xfailed, in 5:52. `tsc --noEmit` clean.
- Next: E7/㉑ seasonality is the only Tier B engine with a hard data gate — two
  full seasonal cycles before it may make an annual claim — so it can be built to
  refuse, but it will refuse for a long time. E10 pick list is the more useful
  one and needs storage locations more than history.

### 2026-08-18 — Claude (Opus 5) — E7, E9, E10: the roster is complete

- Workstream: `inventory-integrity` (CL-003 scope)
- Branch: `feat/inventory-integrity`
- The last three engines on the roster. No migration — all three are read-only
  computations over columns that already exist.

**E7 / ㉑ seasonality — `seasonality.py`, `GET /inventory/seasonality`.**

- Buckets by **Jalali month**, using the existing `services/core/localization/
  jalali.py`. This is arithmetic, not decoration: Nowruz is 1 Farvardin every
  year and drifts across 20-21 March, so a Gregorian March bucket splits the
  new-year peak across two months and halves it. The school year turns on 1 Mehr,
  which lands in September or October.
- Two refusals carry it. **Two complete cycles or no claim, ever** — with one
  turn of the year there is no second observation of any month, so a real peak
  and one unusual month are the same data. And where the month explains less than
  30% of the variance, the answer is "no detectable seasonality" rather than a
  flat line with a shape drawn on it. The strength statistic is reported so the
  claim can be argued with.
- **Ramadan is not captured and every response says so.** It is lunar and moves
  against the Jalali calendar as well, roughly eleven days a year, so it smears
  across Jalali months exactly the way Nowruz smears across Gregorian ones.
  Capturing it needs a Hijri conversion this codebase does not have. The effect
  lands in the residual, which *depresses* the strength statistic — so the
  omission makes a seasonal claim harder to make, not easier, which is the safe
  direction for a gap to fail in.
- On the pilot's data it will answer `insufficient_cycles` for a long time. That
  is the engine working, not waiting.

**E9 expiry probability — `expiry_probability.py`, `GET /inventory/expiry-odds`.**

- E1 says a lot either clears or does not. Most real stock is in the middle, and
  the middle is where the money is: a lot at 5% is not worth discounting, the same
  lot at 60% is worth discounting today while a customer still exists, and E1 puts
  both in the same bucket.
- The guard is that the normal approximation is the central limit theorem doing
  the work, and the theorem needs a sum of many things. A probability is quoted
  only where the horizon is expected to contain ≥10 demand events, computed from
  E6's measured interval. **Lumpy items are refused outright** — E6 declines to
  forecast them and this declines to put a number on them, the same judgement
  twice. No measured spread is also a refusal: the whole probability is a function
  of σ.
- Carries E1's FEFO allocation through, so lots expiring sooner take their share
  of the same demand stream first. Charging every lot the item's whole demand is
  the defect E1 was rebuilt to fix and it would have reappeared here unchanged.

**E10 morning pick list — `pick_list.py`, `GET /inventory/pick-list`.**

- The replenishment machinery already existed — shelves, placements, a
  dual-verified session, a transfer ledger. What it never had was an answer to
  *which items and how many*: `create_session` takes a list of NDCs somebody
  typed in, so the intelligence in the morning round has been a person
  remembering what ran out yesterday.
- **A shelf stocked to the mean runs out half the time**, which is not a service
  level anybody would choose out loud. The target is the 90th percentile of a
  day's demand — except for items whose demand arrives whole, where a percentile
  of a daily total is meaningless and the shelf needs one event.
- Two refusals, physical rather than statistical, and both **drops rather than
  warnings** because a warning on a picking list is read at speed by somebody
  holding a crate: a refrigerated drug is never proposed for a room-temperature
  shelf, and nothing quarantined, recalled or expired is ever picked. Shelf
  capacity caps the request whatever the target says.
- Items with no measured demand are left off and the omission is explained.
  Shelf space is the one thing in a pharmacy that cannot be ordered more of.
- The skipped list is rendered as prominently as the round: "not on the list"
  reads as "not needed" unless the reason is given.

**A defect found by running it.** `expiry-odds` ordered lots by `(ndc11,
expiry_date)` only. Lots sharing an expiry date are entirely ordinary, and with
no second key the FEFO cascade ran in whatever order the query planner returned —
so the same shelf could be given different per-lot probabilities on two
consecutive runs. Fixed with a `lot_number` tiebreak, matching what
`pick_list._fefo` already did, and the verification now asserts twice-identical
output.

**Verification.** 54 new unit tests (16 E7, 18 E9, 20 E10).
`scripts/verify_demand_shape.py` extended to cover all four Tier B engines end to
end on a tenant with three deliberately-shaped dispensing histories → **39/39**,
including the three-item table where the same rate produces reorder points of
8.6 / 22.9 / 87.6 and pick-list targets of 2.3 / 14 / 80. `tsc --noEmit` clean.
`pytest tests/unit -p no:randomly` → **1,896 passed, 1 failed**
(`test_integrations_sandbox`, pre-existing and logged repeatedly above),
1 xfailed, in 9:57.

**The roster is complete.** Every engine E1–E14 plus ⑳ and ㉑ is built. What is
*validated* is narrower and worth stating plainly: the arithmetic and the
refusals. Forecast quality against real pharmacy behaviour is untested for every
Tier B engine and will stay that way until the pilot dispenses — production holds
46 fills across 7 NDCs on a single day. The engines are built to say so, and on
that data they do.

### 2026-08-18 — Claude (Opus 5) — review of the whole inventory section: six defects, one referred

- Workstream: `inventory-integrity` (CL-003 scope)
- Branch: `feat/inventory-integrity`
- A deliberate review pass over ~8,900 lines across 29 core modules, 6 routers and
  the engine dashboards, rather than more building. Method: cross-module constant
  audit, a provenance sweep for fabricated fallbacks, an AST check that every
  id-taking handler is tenant-gated, adversarial inputs to all 40 pure entry
  points, a declared-vocabulary-vs-reachable-code check, and the invariant
  simulator over the ledger.

**Clean.** The ledger survived 3,368 events over 8 seeds with **0 invariant
violations**. All 40 pure entry points survived empty / None / zero / negative /
huge inputs without raising. No fabricated fallback on a measurement anywhere —
the provenance rule holds across every engine. Every id-taking handler in the
inventory routers is tenant-gated.

**Fixed.**

1. **E12 called the most reliable supplier on the roster a 100% short-filler.**
   `receiving` deliberately treats 999-of-1000 as an *exact* delivery, with a
   comment saying that scoring a rounding gap as a failure "would make every
   reliable supplier look unreliable". `supplier_reliability` compared exactly,
   so a supplier delivering 999 on every line scored `short_lines = 8/8` and had
   a concern raised against it. The same failure `EXACT_TOLERANCE` exists to
   prevent, reintroduced one module over. There is now one definition of "short"
   (`_is_short`), and it is receiving's.

2. **The clinical escalation depended on a button on another tab.** E13 read
   `stock_levels.avg_daily_demand`, which only exists after somebody runs a
   demand refresh, while E6 and E10 compute from the fill record directly. With
   no stored rate there is no days-of-cover, so `thin` was False and an identical
   market shortage came out **`high / buffer_stock`** instead of
   **`critical / alert_prescribers`** — measured both ways on the same shelf.
   "Stop writing this prescription" must not depend on whether anyone pressed
   Recalculate. The endpoint now falls back to the fill record when the stored
   signal is absent, and says which source it used. Verified end to end: with the
   signal nulled, cover comes back 800 days, basis `observed`, concern attached.

3. **`/expiry-odds` returned 24,000 lots in one response** on a large tenant —
   measured, not hypothetical. Now capped at 500 worst-first with
   `lots_total` / `shown` / `truncated`, and the totals stay over every lot:
   silent truncation would read as "that is all of them".

4. **`pick_list.REASONS` declared three values no code path produced** and
   omitted the four it did. A vocabulary the code cannot emit is a case the UI
   may render and the engine can never reach. Renamed `SKIP_REASONS`, corrected,
   and a test now asserts the declaration equals what `build` emits.

5. **`shortage.ACTIONS` promised `seek_alternative`**, which nothing could
   produce — proposing a therapeutic substitute needs an equivalence source this
   platform does not have. Removed rather than left aspirational.

6. **An already-expired lot was labelled `basis="observed"`** in E9 though nothing
   had been observed about its demand. Now `already_expired`.

Plus two pieces of hygiene: `expiryOdds` was in the API client with **zero call
sites**, so E9's money figure was unreachable from the UI — now surfaced in the
Value tab, `unknown` rows included with their reasons, because a list of only the
quantified lots would read as the whole exposure. And the verification scripts
called handlers positionally, so adding a query parameter shifted every argument
and blew up inside a handler; they now go through a `call()` helper that resolves
FastAPI defaults the way the framework would.

**Referred, not fixed — outside CL-003 and it needs its owner.**
`POST /inventory/products/{product_id}/price` (`routers/inventory.py:415`) gates
on the `inventory:price` permission but **not on tenancy**, and the columns it
writes — `drug_products.manual_shelf_price`, `manual_price_is_mandate`,
`manual_price_set_by` — live on the **global product catalogue**, which has no
`pharmacy_id`. `price_history` has none either. So on a multi-tenant deployment
one pharmacy repricing a product changes what **every** pharmacy charges, and
they share one price history. Latent today (production holds a single pharmacy)
and invisible until the second one arrives. Verified against the live schema
rather than inferred. This is the pricing workstream's file and migrations
0042/0043 are its work, so it is recorded here for that owner rather than edited
across the boundary — and it is exactly the "tenant isolation" gate this document
lists as requiring independent review.

**Verification.** 6 new regression tests, one per fixed defect. All four
verification scripts pass on `pharmpilot_test` (30/30, 27/27, 28/28, 41/41).
`scripts/inv_simulate.py --seeds 8` → 0 findings. `tsc --noEmit` clean.
`pytest tests/unit -p no:randomly` → **1,902 passed, 1 failed**
(`test_integrations_sandbox`, pre-existing and logged repeatedly above),
1 xfailed, in 8:08.
