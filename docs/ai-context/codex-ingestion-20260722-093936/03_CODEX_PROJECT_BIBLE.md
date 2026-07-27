# Project Bible

## Last validated against snapshot

Confirmed by read-only inspection on 2026-07-22. Snapshot root: `/var/folders/tf/56bc3lwd54gdbld5hck5_56r0000gn/T/codex-project-ingestion.20260722-093936.W5izgf/repository-snapshot`.

Source intake says the original repo was captured at `2026-07-22T05:09:37Z` from `/Users/sashad85/PharmPilot-Claude`, branch `feat/darunameh-crawler`, commit `db4eee3aa5915a4736a302321933712d531894aa`, with a dirty working tree. The isolated snapshot's local `.git` metadata differs (`master`, `ddcf0cdc58998833f501266970c94b2fdae06759`), so future work should treat `.codex-intake/SOURCE_STATE.md` as the takeover provenance record.

No files were modified. No dependencies were installed. No app code, project scripts, tests, services, network calls, or builds were run. Evidence came from safe file reads and `rg`/`find`/`git status` style inspection only.

## Canonical platform description

Confirmed: PharmPilot AI is an Iran-first pharmacy operations and medication-intelligence platform. It combines a pharmacist dispensing workstation, a broad FastAPI backend, deterministic prescription/pricing/clinical workflows, advisory AI modules, Iranian drug catalog and insurer-coverage tooling, and partial admin/mobile shells.

Inference: The near-term product center is not generic US pharmacy SaaS. Current code and recent commits emphasize Iranian NFI/IRC catalog ingestion, دارونامه coverage import/harvest, Tamin/Salamat/armed-forces coverage reconciliation, price history, enrichment, and durable crosswalk decisions. US concepts such as NCPDP, Surescripts, EPCS, PDMP, FDB, and Medi-Span remain in code/docs as legacy or future integration concepts, but should not be allowed to define the primary product language unless a human decides to keep them.

Recommended canonical shorthand: `Iran-first pharmacy operating system with deterministic dispensing, pricing, coverage, catalog, and advisory clinical intelligence.`

## Primary source-of-truth files

Confirmed high-value read-first paths:

- `.codex-intake/SOURCE_STATE.md`: source capture provenance and dirty-tree status.
- `.codex-intake/CODEX_PROJECT_DEEP_DIVE.md`: useful first pass, but corrected by this bible.
- `docs/ROADMAP.md`: most candid current product status.
- `README.md` and `DAY1_RUNBOOK.md`: useful intent/runbook, but stale and overstated in test/completeness claims.
- `services/platform/main.py`: FastAPI composition root and router registry.
- `services/platform/config.py`: settings, defaults, ports/integration credentials, identity defaults.
- `services/platform/auth.py` and `services/platform/routers/auth.py`: JWT/session/RBAC implementation.
- `services/platform/routers/prescriptions.py`, `services/core/pharmacy_workflow/state_machine.py`: Rx lifecycle and queue.
- `services/platform/routers/pricing.py`, `services/core/pricing_ir/`, `services/core/drug_catalog/`, `shared/models/drug_catalog.py`, `shared/models/coverage.py`, `shared/models/crosswalk.py`, `shared/models/price_history.py`: Iranian catalog/pricing/coverage axis.
- `shared/models/`: ORM data model map.
- `frontend/workstation/src/App.tsx`, `frontend/workstation/src/DashboardShell.tsx`, `frontend/workstation/src/lib/api.ts`, `frontend/workstation/src/stores/rxQueue.ts`: workstation shell and API contracts.
- `frontend/admin/src/App.tsx`: admin maturity check.
- `mobile/patient_app/src/api/client.ts`, `mobile/patient_app/src/store/auth.ts`: mobile/API mismatch and security posture.

## Snapshot coverage

Confirmed by intake ledgers:

- `.codex-intake/REPOSITORY_INVENTORY.md`: 1,888 files, 571 probable source files, 135 test-related files, 85 docs.
- `tests/` itself contains 94 files in this snapshot; intake's 135 count includes test-related artifacts such as Playwright reports/screenshots.
- Major roots: `services/`, `shared/`, `frontend/`, `mobile/`, `tests/`, `data/migrations/`, `docs/`, `.github/`, `.qdrant/`, `graphify-out/`, `tamin/`.
- No existing `AGENTS.md` or `docs/ai-context/` files were found in the snapshot.

## Architecture

Confirmed backend stack:

- Python 3.11 declared in `pyproject.toml`; GitHub CI uses Python 3.12.
- FastAPI app in `services/platform/main.py` with async SQLAlchemy sessions from `services/platform/database.py`.
- Alembic migrations under `data/migrations/versions/0001...0024`; chain appears linear by static inspection.
- PostgreSQL, Redis, Kafka, Qdrant, Neo4j, ClickHouse, Prometheus/Grafana are referenced; not all are reliable local topology.
- `services/platform/main.py` imports model classes up front so SQLAlchemy string relationships resolve before serving requests.

Confirmed frontend/mobile stack:

