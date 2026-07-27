# Codex Project Deep Dive

## 1. Executive Understanding And Product Thesis

PharmPilot AI is an Iranian pharmacy operations platform: a pharmacist workstation plus admin and mobile surfaces, backed by a FastAPI service for prescription intake, DUR/CDS, pricing/coverage, inventory, claims, identity, biometric/audio workflows, and offline-first “intelligent services.”

The product thesis is clear: keep core pharmacy operations deterministic and auditable, then layer AI as assistive prose, triage, summarization, or decision support. This is explicitly stated in `README.md`, `DAY1_RUNBOOK.md`, `docs/ROADMAP.md`, and `docs/MASTER_PROMPT_intelligent_services.md`, and is partly reflected in code through deterministic pricing, state machines, rule-based clinical checks, hash-chain Rx events, and local-first intelligence envelopes.

Confidence: high for static architecture and implementation shape; medium for actual runtime health because the mission prohibited running code, services, installs, tests, or lifecycle scripts.

## 2. Current Maturity And Actually Implemented

The backend is broad and substantially implemented. `services/platform/main.py` wires roughly 40 router groups under `/api/v1`, including auth, patients, prescriptions, claims, pricing, inventory, clinical/CDS, AI intelligence, depot transfer, procurement, package verification, POS, and audit/security surfaces.

The strongest implemented areas are:

- FastAPI composition root, settings, async SQLAlchemy, Alembic-on-boot.
- Staff auth, RBAC, sessions, JWT refresh flow.
- Prescription lifecycle state machine with event hash chaining.
- Clinical CDS and interaction-report workflows.
- Iranian drug catalog, coverage, crosswalk, enrichment, and deterministic pricing engine.
- Workstation shell with many operational panels.
- Unit-level tests around clinical modules, pricing/catalog, identity/auth, migrations, and tenant behavior.

The platform is not production-complete. `docs/ROADMAP.md` is more candid than `README.md`: real national data ingestion is partial, insurer integrations are not complete, fulfillment/POS/labels/inventory decrement are incomplete, CI/deploy hardening remains, and frontend type/e2e health is questionable.

## 3. Repository Map And Technology Stack

Inventory evidence from `.codex-intake/REPOSITORY_INVENTORY.md`:

- 1,888 files copied.
- 571 probable source files.
- 135 tests.
- 85 docs.
- Major roots: `services/`, `shared/`, `frontend/`, `mobile/`, `tests/`, `data/migrations/`, `docs/`, `.github/`, `.qdrant/`, `graphify-out/`.

Core stack:

- Backend: Python 3.11, FastAPI, async SQLAlchemy, Alembic, Pydantic, Redis, Postgres, optional Kafka/ClickHouse/Qdrant/Neo4j.
- Frontend workstation/admin: React 19, Vite 8, TypeScript 6, Tailwind/CSS, TanStack Query, Zustand, axios, lucide icons.
- Mobile: Expo Router / React Native, SecureStore, TanStack Query.
- ML/AI dependencies declared: numpy, pandas, sklearn, xgboost, torch, transformers, FAISS, Prophet, Google generative AI, OpenCV, speech libraries.
- Ops: Docker Compose, GitHub Actions, Terraform skeleton, Prometheus/Grafana references.

Important mismatch: `pyproject.toml` lacks a lockfile and appears to omit packages imported or installed in CI, including `anthropic`, `openai`, `qdrant-client`, `neo4j`, and `email-validator`.

## 4. Runtime Architecture And Data Flows

Backend composition:

- `services/platform/main.py` creates the FastAPI app, runs optional Alembic migrations on boot through `services/platform/migrations.py`, configures CORS/GZip/metrics, and includes all routers.
- `services/platform/config.py` centralizes settings: DB, Redis, Kafka, Qdrant, AI keys, CORS, identity defaults, Iranian insurer settings, and development defaults.
- `services/platform/database.py` provides async DB sessions and commits on successful request completion.

