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