- Workstation: React 19, Vite 8, TypeScript 6, TanStack Query, Zustand, axios, lucide, many operational dashboards.
- Admin: React/Vite shell with one overview section and placeholder sections.
- Mobile: Expo Router / React Native app labeled patient-facing, but it uses staff auth endpoints.

## Core data flows

### Rx flow

Confirmed:

1. `POST /api/v1/prescriptions` in `services/platform/routers/prescriptions.py` creates a `Prescription` and immediately transitions from `intake` to `pending_dur`.
2. `RxStateMachine.transition()` in `services/core/pharmacy_workflow/state_machine.py` enforces allowed transitions and writes `RxStateEvent` rows with SHA-256 hash chaining.
3. Claim/release moves prescriptions through `pending_verification` and `verification_in_progress`.
4. Transition to `pending_adjudication` creates a `PrescriptionFill` row if missing.
5. Transition side effects update fill dates/refills and log inventory deduction intent, but do not clearly decrement inventory.
6. `GET /api/v1/prescriptions` uses raw SQL, tenant-scopes by `staff.pharmacy_id`, and can return active queue or patient history.
7. `GET /api/v1/prescriptions/{rx_id}/history`, `/dur-alerts`, and `/fills` lack visible pharmacy scoping in their direct queries.
8. `websocket /api/v1/prescriptions/queue/ws/{pharmacy_id}` accepts a path pharmacy id and has no auth dependency.

### Pricing/catalog/coverage flow

Confirmed:

1. `POST /api/v1/pricing/quote` resolves lines by IRC/name through `services/core/drug_catalog/repo.py`.
2. Effective price is built from catalog records; `services/core/pricing_ir/engine.py` deterministically computes gross, insurer share, patient share, differential, VAT, and technical fee splits using `Decimal`.
3. Coverage JSON and optional eligibility provider can override local coverage facts.
4. Same-ingredient alternatives are returned for affordability swaps.
5. `services/platform/routers/pricing.py` also exposes NFI harvest/import, coverage source/harvest/run review, Tamin harvest, inconsistencies, crosswalk/override, enrichment, price-history, and match-intelligence endpoints.
6. `data/migrations/versions/0016` through `0024` are the catalog/pricing/enrichment/crosswalk migration sequence.

### Clinical/intelligence flow

Confirmed:

- Basic CDS in `services/ai/clinical_decision_support/engine.py` evaluates exactly six direct rules from `rules.py`: clarithromycin/simvastatin, ACEI/spironolactone potassium, NSAID/anticoagulant, metformin/eGFR, benzodiazepine/elderly, SSRI/tramadol.
- Broader modules exist for ADR, counselling, lab safety, physician message, PGx, polypharmacy, med reconciliation, second brain, drug intelligence, interaction reports, and physician letters.
- `services/ai/intelligence_core/tier_resolver.py` implements a local/cloud/hybrid envelope pattern and fail-closed-to-local behavior.
- `services/ai/intelligence_core/local_llm.py` scrubs identifiers before LLM calls and prefers local embeddings; generation routes through provider registry when available.
- `services/ai/intelligence_services/analytics_qa.py` has an LLM-to-SQL guard: SELECT/WITH only, single statement, no comments, whitelist tables, block PHI columns, enforce limit, bind `pharmacy_id`. Residual risk: some analytics endpoints in `services/platform/routers/analytics.py` accept optional `pharmacy_id` and can query globally for `reports:read` users.

## Module map and implementation status

| Area | Status | Evidence |
|---|---|---|
| FastAPI app/router registry | Implemented/broad | `services/platform/main.py` includes 45 router files under `/api/v1` groups. |
| Settings/config | Implemented with risky defaults | `services/platform/config.py` has development secrets and sandbox integration defaults. |
| Auth/RBAC/session JWT | Partial | `services/platform/auth.py`, `routers/auth.py`; refresh sessions expire at access-token duration, not refresh duration. |
| Rx lifecycle | Partial | State machine and hash-chain events exist; POS, label finalization, inventory decrement not integrated end-to-end. |
| Patient CRUD/search | Partial | Main CRUD tenant-scoped; allergy/insurance/lab/note child routes do not verify parent pharmacy. |
| Claims/adjudication | Partial/sandbox | Router and NCPDP builder exist, but roadmap says live insurer submission is not done. |
| Iranian pricing/catalog/coverage | Substantial partial | Pricing engine, NFI/import, coverage import/harvest, crosswalk, enrichment, price history exist; real national data/receipt validation incomplete. |
| Inventory/procurement/depot | Partial | Models/routes/tests exist; movement/decrement integration with dispense is incomplete. |
| Clinical modules | Mixed | Core endpoints and tests exist; depth varies from deterministic rules to heuristic modules. |
| Offline-first intelligence | Partial | Envelope/tier patterns exist; many services are heuristics or safe stubs, not trained models. |
| Biometric/audio/security | High-risk partial | Routes exist; several ingest/stream/transcript endpoints have missing/weak auth and tenant checks. |
| Workstation UI | Substantial partial | Main workstation plus dashboard shell and many panels; no URL router; WebSockets unauthenticated. |
| Admin UI | Stub-heavy | `frontend/admin/src/App.tsx` renders overview plus `Frontend rendering phase — backend ready` placeholders. |
| Mobile app | Stub/contract mismatch | Calls missing patient endpoints, uses `/auth/login` staff auth, stores saved password for biometric re-login. |
| CI/CD | Partial/untrusted | CI exists but integration tests are non-gating; README test claims conflict with roadmap/artifacts. |
| Docker/Terraform | Partial/broken | Compose references missing `Dockerfile.clinical_brain`, `services.platform.celery_app`, and `infrastructure/prometheus.yml`. |