Prescription flow:

1. Workstation calls `/api/v1/prescriptions/intake`.
2. `routers/prescriptions.py` creates the Rx and transitions it to `PENDING_DUR`.
3. DUR/CDS background analysis is queued/precomputed.
4. State transitions go through `services/core/pharmacy_workflow/state_machine.py`.
5. `RxStateEvent` records form a hash chain.
6. Later transitions create fills and advance toward verification, adjudication, filling, will-call, dispensed, or exception states.

Pricing flow:

1. `/api/v1/pricing/quote` resolves catalog item by IRC/name.
2. Coverage source and insurer plan data are loaded.
3. `services/core/pricing_ir/engine.py` computes gross, covered base, insurer share, patient share, differential, VAT/fees.
4. Crosswalk/enrichment/proposal routes support source reconciliation and catalog improvement.

Clinical flow:

1. `/api/v1/cds/evaluate` builds patient context from patient, allergies, labs, active meds/Rx.
2. Deterministic rules in `services/ai/clinical_decision_support/` produce findings.
3. Alerts and audit logs are persisted.
4. Interaction reports, acknowledgements, revisions, and physician letters have their own audited flows.

Offline intelligence flow:

- `services/ai/intelligence_core/tier_resolver.py` resolves local/cloud/hybrid tier and emits a universal envelope with degradation metadata.
- Many intelligence modules are heuristic or rules-first, with cloud synthesis optional.
- This matches the product intent, but not all documented “model” claims are backed by actual trained-model code.

## 5. Module Catalog And Status

| Area | Status | Evidence |
|---|---:|---|
| Platform app/router registry | Implemented | `services/platform/main.py` |
| Settings/config | Implemented, risky defaults | `services/platform/config.py` |
| Alembic migrations | Implemented | `data/migrations/versions/0001...0024`, `services/platform/migrations.py` |
| Staff auth/RBAC/session JWT | Partial | Implemented, but refresh/session semantics and permissions need review |
| Patient CRUD/search | Partial | Core tenant-scoped; child routes have tenant-scope gaps |
| Prescription intake/queue/state | Partial | State machine implemented; fulfillment/POS/inventory decrement incomplete |
| Claims/adjudication | Partial | Present, but insurer/live integrations not production-complete |
| Iranian pricing/catalog/coverage | Substantial partial | Quote/import/crosswalk/enrichment implemented; national data still partial |
| Inventory/procurement/depot | Partial | Models/routes exist; operational completeness unclear |
| CDS basic rules | Implemented but limited | Six direct rules in basic engine |
| Interaction report/audit/bundle | Implemented | Richer interaction engine, cache, bundle install/status |
| ADR/counselling/PGx/polypharmacy/lab safety/med reconciliation | Implemented to varying depth | Routers and tests exist; depth varies by service |
| Drug intelligence/monograph | Implemented/partial | Router and service present |
| Knowledge/RAG/Qdrant | Partial | Qdrant state present; SQL knowledge models lack matching migration evidence |
| AI workflow copilot | Partial | Decision-aid heavy; limited auto-execution |
| Biometric/audio/security | Partial, high-risk | Feature routes exist; auth boundaries need audit |
| Workstation frontend | Substantial partial | Main operational shell and many panels |
| Admin frontend | Stub-heavy | Overview real; most sections “backend ready” placeholder |
| Mobile app | Stub/partial | Staff-auth based, calls missing patient endpoints |
| CI/CD | Partial | Workflows exist; some jobs likely broken or non-gating |
| Docker/Terraform | Partial/broken | Missing referenced Dockerfiles/prometheus/modules |

## 6. Frontend Information Architecture

Workstation:

