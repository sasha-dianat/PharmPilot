# AGENTS.md

## Purpose

This repository is PharmPilot AI, an Iran-first pharmacy operations and medication-intelligence platform. Its primary product is a pharmacist workstation backed by a FastAPI/PostgreSQL modular monolith. Iranian catalog, coverage, price governance, deterministic pricing, and deterministic clinical support are the strongest areas. AI is advisory and degraded-mode assistance, not a source of autonomous clinical truth.

Treat the repository as an advanced prototype/pilot foundation, not a production-ready pharmacy system.

## Read first

Before substantial work, read:

1. `docs/ai-context/CODEX_PROJECT_BIBLE.md`.
2. This `AGENTS.md`.
3. `docs/ROADMAP.md` for current intent and maturity.
4. The directly relevant code and tests.

If the Project Bible is absent, reconstruct only task-relevant context from this file, `.codex-intake/CODEX_PROJECT_DEEP_DIVE.md`, and `.codex-intake/SOURCE_STATE.md`. Create or update the Bible when write access is available.

Use targeted exploration. Do not reread the entire repository for every task.

## Source-of-truth priority

For current behavior, prefer:

1. Actual code, migrations, and tests.
2. `docs/ROADMAP.md`, verified against code.
3. `.codex-intake/SOURCE_STATE.md` for snapshot provenance.
4. `docs/ai-context/CODEX_PROJECT_BIBLE.md`.
5. `.codex-intake/CODEX_PROJECT_DEEP_DIVE.md` with Bible corrections applied.
6. `README.md` and `DAY1_RUNBOOK.md`, only after verification.

Do not repeat stale claims such as 402 passed, 1 skipped, clean frontend typecheck, verified Day 1 lifecycle, or Docker Compose readiness without fresh evidence.

## Repository layout

- `services/platform/`: FastAPI composition, configuration, auth, database sessions, migrations, and routers.
- `services/core/pharmacy_workflow/`: Rx state machine, DUR, triage, intake precompute, patient context, and back-office agents.
- `services/core/drug_catalog/`: Iranian catalog, coverage harvest/import, matching, crosswalk, enrichment, canonical export, and price history.
- `services/core/pricing_ir/`: deterministic `Decimal`/Rial pricing.
- `services/core/inventory/`: movement, depot/replenishment, procurement, and stock intelligence.
- `services/core/adjudication/`: US/NCPDP-style sandbox adjudication, not live Iranian adjudication.
- `services/ai/`: deterministic/advisory clinical modules, provider registry, RAG, offline-first services, OCR, transcription, and vision.
- `services/integrations/iranian_insurance/`: Iranian insurer adapter contracts and sandbox fallback implementations.
- `services/biometric/`, `services/audio/`: experimental identity, security, evidence, and transcription surfaces.
- `shared/models/`: main SQLAlchemy model registry.
- `data/migrations/versions/`: linear Alembic chain `0001` through `0025` by static inspection.
- `frontend/workstation/`: main dispensing workstation and 24-section dashboard shell.
- `frontend/admin/`: overview shell; most sections are placeholders.
- `mobile/patient_app/`: patient-labeled Expo prototype currently using staff authentication.
- `tests/`: Python unit and clinical-validation tests.
- `frontend/workstation/tests/e2e/`: Playwright tests and ignored local artifacts.
- `docs/`: roadmap and historical specifications.
- `.codex-intake/`: snapshot inventory, provenance, ledgers, and first-pass report.
- `graphify-out/`, `.qdrant/`, `dump.rdb`: generated or stateful artifacts; inspect only when directly relevant.

## Engineering workflow

- Inspect current Git status before edits. The source snapshot was captured from a dirty worktree; preserve unrelated user changes.
- Verify the requested behavior against current code before designing a change.
- Make a bounded plan before broad, cross-module, security-sensitive, schema, or workflow changes.
- Prefer established deterministic services and reusable authorization loaders over new router-local logic.
- Implement incrementally and keep diffs narrow.
- Preserve existing behavior unless the task explicitly changes it or the behavior violates an agreed invariant.
- Add two-pharmacy negative tests for every tenant-sensitive route or loader.
- Run the smallest evidenced checks that cover the change, then broaden only when justified and prerequisites exist.
- Review the final diff for unrelated churn, unsafe fallbacks, direct status updates, missing tenant predicates, internal commits, and undocumented schema changes.
- Report checks actually run, their results, and checks not run with reasons.

## Domain and safety invariants