## Safety and domain invariants

Confirmed invariants in code/docs:

- Clinical AI is intended to be advisory-only; deterministic facts/rules decide verdicts, LLMs draft prose only.
- Patient/PHI data must remain tenant-scoped by `pharmacy_id` and should not leak across pharmacies.
- Rx state transitions should go through `RxStateMachine`; direct status updates would bypass audit hash-chain semantics.
- Price/catalog changes are intended to be manager-approved proposals or durable overrides, not silent destructive imports.
- Iranian pricing arithmetic must remain deterministic and auditable; use `Decimal`/Rial arithmetic in `services/core/pricing_ir/engine.py`.
- For LLM/RAG, prefer retrieval/local fallback and explicit degraded metadata over hard failure or fabricated facts.
- Uploaded files/audio/biometrics/security feeds are untrusted input; treat OCR/crawler/audio/LLM output as untrusted until validated and reviewed.

## Corrections to first-pass report

The first-pass report is broadly useful, but future sessions should apply these corrections:

- Confirmed stronger than the report: biometric/audio/security auth gaps are not just possible. `biometric.identify`, `biometric.enroll`, `biometric.stream`, `audio.transcribe`, `audio.get_patient_transcripts`, `audio.approve_enrichment_action`, `security.behavior-detection`, and `security.stream` lack normal authenticated staff dependencies in inspected code.
- Confirmed stronger than the report: patient child route tenant gaps are concrete in `services/platform/routers/patients.py`; allergy/insurance/lab/note routes query by `patient_id` without parent pharmacy check.
- Clarified: `tests/unit/test_alembic_schema_parity.py` exists, but schema drift is `pytest.xfail(...)`, so parity is diagnostic, not a hard gate.
- Clarified: the snapshot has 94 files under `tests/`; intake's 135 is broader test-related coverage including e2e artifacts.
- Clarified: `README.md` and `DAY1_RUNBOOK.md` claims such as `402 passed, 1 skipped`, clean frontend typecheck, and verified lifecycle are stale relative to `docs/ROADMAP.md` and Playwright failure artifacts.
- Clarified: the app defaults are inconsistent: scripts/frontend/CI default API to `8001`, while `docker-compose.yml` exposes API on `8000`.
- Clarified: `docker-compose.yml` is not authoritative as-is; it references missing files/modules.

## Declared commands and verification status

These commands are declared in manifests or CI, but were not executed in this audit:

- Backend CI unit command: `PYTHONPATH=. pytest tests/unit/ tests/clinical_validation/ -v --tb=short --cov=services --cov=shared --cov-report=xml --cov-fail-under=60` from `.github/workflows/ci.yml`.
- Backend CI lint command: `ruff check services/ shared/ tests/ --select E,F,W --ignore E501` and `ruff format --check services/ shared/ tests/` from `.github/workflows/ci.yml`.
- Workstation: `npm run dev`, `npm run build`, `npm run lint`, `npm run preview`, `npm run test:e2e` from `frontend/workstation/package.json`.
- Admin: `npm run dev`, `npm run build`, `npm run lint`, `npm run preview` from `frontend/admin/package.json`.
- Mobile: `npm run start`, `npm run android`, `npm run ios`, `npm run type-check` from `mobile/patient_app/package.json`.

Do not repeat README's passing-test claims unless verified in the current workspace.

## Decisions to preserve until humans override

- Canonical market/default identity is Iranian: `DEFAULT_IDENTITY_SYSTEM = "iranian"`, `DEFAULT_LOCALE = "fa-IR"` in `services/platform/config.py`.
- National drug/catalog work should use IRC/NFI terminology and Persian insurer workflows, while legacy US concepts should be labeled as legacy/future integration seams.
- Treat `docs/ROADMAP.md` as more current than `README.md` and `DAY1_RUNBOOK.md` for maturity/status.
- Security/tenant fixes should come before expanding features.
- Durable context should live in `AGENTS.md` and `docs/ai-context/CODEX_PROJECT_BIBLE.md` when write access is available.

## Known unknowns

- Which compliance target applies: Iranian health-data law, HIPAA-like controls, both, or a pilot-only internal standard.
- Which insurer integrations and NFI feeds are contractually available versus simulated/crawled.
- Whether US EPCS/PDMP/NCPDP concepts should remain first-class or be deprecated behind optional adapters.
- Whether admin and mobile are expected production surfaces in the next milestone or should be hidden/deferred.
- Which local dev topology is official: scripts on ports `8001/3001`, Docker Compose on `8000`, or something else.
- Whether `.qdrant/` and generated `graphify-out/` should remain in repository snapshots or be treated as generated artifacts.