- Entry: `frontend/workstation/src/App.tsx`.
- Auth gate uses `localStorage.access_token`.
- Main workstation layout has three columns: `RxQueue`, `VerificationCenter`, `PatientPanel`.
- `DashboardShell.tsx` adds a larger operational console with sections including command, inventory, clinical, CDS, ADR, counselling, physician message, second brain, drug intelligence, polypharmacy, PGx, lab safety, med reconciliation, security, financial, adherence, AI hub, knowledge, depot, interaction audit, interaction bundle, price proposals, drug catalog, and coverage.
- No URL router was found; section navigation is local React state.
- `frontend/workstation/src/lib/api.ts` centralizes axios, JWT injection, refresh handling, and typed API calls.
- `frontend/workstation/src/stores/rxQueue.ts` manages queue, selected Rx/patient, biometric arrivals, and websockets.

Admin:

- `frontend/admin/src/App.tsx` has a sidebar and overview dashboard.
- Most admin sections render placeholder copy such as “Frontend rendering phase — backend ready.”
- CSS still resembles Vite starter constraints, which likely conflicts with production admin layout expectations.

Mobile:

- Expo Router with auth login and tabs for prescriptions, medications, messages, profile.
- Uses staff auth endpoints rather than true patient auth.
- Calls several backend endpoints that were not found, including refill request, notification token, patient messages, and patient medications.

## 7. Backend, Data Model, APIs, Integrations, Auth

Key table groups in `shared/models/`:

- Auth: `staff`, `staff_sessions`.
- Patient: `patients`, `patient_allergies`, `lab_results`, `clinical_notes`.
- Clinical: `medications`, `genotype_results`, `clinical_alerts`, `clinical_audit_logs`, `interaction_reports`, `physician_letters`.
- Rx: `prescriptions`, `prescription_fills`, `dur_alerts`, `rx_state_events`, `label_events`.
- Inventory: `drug_products`, `inventory_lots`, `stock_levels`, `purchase_orders`, `receiving_records`, `inventory_movements`.
- Pricing/catalog: drug catalog, coverage sources/runs, crosswalk entries, field overrides, proposals, enrichment, price history.
- Claims, pharmacy, prescriber, biometric, audio, depot.

Auth and permissions:

- Roles include pharmacist, technician, intern, cashier, manager, inventory_staff, super_admin.
- `super_admin` has `*`.
- Pharmacist has clinical permissions; manager notably lacks `clinical:read/write`.
- Access tokens carry `sub`, `pharmacy_id`, `role`, `jti`, `type`.
- SSE tickets are short-lived JWTs.
- Refresh flow appears to create session expiry using access-token duration rather than refresh-token duration.
- Logout revokes all active sessions for a staff user, not only the current token.

Integration posture:

- External integrations are mostly configured as optional/sandbox: Surescripts, FDB/Medi-Span, wholesalers, insurer APIs, OpenAI/Anthropic, Qdrant, Neo4j.
- `docker-compose.yml` includes many services but references missing or inconsistent pieces.

## 8. AI, Automation, Domain Capabilities, And Safety Boundaries

Confirmed safety patterns:

- Clinical modules are deterministic-first and return degraded/local results when richer context is unavailable.
- CDS and physician-letter flows persist audit logs.
- Physician letter generation uses placeholder substitution and review/audit concepts.
- Analytics SQL generation has a guardrail layer: SELECT/WITH only, no multiple statements/comments, forbidden keywords, PHI-column exclusions, table whitelist, enforced `pharmacy_id`.

Gaps:

- Several docs describe trained local models, nightly training, CNN/RDKit-style capabilities, or ModelStore behavior that are not evidenced in implementation.
- Rx copilot auto-execution is narrow; many actions are recommendations or simulations.
- Some automation routes and websockets have weak or missing authentication boundaries.
- “Offline-first” is broadly implemented as envelopes and heuristics, but not all modules have equivalent local data depth.

## 9. Build, Test, Lint, Typecheck, Dev, Deploy Workflows

Evidenced commands only:

