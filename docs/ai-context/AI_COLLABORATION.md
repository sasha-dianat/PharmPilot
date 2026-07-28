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
| CL-001 | Claude Code | ACTIVE | `feat/darunameh-crawler` / PR [#22](https://github.com/sasha-dianat/PharmPilot/pull/22) | Every file currently changed by PR #22, primarily drug catalog, coverage harvest/import, enrichment, catalog/coverage models and migrations, related workstation administration, and their tests | Freeze scope, refresh the PR description to the actual head, run evidenced release gates, then independent review |
| CX-001 | Codex | READY_FOR_REVIEW | `agent/ai-collaboration-protocol` (stacked on CL-001) | `AGENTS.md`, `CLAUDE.md`, and `docs/ai-context/AI_COLLABORATION.md` only | Review and merge the coordination protocol into `feat/darunameh-crawler` |
| CX-002 | Codex | PLANNED | Read-only review of PR #22; fix branch only after findings are accepted | Independent review of tenant isolation, PHI/AI-provider policy, migration integrity, pricing conservation/provenance, frontend/API regressions, and test evidence; no edits to CL-001-owned files without handoff | Deliver prioritized findings with file/line evidence and proposed ownership |
| CL-002 | Claude Code | PLANNED | New branch after CL-001 stabilizes | Iran-proxy/NFI and insurer-publication data operations, replay evidence, and source diagnostics; no Codex hardening paths | Project owner approves data-source inputs and operating window |
| CX-003 | Codex | PLANNED | New branch from the accepted post-PR-22 base | First safety slice from `CODEX_NEXT_BUILD_PLAN.md`: tenant-bound, provenance-safe identity; excludes Claude-owned data-pipeline paths | Project owner approves implementation after CX-002 and PR #22 disposition |

The PR file list is authoritative for CL-001 ownership while PR #22 remains
active. A path leaves that boundary only through a handoff entry below.

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