- The canonical market/default identity is Iranian: `DEFAULT_IDENTITY_SYSTEM = iranian`, `DEFAULT_LOCALE = fa-IR`.
- Use IRC, NFI/فهرست رسمی دارویی, دارونامه, Tamin, Salamat, Armed Forces, Rial, Toman, Jalali, and استعلام for current Iranian work.
- Treat NDC, NCPDP, PBM, Surescripts, EPCS, PDMP, DEA, NPI, FDB, Medi-Span, CMS, REMS, DIR, and 340B as legacy or future seams unless a human activates them.
- Derive pharmacy scope from the authenticated staff or service principal. Do not trust caller-supplied tenant IDs.
- Validate parent pharmacy ownership before every patient/Rx child read or mutation.
- Authenticate and pharmacy-bind WebSockets, streaming endpoints, and edge ingest.
- Never directly update `Prescription.status`; use `RxStateMachine` and preserve its audit chain.
- Serialize concurrent status/hash-chain transitions with row locks or equivalent protection.
- Clinical AI is advisory. Deterministic facts and validated rules decide verdicts; humans authorize safety-critical actions.
- Do not persist sandbox, degraded, crawler, OCR, audio, biometric, or LLM output as authoritative patient, catalog, coverage, price, or clinical fact without explicit provenance and approval.
- Never replace a failed live insurer lookup with plausible fake demographics.
- Keep authoritative monetary arithmetic in `Decimal` Rial logic and preserve source/effective-date evidence.
- Stage catalog, price, coverage, and enrichment changes for approval unless an explicitly approved policy says otherwise.
- Use Alembic for schema changes. Do not add request-time `CREATE TABLE` behavior.
- An audit column is not an audit event. Write immutable, tenant-bound events for regulated actions.
- Never fabricate payment, claim, persistence, or workflow success after failure.
- Preserve consent, minimum-necessary access, retention, and egress controls for PHI, national codes, biometrics, audio, and AI prompts.

## High-risk areas to inspect before feature work

- `services/platform/routers/identity.py`, `services/biometric/identity_resolution/identity_orchestrator.py`, and `services/integrations/iranian_insurance/adapters.py`: caller-controlled pharmacy scope and sandbox identities that may be persisted.
- `patients.py`, `prescriptions.py`, `adjudication.py`, `analytics.py`, `phase32.py`, `clinical_brain.py`, and `knowledge.py`: missing tenant predicates on child or clinical-context access.
- `rx_documents.py`, `rx_transcription.py`, and `dur_overrides.py`: dynamic schema, missing pharmacy columns, spoofable actor fields, and unscoped records.
- Rx, biometric, and security WebSockets plus audio, biometric, and behavior-detection ingest: absent authentication or tenant binding.
- `pos.py` and `backoffice_agents.py`: direct prescription status updates; POS also references an unmodeled `adjudication_results` table and uses global payment records.
- `cds.py:acknowledge_interactions`: does not validate the submitted findings against current server findings or enforce a transition prerequisite.
- `pricing.py:/coverage/import`: directly applies coverage, bypassing staged approval. `coverage_harvest.apply_run` clears at most 50 removed entries.
- `AIProviderRegistry`: custom routing and `force_provider` bypass PHI filtering; settings and audit state are global/process-local.
- Knowledge ingestion: arbitrary URL crawling, server filesystem paths, SQLite paths, and pasted session cookies require SSRF, path, secret, licensing, and authorization controls.
- `shared/models/__init__.py`: omits `drug_price_proposal.py`; knowledge ORM models live outside the shared registry. Schema parity currently xfails drift.
- Mobile: stores username/password for biometric re-login, uses staff auth, treats staff ID as patient ID, and calls missing endpoints.
- Ignored E2E artifacts in the snapshot include failed-run state and an auth-state file with bearer/refresh material. Do not expose those values.

## Declared verification commands

These commands are evidenced in CI or package manifests. Their presence does not mean they currently pass. Run them only when dependencies and safe prerequisites are available.

Backend CI lint and format:

- `ruff check services/ shared/ tests/ --select E,F,W --ignore E501`
- `ruff format --check services/ shared/ tests/`

Backend CI tests:

- `PYTHONPATH=. pytest tests/unit/ tests/clinical_validation/ -v --tb=short --cov=services --cov=shared --cov-report=xml --cov-fail-under=60`

Workstation, from `frontend/workstation/`:

- `npx tsc --noEmit`
- `npm run build`
- `npm run lint`
- `npm run test:e2e`

Admin, from `frontend/admin/`:

- `npm run build`
- `npm run lint`

Mobile, from `mobile/patient_app/`:

- `npm run type-check`

Do not claim integration coverage: CI invokes a missing `tests/integration/` directory with `|| true`. Migration/schema tests require a disposable PostgreSQL database and must never target real data.

## Forbidden actions without explicit user authorization

- Do not install dependencies, access the network, start services, execute broad scripts, crawl sources, or run migrations against a real database.
- Do not commit credentials, tokens, session cookies, PHI, national codes, biometric evidence, or audio.
- Do not use destructive Git or filesystem commands.
- Do not bypass tenant checks, clinical gates, audit events, proposal approval, or source provenance for convenience.
- Do not interpret a roadmap checkbox, test count, ignored artifact, UI rendering, or model docstring as proof of working behavior.

## Completion criteria

A task is complete when:

- relevant code, models, migrations, and tests were inspected;
- the implementation is bounded to the request;
- tenant isolation, PHI controls, audit, deterministic clinical behavior, and Iran-specific domain invariants are preserved;
- targeted checks were run or a precise reason is given for not running them;
- the diff was reviewed for unrelated churn and invariant violations;
- user-visible failures are truthful rather than synthetic success;
- durable context was updated when architecture, behavior, commands, terminology, data authority, or safety rules changed.

Update `docs/ai-context/CODEX_PROJECT_BIBLE.md`, this file, `docs/ROADMAP.md`, or a task-specific design note when the change would otherwise force future agents to rediscover an architectural decision.