- Backend docs mention local Postgres plus `scripts/dev.sh`.
- `pyproject.toml` exposes Poetry-managed backend dependencies.
- Workstation scripts: `npm run dev`, `build`, `lint`, `preview`, `test:e2e`.
- Admin scripts: `npm run dev`, `build`, `lint`, `preview`.
- Mobile scripts: Expo start/android/ios/eas/type-check.
- GitHub CI runs ruff, pytest with coverage, workstation typecheck/build, bandit, and an integration job.
- Deploy workflow builds/pushes Docker images, builds frontend, syncs to S3/CloudFront, deploys ECS, and performs health check.

Not run: tests, typechecks, builds, app imports, services, dependency installs, package scripts. This was prohibited by the mission.

Workflow risks:

- CI includes `pytest tests/integration/ ... || true`, making integration failures non-gating.
- Some GitHub expression syntax for repeated hex strings appears invalid.
- Frontend deploy appears parallel to tests rather than fully gated.
- Docker Compose references missing files/services.
- API port differs across evidence: compose uses 8000, docs/frontend default to 8001.

## 10. Documentation-To-Code Contradiction Matrix

| Claim | Conflicting Evidence | Assessment |
|---|---|---|
| `README.md` claims `402 passed, 1 skipped` and clean frontend typecheck | `docs/ROADMAP.md` says baseline frontend tsc errors remain; Playwright artifacts show failures | Stale/overstated |
| Day 1 runbook says controlled lifecycle/POS flow verified | Roadmap says reception-to-fill handoff, inventory decrement, POS, label final step incomplete | Overstated |
| MASTER_PROMPT endpoint layout | Actual routers group many routes under `/intelligence/workflow/*`, `/intelligence/clinical/*`, `/intelligence/inventory/*` | Spec drift |
| “14 intelligent services” with local models/training | Code mostly uses heuristics/rules; no clear ModelStore training pipeline | Overstated |
| Outbox as designed persistent modeled component | Actual outbox uses raw table creation; no Alembic/ORM evidence | Implementation drift |
| Superpower docs marked pre-implementation | Many related modules now exist | Stale docs |
| “Conversation AI” UI/test expectation | Actual dashboard uses Counselling; `AudioIntelligence` appears unmounted | Stale frontend expectation |
| Mobile patient app | Uses staff auth and missing patient endpoints | Product/API mismatch |
| Docker Compose as full local topology | Missing Dockerfiles, prometheus config, celery app/module refs | Broken/incomplete |
| README says modules work without LLM credentials | Runbook says Anthropic required for some “ACB brain” behavior | Needs distinction between deterministic result and cloud enrichment |

## 11. Risks

Security/privacy:

- Tenant isolation gaps in patient child resources and some Rx subroutes.
- Unauthenticated or weakly authenticated websocket/media/biometric/security endpoints.
- Mobile stores raw username/password for biometric re-login.
- Development defaults for secrets and sandbox integration settings need production hardening.
- Analytics LLM-to-SQL deserves adversarial testing despite existing guardrails.

Correctness:

- Rx `DISPENSED` transition logs inventory deduction but does not clearly decrement inventory.
- POS/payment collection can be bypassed or is not integrated into the main flow.
- Refresh-token/session expiry mismatch may produce surprising auth behavior.
- Iranian insurer fee/franchise/special-population rules are explicitly marked for verification.

Maintainability:

- Documentation has diverged significantly from code.
- Admin/mobile are much less mature than workstation/backend.
- Dependency manifests and CI install lists are inconsistent.
- Docker/Terraform give a false sense of deployability.

Performance/operations:

- Queue websocket polls every 3 seconds.
- AI/cloud tier probing and optional integrations need production timeout/circuit-breaker review.
- Observability exists in dependencies/config but production dashboards and alerting are not evidenced as complete.

## 12. Prioritized Continuation Roadmap

1. Stabilize truth source.
   - Update README/runbook to match `docs/ROADMAP.md`.
   - Create a short “current implementation contract” for backend/frontend/mobile.

2. Fix security boundaries before feature growth.
   - Audit tenant scoping for all patient/Rx child routes.
   - Require auth for websockets, biometric, audio, and security routes.
   - Fix refresh-token expiry/session semantics.
   - Remove mobile password storage.

3. Make the prescription fulfillment path real end to end.
   - Reception/intake → DUR → verification → adjudication → fill → payment → label → dispense.
   - Wire inventory decrement, POS requirement, label event, and audit trail into state transitions.

4. Reconcile build/runtime.
   - Fix Python dependency manifest and lock strategy.
   - Repair Docker Compose or mark it unsupported.
   - Align API ports.
   - Make CI integration tests meaningful and gating.

5. Frontend integration pass.
   - Replace admin placeholders or hide unfinished sections.
   - Mount only complete workstation sections or label internal ones.
   - Fix stale Playwright selectors and run against the actual shell.

6. National data and insurer hardening.
   - Finish NFI/IRC validation, Tamin/Salamat/armed forces source reconciliation, coverage provenance, tariffs, and special population rules.
   - Add regression fixtures for Iranian pricing.

Suggested first implementation slice: tenant/auth hardening plus prescription child-route scoping. It has the highest safety value, is locally bounded, and reduces risk before continuing product work.

## 13. Unknowns And Human Decisions

- Which documentation is authoritative: README/runbook or roadmap?
- Is the intended first market Iran-only, or should the platform keep US/EPCS/PDMP concepts alive?
- Should admin and mobile be production surfaces now, or deferred shells?
- What insurer integrations are contractually available versus simulated?
- Should Docker Compose be repaired as the official local stack, or replaced by a smaller dev setup?
- Which AI claims are product commitments versus exploratory prototypes?
- What is the required PHI/privacy compliance target?

## 14. Coverage Ledger

Examined directly or via bounded subagent investigation:

- `.codex-intake/REPOSITORY_INVENTORY.md`, `DOCUMENT_LEDGER.txt`, `SOURCE_STATE.md`.
- Root README/runbook/roadmap/master prompt.
- Root manifests, env template, Docker Compose, pyproject, CI/CD workflows.
- Alembic migrations and model groups.
- Backend composition root, config, DB, auth, state machine, prescription/patient/pricing/CDS/intelligence routes.
- Representative AI/clinical/pricing/intelligence services.
- Workstation, admin, and mobile entry points, API clients, stores, routes/screens.
- Tests inventory and representative unit/e2e artifacts.

Not fully read line-by-line:

- Generated `graphify-out/**/GRAPH_REPORT.md` corpus.
- `.claude/graphify` tooling docs.
- Some long historical plan/spec docs under `docs/superpowers/`; they were sampled and compared by product/contradiction investigation rather than exhaustively transcribed.
- Full frontend component tree and every test file; representative/high-centrality files were inspected.

## 15. Handoff Checklist

- Start with `docs/ROADMAP.md`; treat `README.md` and `DAY1_RUNBOOK.md` as partially stale.
- Read `services/platform/main.py`, `config.py`, `auth.py`, `routers/prescriptions.py`, `routers/patients.py`, and `services/core/pharmacy_workflow/state_machine.py` before touching workflows.
- For clinical work, inspect `services/ai/clinical_decision_support/` and existing tests first.
- For pricing/Iran work, inspect `services/core/pricing_ir/`, `shared/models/drug_catalog.py`, `coverage.py`, and `crosswalk.py`.
- For frontend work, start at `frontend/workstation/src/App.tsx`, `DashboardShell.tsx`, `lib/api.ts`, and `stores/rxQueue.ts`.
- Do not assume admin/mobile are complete.
- Before adding features, fix tenant scoping and auth boundaries.
- Before relying on CI/dev stack, reconcile dependencies, ports, Docker Compose, and workflow gates.