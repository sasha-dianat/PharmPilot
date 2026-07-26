# Codex Project Deep Dive

> Static, analysis-only review of snapshot `aeddb87` on `feat/darunameh-crawler`, captured 2026-07-22. No files were modified. No dependencies, application code, services, migrations, builds, linters, typechecks, tests, or network operations were executed.
>
> Evidence levels: **confirmed** means directly supported by repository content; **strong inference** means multiple code paths support the conclusion but runtime verification is absent; **unknown** means a human, external system, or authorized execution is required.

## 1. Executive understanding and product thesis

PharmPilot AI is currently an **Iran-first pharmacist workstation and pharmacy-operations platform**.

Its core product thesis is to combine:

1. An auditable prescription intake, verification, fill, and dispense workflow.
2. An Iranian medication truth layer built around IRC, NFI, دارونامه, Tamin, Salamat, armed-forces coverage, Rial pricing, and Jalali/Persian identity.
3. Deterministic clinical decision support, with AI limited to advisory prose or structured suggestions.
4. Offline/degraded operational intelligence for pharmacy workflow, inventory, documentation, and reference lookup.

The primary persona is the pharmacist. Managers, technicians, interns, cashiers, and inventory staff exist in `shared/models/auth.py:StaffRole`; reception/front-counter is an implicit workflow persona. The Expo “patient app” does not yet have a valid patient identity or authorization model.

The platform’s strongest foundations are:

- Iranian catalog, coverage-ingestion, price-history, proposal, crosswalk, and canonical-export architecture.
- Deterministic Rial pricing arithmetic.
- Deterministic clinical engines, interaction reports, physician letters, and advisory audit records.
- A broad workstation UI for verification and clinical review.
- A substantial static unit-test corpus.

The platform’s weakest boundaries are:

- Tenant isolation and unauthenticated PHI/biometric/audio/WebSocket surfaces.
- Audit-chain violations in POS and back-office status mutation.
- Missing transactional dispense side effects, especially payment and inventory.
- Live insurer integration and verified Iranian tariff policy.
- Truthful degraded UI states.
- Reproducible deployment, integration testing, and operational readiness.
- Admin and patient-mobile product viability.

**Overall maturity: advanced prototype/pilot foundation, not production-ready.**

The current architecture should be treated as a **FastAPI modular monolith backed by PostgreSQL**, not as the distributed platform implied by Compose and Terraform.

---

## 2. Current maturity and what is actually implemented

| Capability | Status | Evidence-based assessment |
|---|---|---|
| Iranian national catalog | **Partial, strong** | Canonical export contains 39,044 IRC rows, 39,017 from `nfi-harvest`; roadmap estimates roughly 65% of the expected corpus. |
| Current-price export | **Partial, strong** | `data/canonical/manifest.json` reports 32,506 current-price entries. Historical pricing exists in migration `0022`. |
| Coverage ingestion/governance | **Partial, strong** | Staged runs, snapshots, diffs, approve/reject, crosswalks, overrides, and diagnostics exist. Real insurer sources and completeness are not established. |
| Deterministic Iranian pricing | **Implemented engine; policy partial** | `services/core/pricing_ir/engine.py` uses `Decimal`/Rial conservation. Several rates, fees, VAT, inpatient, armed-forces, and special-population rules remain marked for verification. |
| Live Iranian eligibility | **Stubbed/partial** | Pricing registers only `NullEligibilityProvider`. Salamat has a live adapter method in a separate identity abstraction; Tamin, armed-forces, and supplementary live methods are not implemented. |
| Rx state model | **Implemented** | `RxStatus`, transition graph, EPCS checks, and SHA-256 `RxStateEvent` chains exist. |
| End-to-end dispense journey | **Partial** | Reception handoff is inert; payment is unmounted; inventory is not decremented; label/payment/dispense are not atomic; return/reversal/partial-fill paths are incomplete. |
| US adjudication | **Partial legacy seam** | NCPDP/PBM submission code exists, mostly sandbox-oriented. It is not an Iranian e-prescription integration. |
| Deterministic clinical modules | **Implemented but narrow** | CDS, ADR, counselling, lab safety, PGx, polypharmacy, med reconciliation, second brain, drug intelligence, and physician messaging have real engines/routes/tests. Corpus breadth and external validation remain limited. |
| Interaction reporting and letters | **Implemented core** | Curated/inferred DDI engine, cached reports, acknowledgements, physician letters, revision/reprint, audit dashboard, and bundles exist. Server-side acknowledgement enforcement is incomplete. |
| Offline-first intelligence substrate | **Partial** | Typed envelopes, tier resolution, local/cloud routing, outbox, and fourteen service implementations exist, but timing, durability, PHI routing, and cloud integrations do not universally satisfy the documented doctrine. |
| Workstation | **Partial, visually broad** | Verification and patient context are substantial. Many dashboards mix live and fabricated data; several workflow components are unreachable. |
| Admin | **Stubbed** | Overview plus seven construction placeholders; no functional authentication client. |
| Patient mobile | **Stubbed/unsafe** | Uses staff auth as patient auth, stores reusable credentials, and calls missing or mismatched APIs. |
| Audio/biometric/security | **Prototype** | Significant algorithmic code exists, but several entry points are unauthenticated, unscoped, process-local, and non-durable. |
| CI/testing | **Partial** | Large unit suite and some CI gates. Integration, browser, admin, mobile, schema parity, and medium security findings are weak or non-gating. |
| Deployment/IaC | **Documented-only/partial** | Missing Compose files, commands, Terraform modules, port conflicts, and unsafe deploy ordering prevent treating it as runnable production infrastructure. |

Two concrete data-quality observations require investigation before relying on the canonical artifact:

- `data/canonical/catalog.json` contains **254** occurrences of `"share_pct":10070`.
- Canonical `crosswalk.json` and `overrides.json` are empty despite the implemented persistence architecture.

`tamin/tamin_formulary_full.json` contains 2,600 `drug_code` records, but its production authority and relationship to the canonical export are unverified.

---

## 3. Repository map and technology stack

### Snapshot composition

`.codex-intake/REPOSITORY_INVENTORY.md` reports:

- 1,905 copied files.
- 575 source files.
- 86 documentation files.
- 136 test-related files.
- 303 files under `services/`.
- 148 under `frontend/`.
- 95 under `tests/`.
- 37 under `data/`.

Generated/stateful material dominates the raw count:

- `graphify-out/`: 956 files.
- `.qdrant/`: 230 files.

The source worktree was dirty when captured. `.codex-intake/SOURCE_STATE.md` records deleted Claude configuration, modified `data/reference/match_model.json`, and untracked Codex context documents. The required Project Bible and prior deep dive were not captured.

### Main repository areas

| Path | Role |
|---|---|
| `services/platform/` | FastAPI composition, configuration, database lifecycle, auth, migrations, and HTTP routers |
| `services/core/` | Deterministic pharmacy workflow, pricing, catalog, inventory, adjudication, localization, and pharmacy-domain services |
| `services/ai/` | Clinical engines, interaction reporting, knowledge/RAG, provider routing, intelligence services, audio/vision-adjacent code |
| `services/integrations/` | Iranian insurer adapters plus legacy US/FHIR/wholesaler seams |
| `shared/models/` | Eager-imported SQLAlchemy model registry |
| `data/migrations/versions/` | Linear Alembic chain `0001` through `0025` |
| `data/canonical/` | Exported IRC catalog, current prices, crosswalk, and overrides |
| `frontend/workstation/` | Main pharmacist workstation |
| `frontend/admin/` | Chain/admin shell |
| `mobile/patient_app/` | Patient-labelled Expo application |
| `tests/` | Unit, clinical validation, and load tests |
| `frontend/workstation/tests/e2e/` | Playwright tests and committed failed-run artifacts |
| `infrastructure/` | Four Dockerfiles and two Terraform files |
| `scripts/` | Local setup, start, seed, ingestion, and operational scripts |
| `docs/superpowers/` | Historical designs and implementation recipes |
| `graphify-out/`, `.qdrant/` | Generated graph reports and local vector-store state |

### Technology stack

- Backend: Python 3.11 manifest; FastAPI 0.111; Pydantic 2; async SQLAlchemy; Alembic; PostgreSQL.
- Cache/coordination: Redis, but inconsistently injected.
- AI/ML: NumPy, pandas, scikit-learn, XGBoost, PyTorch, transformers, sentence-transformers, FAISS, Prophet.
- Audio/CV: Whisper, PyAudio, librosa, pyannote, OpenCV, InsightFace, ONNX Runtime.
- Knowledge: Qdrant; optional Neo4j code.
- Messaging declarations: Kafka and Celery, not coherently composed.
- Workstation/admin: React 19.2, TypeScript 6, Vite 8, Tailwind 4.
- Workstation state/data: TanStack Query, Zustand, Axios, Recharts/Nivo.
- Mobile: Expo 52, React Native 0.76, React 18, Expo Router, Zustand, TanStack Query, SecureStore.
- Tests: pytest, pytest-asyncio, Playwright, k6 script.
- Deployment declarations: Docker Compose, AWS ECS/S3/CloudFront, Terraform.

Python has no lockfile. Workstation and admin have npm lockfiles; mobile does not.

---

## 4. Runtime architecture and end-to-end data flows

### Actual topology

`services/platform/main.py:create_app` is the sole application composition root. It mounts 48 router modules with approximately 271 route decorators.

The real runtime is:

```text
Workstation/Admin/Mobile
        |
        v
One FastAPI modular monolith
        |
        +--> PostgreSQL: authoritative application state
        +--> optional Redis: leases/cache, usually not injected
        +--> optional Qdrant: knowledge/RAG corpus
        +--> external insurer/AI/reference services when configured
```

Kafka, ClickHouse, Neo4j, Celery, separate audio/biometric/clinical/ML workers, Prometheus, and Grafana are declared but not coherently wired through `create_app`.

### Authentication flow

1. `POST /api/v1/auth/login`.
2. `authenticate_staff` verifies bcrypt credentials and lockout.
3. A `StaffSession` is created with JTI and hashed refresh token.
4. JWT contains staff ID, pharmacy ID, role, JTI, type, and expiry.
5. `get_current_staff` checks token type, session revocation/expiry, active staff, and lockout.
6. `require_permission` applies `ROLE_PERMISSIONS`.

SSE has a sounder browser-auth pattern: `/auth/sse-ticket` mints a 60-second session-bound ticket for EventSource.

Limitations:

- Refresh sessions expire after `ACCESS_TOKEN_EXPIRE_MINUTES`; `REFRESH_TOKEN_EXPIRE_DAYS` is unused.
- Logout revokes all active sessions for the staff member, not just the current JTI.
- Default `SECRET_KEY` is insecure and lacks a production startup guard.
- The workstation stores bearer and refresh tokens in `localStorage`.

### Prescription flow

1. `POST /api/v1/prescriptions` creates a tenant-owned Rx.
2. `RxStateMachine.transition` advances intake to `pending_dur`.
3. A FastAPI background task runs specialist council, triage, and interaction precompute.
4. Workstation queue selects and claims an Rx.
5. `VerificationCenter` retrieves Rx, patient context, fills, DUR, analysis, interactions, and claim information.
6. Generic transition calls drive verification, adjudication, filling, label, and dispense states.
7. Each legitimate state-machine transition writes a hash-chained `RxStateEvent`.

Critical limitations:

- `RxStateMachine` loads by Rx ID, not `(Rx ID, pharmacy_id)`.
- No `SELECT FOR UPDATE` protects state and hash-chain sequencing.
- Routes instantiate `RxStateMachine(db)` without Redis, so documented lease protection is inactive.
- No scheduler was found for expired-lease auto-release.
- Serious-interaction acknowledgement is a UI gate; generic backend transitions do not require the acknowledged findings hash.
- Dispense and return effects only log intended inventory work.
- `services/platform/routers/pos.py` directly executes `UPDATE prescriptions SET status='dispensed'`, bypassing the state machine.
- Back-office code also directly writes `pending_pa`.
- Payment, inventory, claim, label, and audit effects are not one transaction.

### Catalog and coverage flow

1. Harvester/importer obtains NFI or insurer data.
2. Data is parsed using Persian/English aliases.
3. Records are matched by IRC, ingredient key, deterministic/fuzzy linkage, or durable crosswalk decision.
4. Coverage runs retain staged data, unmatched data, diagnostics, and raw snapshots.
5. Owner review approves, rejects, or overrides.
6. Approved data changes global `DrugCatalogItem` records.
7. Price proposals and SCD2 price history preserve changes.
8. Canonical export writes catalog/current prices/crosswalk/overrides.

Strengths:

- External facts are staged instead of automatically applied.
- Crosswalk decisions and field overrides are designed to survive reimport.
- Enrichment suggestions cannot promote canonical facts without approval.
- Match intelligence demotes questionable matches rather than auto-promoting them.

Correctness risk:

- Coverage diff samples cap removed IRCs at 50, while `apply_run(remove_missing=True)` iterates only that sample. A run can report many removals but clear at most 50.

Harvester tasks and locks are process-local, so multi-worker coordination and restart recovery are absent.

### Pricing flow

`POST /api/v1/pricing/quote`:

1. Resolves an IRC/name against the global catalog.
2. Reads current price and coverage.
3. Optionally attempts insurer eligibility.
4. Runs deterministic Rial arithmetic.
5. Returns insurer share, patient share, differential, VAT, fee, and alternatives.

The arithmetic preserves conservation, but:

- Live pricing eligibility is not wired; the registered provider is null.
- Date-pinned quotation is not used despite price-history documentation.
- Deterministic decimals are converted to response floats.
- IRC catalog identity and NDC inventory identity have no canonical neutral product key.

### Clinical and AI flow

Clinical endpoints generally:

1. Verify `clinical:read`.
2. Load pharmacy-scoped patient/Rx context.
3. Run deterministic rules.
4. Optionally ask an LLM to draft narration.
5. Validate output.
6. Persist an explicit `ClinicalAuditLog`.
7. Return an advisory result.

The interaction engine combines curated DDI data, optional DDInter data, PK/PD inference, allergies, conditions, duplications, severity precedence, and suppression logic.

AI intelligence uses tier resolution and typed envelopes, but direct provider registry and AI Hub calls do not universally pass through the same PHI scrubber or timeout boundary.

### Persistence and transaction ownership

- `get_db` commits after a successful request and rolls back on exception.
- Many services and routers also commit internally.
- Dynamic DDL appears in POS, intelligence outbox, Rx documents, and transcription paths.
- Boot-time migrations default to enabled.
- A legacy database with `prescriptions` but no `alembic_version` is stamped directly to head on the assumption that it already matches the schema.
- Multiple production replicas could race boot-time Alembic.

---

## 5. Complete module/section catalog

Statuses use the requested vocabulary.

| Module or surface | Status | Notes |
|---|---|---|
| `services/platform/main.py`, config, DB | **Implemented** | Working modular-monolith foundation; health check is shallow |
| Auth/JWT/RBAC/session | **Implemented** | Strong core, but refresh/logout semantics and route binding need correction |
| Patient root CRUD | **Implemented** | Root routes tenant-scoped |
| Patient allergies/insurance/labs/notes | **Partial** | Authenticated but missing parent pharmacy checks |
| Prescription state machine | **Implemented** | Transition graph and hash chain; concurrency and tenant context incomplete |
| Prescription HTTP workflow | **Partial** | Main reads scoped; several child commands/reads are not |
| Adjudication | **Partial** | US NCPDP/PBM seam, not Iranian submission |
| POS | **Stubbed/unsafe** | Dynamic DDL, global reads, likely schema mismatch, direct status mutation |
| Label engine | **Partial** | Generation/ZPL/print/audit exist; supporting pharmacy call and final workflow incomplete |
| Rx documents/transcription | **Partial** | Dynamic schema creation and inconsistent auth/governance |
| Inventory catalog/stock/PO | **Partial** | Substantial US/NDC model; not transactionally integrated with dispense |
| Inventory movements | **Partial** | Tenant-aware movement model/routes; end-to-end stock truth unverified |
| Depot/shelf transfer | **Partial** | Models, guards, routes, UI, tests; enforcement and vision remain prototype-grade |
| Iranian pricing engine | **Implemented** | Deterministic arithmetic; policy verification incomplete |
| Pricing eligibility | **Stubbed** | Null provider only |
| Drug catalog/import | **Implemented/partial** | Strong implementation, incomplete national corpus |
| Coverage harvesting | **Partial** | Strong staged governance; real sources and removal bug remain |
| Crosswalk/override/canonical export | **Implemented/partial** | Architecture exists; committed canonical crosswalk/override artifacts are empty |
| Price proposals/history | **Implemented** | Human approval and SCD2 history exist |
| Localization/national ID/Jalali | **Implemented** | Iranian helpers exist; patient CRUD defaults still American/English |
| Core billing | **Partial** | US-oriented concepts remain |
| Clinical services/MTM | **Partial** | Implemented engines, not central Iran-first workflow |
| Compounding | **Partial** | Rule scaffolding and coarse placeholder ranges |
| Specialty/prior auth/LTC/EPCS | **Partial** | Mostly legacy US/future seams |
| CDS | **Implemented but narrow** | Deterministic rules, advisory outputs, audit |
| Interaction reports/bundles | **Implemented but narrow** | Strong architecture; small curated corpus and incomplete server enforcement |
| Physician responsibility letters | **Implemented** | PHI-safe placeholders, Persian/English fallback, validation, persistence |
| ADR detective | **Implemented but narrow** | Deterministic assessment plus narration |
| Counselling | **Implemented but narrow** | Structured advisory generator and RTL output |
| Physician message | **Implemented** | Pharmacist-authored content plus bounded narration |
| Polypharmacy | **Implemented but narrow** | Manual/context-driven review |
| Pharmacogenomics | **Implemented but narrow** | Curated interpretation, not comprehensive clinical corpus |
| Lab safety | **Implemented but narrow** | Deterministic assessment |
| Medication reconciliation | **Implemented** | Manual/source reconciliation workflow |
| Second brain | **Partial** | Query UI/service; depends on local/reference state |
| Drug intelligence | **Partial** | Monograph synthesis and UI; corpus/provider dependent |
| Intelligence core | **Partial** | Envelopes/tier/outbox/model store exist; guarantees are inconsistent |
| Fourteen intelligence services | **Partial** | Local logic exists; several live/cloud sources are placeholders |
| Provider registry/AI Hub | **Partial/unsafe** | Broad provider support; PHI routing, durability, secrets, and tenant settings need work |
| Knowledge/RAG | **Partial** | Qdrant ingestion/retrieval exists; corpus and sidecar state unknown |
| Catalog research enrichment | **Partial** | Suggestion-only workflow is safe; provider/runtime state unknown |
| Package verification | **Prototype/partial** | Real engine/UI mixed with demo product state |
| Shelf vision | **Stubbed** | Explicit degraded/manual-confirmation behavior |
| Adherence/pharmacovigilance | **Partial** | Engines exist but are not a complete mounted patient product |
| Audio | **Prototype/unsafe** | Heavy synchronous work and unauthenticated PHI endpoints |
| Biometric identity | **Prototype/unsafe** | Process-memory enrollment and unauthenticated entry points |
| Security/duress/vault | **Partial/unsafe** | Some scoped reads and vault controls; unauthenticated ingest/streams |
| Neo4j graph | **Documented-only/partial** | Clients/schemas exist but are disconnected from composition |
| Iranian insurer identity adapters | **Partial** | Deterministic sandbox; only Salamat has a live override |
| Surescripts/PDMP/US drug DB/wholesaler/FHIR | **Partial legacy seams** | Retain as future integrations, not current Iran product |
| SQLAlchemy models | **Implemented** | Broad model set; tenant enforcement is mostly route responsibility |
| Alembic migrations | **Implemented/partial** | Linear `0001`–`0025`; parity drift is non-gating |
| Workstation | **Partial** | Strong breadth, incomplete lifecycle, misleading fallbacks |
| Admin | **Stubbed** | Overview plus placeholders |
| Patient mobile | **Stubbed/unsafe** | Broken identity and API contract |
| Docker Compose | **Documented-only** | Missing files/process commands and port conflicts |
| Terraform | **Documented-only** | Missing modules and invalid/incomplete wiring |
| Observability | **Stubbed/partial** | Basic `/metrics`; no complete instrumentation, alerting, or readiness |
| Deployment workflow | **Partial/unsafe** | Artifacts can publish before tests; migrations not awaited |

---

## 6. Frontend information architecture

### Workstation composition

`frontend/workstation/src/App.tsx` is a state-driven SPA without URL routing.

The authenticated core is a fixed three-column screen:

- `RxQueue`
- `VerificationCenter`
- `PatientPanel`

`DashboardShell` replaces that view using local section state. React Router is installed but unused, so there are no deep links, history semantics, route guards, or independently addressable screens.

### Dashboard catalog

| Dashboard section | Status |
|---|---|
| Command Center | **Partial** — live query wrapped in fabricated operational defaults |
| Inventory AI | **Partial** — live stock mixed with demo US inventory/intelligence |
| Clinical Intel | **Partial** — real queries plus fabricated population and US REMS content |
| Clinical Assistant/CDS | **Implemented client** |
| ADR Detective | **Implemented client** |
| Counselling | **Implemented client**, dynamic RTL |
| Physician Message | **Implemented client**, dynamic RTL |
| Second Brain | **Implemented client** |
| Drug Intelligence | **Implemented client** |
| Polypharmacy | **Implemented client** |
| Pharmacogenomics | **Implemented client** |
| Lab Safety | **Implemented client** |
| Medication Reconciliation | **Implemented client** |
| Surveillance | **Partial/misleading** — fixed people/map and fabricated critical events |
| Financial Ops | **Partial/misaligned** — live summary mixed with USD/PBM/CMS data |
| Patient Care | **Stubbed/static** — CMS/MTM/CPT and dollar content |
| AI Hub | **Partial** — provider/routing/audit UI; reorder is “coming soon” |
| Knowledge Base | **Implemented client** |
| Depot Restocking | **Partial/substantial UI** |
| Interaction Audit | **Implemented client**, no client-side role gate |
| Interaction Bundle | **Implemented client** |
| Price Proposals | **Implemented Iranian workbench** |
| Drug Catalog | **Implemented Iranian workbench** |
| Insurance Coverage | **Implemented Iranian/RTL workbench** |

Built but unreachable components include:

- `RxIntakeForm`
- `RxScanner`
- `PaymentCollection`
- `AudioIntelligence`
- `InventoryPanel`
- `SOAPDraftPanel`
- `CompoundingFlags`
- `CounselingScorecard`

Important workflow behavior:

- Reception quoting is mounted, but “send to filling” only closes the modal.
- `submission_error` can be treated as approved with a synthetic `PENDING-RECONCILE` identifier.
- Hardcoded adjudication values use dollar-era assumptions.
- Label confirmation can move directly to dispensed.
- `PaymentCollection`, if mounted, fabricates a successful payment after API failure.
- Dictated-note confirmation claims success without persistence.
- No mounted New Rx flow exists.

### Navigation and authorization

- No section-level role/permission filtering.
- Alt+T and Alt+A are advertised but excluded by the shortcut handler.
- Coverage advertises Alt+B, which is consumed by sidebar collapse.
- Search is explicitly “coming soon.”
- Some requests fall back to `demo-pharmacy-id`.
- Rx and biometric WebSockets carry no authentication.

### Admin

`frontend/admin/src/App.tsx` defines Overview, Pharmacies, Staff, Analytics, CMS Stars, Security, Knowledge Base, and Settings.

Only Overview has real rendering. The remaining seven sections are construction placeholders. The Axios client adds no bearer token, while its operational analytics endpoint requires `reports:read`. The claimed chain-wide view conflicts with a backend query scoped to one staff pharmacy.

### Mobile

The patient-labelled app:

- Authenticates through staff `/auth/login` and `/auth/me`.
- Uses `staff_id` as `patientId`.
- Stores username and password for biometric relogin and preserves them on logout.
- Calls missing refill, messages, medication, and notification-token endpoints.
- Sends a comma-separated status value where the backend expects one exact status.
- Has no working patient-history or messaging contract.
- References missing image/sound assets.
- Has no lockfile or `tsconfig.json`.

### UI foundations

Positive elements:

- Strong Iranian catalog/coverage RTL treatment.
- Jalali display and masked national ID in patient context.
- Some navigation semantics, live regions, keyboard shortcuts, and reduced-motion handling.
- Service worker deliberately avoids caching API/PHI responses.

Gaps:

- Fixed-width workstation lacks a robust small-screen layout.
- Global document language remains English.
- No localization framework; Persian and English are manually mixed.
- Shared design tokens have no import sites.
- Offline mutation queue is process-memory only, unused, and clears before asynchronous completion.
- Demo data is frequently indistinguishable from live operational data.
- No component-level accessibility automation or frontend unit tests.

---

## 7. Backend, data model, APIs, integrations, auth, and permissions

### API surface

Major route families mounted under `/api/v1` include:

- `auth`, `patients`, `prescriptions`, `claims`, `inventory`
- `biometric`, `audio`, `security`, `vault`, `identity`
- `clinical`, `clinical-services`, `cds`, `dur`
- `adr`, `counselling`, `physician-message`, `polypharmacy`, `pgx`
- `lab-safety`, `med-reconciliation`, `second-brain`, `drug-intelligence`
- `pricing`, `drug-database`, `labels`, `package-verification`, `pos`
- `knowledge`, `analytics`, `ai`, `ai-settings`
- `rx-documents`, `rx-transcription`
- `intelligence` plus finance, inventory, DUR, prescriber, analytics, docs, label, clinical, workflow, and procurement subrouters.

There is no checked-in static OpenAPI specification. Runtime `/docs` and `/redoc` are disabled in production.

### Data model domains

| Domain | Important tables/models |
|---|---|
| Tenant/auth | `pharmacies`, `staff`, `staff_sessions` |
| Patient/PHI | `patients`, `patient_allergies`, `lab_results`, `clinical_notes`, medications, genotypes |
| Insurance | `insurance_plans`, `patient_insurance` |
| Prescription | `prescriptions`, `prescription_fills`, `dur_alerts`, `rx_state_events`, label events |
| Clinical audit | clinical alerts, clinical audit logs, interaction reports, physician letters |
| Claims | claim transactions, ERA 835, DIR adjustments |
| Inventory | drug products, lots, stock levels, POs, receiving, movements |
| Depot | shelves, placements, replenishment sessions, transfer/surveillance events |
| Iranian catalog | drug catalog, coverage sources/runs, formulary snapshots |
| Governance | crosswalks, durable overrides, price proposals, price history |
| AI enrichment | enrichment items, variants, context |
| Audio/biometric | transcripts, enrichment actions, biometric identities, visits, security events, vault objects |

`shared/models/__init__.py` eagerly imports model modules to satisfy SQLAlchemy relationship resolution. New model modules must be registered there.

`AuditedBase` provides timestamps, attribution, and soft-delete fields. It does **not** automatically emit an immutable audit event for every write.

### Migration state

The static Alembic chain is linear:

- `0001`: initial schema
- `0002`: biometric vault
- `0003`: Iranian identity
- `0004`–`0011`: intake/copilot/CDS/genotype/prescriber additions
- `0012`–`0013`: depot and movements
- `0014`–`0015`: interaction reports and physician letters
- `0016`–`0019`: catalog, price proposals, coverage, diagnostics
- `0020`–`0023`: enrichment and price history/context
- `0024`: crosswalk
- `0025`: formulary snapshots

Schema parity is not a reliable gate: its test skips without PostgreSQL and `xfail`s drift.

### Tenant isolation

Correctly scoped examples:

- Patient root CRUD.
- Queue listing and individual Rx read.
- Rx analysis/reanalysis.
- Many clinical endpoints after loading a pharmacy-scoped parent.
- Security event query/summary.

Confirmed gaps:

- Patient allergies, insurance, labs, and notes do not first establish that the parent patient belongs to the caller’s pharmacy.
- Rx claim, release, transition, DUR override, history, alerts, and fills are inconsistently scoped.
- `RxStateMachine` selects by Rx ID alone.
- Claim submission loads fill/Rx/patient/insurance/prescriber children without a consistent parent-pharmacy boundary.
- Claim history accepts a path pharmacy ID without comparing it to staff pharmacy.
- POS reads Rx and payments without pharmacy constraints.
- Phase 32 council routes fetch Rx/patient by ID without consistent tenant binding.
- Several vault operations authorize by role but do not visibly establish tenant ownership for the identity.
- National catalog/governance data is global; its ownership and permissible editors need an explicit governance model.

### Unauthenticated or unbound surfaces

Release-blocking examples:

- `/prescriptions/queue/ws/{pharmacy_id}`
- `/biometric/identify`
- `/biometric/enroll`
- `/biometric/stream/{pharmacy_id}/{zone}`
- `/audio/transcribe`
- `/audio/transcripts/{patient_id}`
- audio enrichment approval
- `/security/behavior-detection`
- `/security/stream/{pharmacy_id}`
- legacy `/clinical/rx-review`

These routes can expose or mutate PHI, biometric data, transcripts, security events, or arbitrary tenant-selected state.

### Integrations

Iranian:

- Salamat, Tamin, armed-forces, supplementary identity adapters.
- Sandbox data is deterministic and plausible-looking.
- Salamat has live HTTP mapping methods.
- Pricing eligibility is a separate abstraction and remains null.
- Live Iranian claim submission is absent.

Legacy/future:

- NCPDP/PBM adjudication.
- Surescripts.
- PDMP.
- FDB/MedSpan/OpenFDA/RxNorm-style drug data.
- US wholesalers.
- HL7/FHIR.
- EPCS/DEA/NPI/REMS/DIR/CMS constructs.

These should remain explicit future-market seams or be feature-flagged out of the Iran-first UI.

---

## 8. AI, automation, clinical capabilities, and safety boundaries

### Deterministic clinical capabilities

Real engines/routes/tests exist for:

- Clinical decision support.
- Drug interaction review.
- ADR assessment.
- Patient counselling.
- Physician messaging.
- Polypharmacy.
- Pharmacogenomics.
- Lab safety.
- Medication reconciliation.
- Second-brain reference answers.
- Drug monographs.
- Review triage and hard-gate behavior.
- Physician responsibility letters.

These are best described as **implemented, advisory, and clinically bounded**. Unit coverage does not establish regulatory validation or comprehensive drug knowledge.

The curated interaction seed appears small relative to national medication coverage. Unknown drugs can produce no finding without a fully implemented missing-information report.

The interaction cache hash omits some clinically relevant inputs, including patient age and medication recency dates, so meaningful context changes may not invalidate cached results.

### Fourteen intelligence services

`services/ai/intelligence_services/` contains:

- Analytics Q&A.
- Clinical documents.
- Compounding compatibility.
- Counselling quality.
- DUR intelligence.
- Expiry prevention.
- Integrity detection.
- Label simplification.
- Margin optimization.
- Patient trajectory.
- Prescriber enrichment.
- Queue prioritization.
- Rx copilot.
- Supply warning.

Local rule implementations are substantial, but live/cloud inputs remain incomplete in several services:

- National shortage and wholesaler supply feeds.
- Wholesaler expiry signals.
- Live margin price refresh.
- Prescriber registry validation.
- Integrity corroboration.
- Clinically authoritative compounding dose ranges.

### AI safety design

Positive controls:

- Deterministic rules decide verdicts.
- LLM narration is generally advisory.
- `local_llm.generate` performs pattern-based PHI scrubbing.
- Physician-letter generation uses identifier placeholders and local substitution.
- Provider registry encodes PHI sensitivity and provider suitability.
- Typed envelopes include tier, source, confidence, degradation, and audit metadata.
- Catalog AI produces suggestions requiring human approval.

Weaknesses:

- Direct AI Hub/registry calls are not structurally forced through `local_llm`’s PHI scrubber.
- `/ai/query` permits forced provider selection.
- Pharmacy-specific routing can return before full PHI/active-provider filtering.
- Static `has_baa` booleans do not prove an actual deployment/account BAA.
- API keys persist as plaintext JSON under `~/.pharmpilot/ai_provider_settings.json`, albeit mode `0600`.
- Provider audit/cost/routing state is process-memory only.
- Anthropic’s synchronous client is called from async code.
- Ollama paths allow 120-second waits.
- Connectivity probes synchronously try public DNS endpoints.
- Failure envelopes can expose `str(exc)` to clients.
- Sentence-transformer initialization may attempt model acquisition if assets are absent.
- The intelligence outbox has dynamic DDL, global queries, no registered handlers, and no automatic reconciliation loop.

### Audio, biometric, and security automation

These areas contain significant implementation but should be treated as experimental:

- Biometric enrollment and identity maps are process-memory rather than durably loaded from the encrypted model.
- Identification does not reliably populate a patient from durable index mapping.
- Some liveness behavior fails open on dependency errors.
- Audio transcription loads heavy models in request paths.
- Approved audio enrichment can write allergies, labs, or notes.
- Security broadcasts are in-memory and will not span workers.
- Consent, retention, tenant binding, and Iranian regulatory requirements remain undecided.

---

## 9. Build, test, lint, typecheck, development, and deployment workflows

These are **declared workflows only**. None were executed during this review.

### Evidenced checks

| Area | Declared command |
|---|---|
| Backend lint | `ruff check services/ shared/ tests/ --select E,F,W --ignore E501` |
| Backend format | `ruff format --check services/ shared/ tests/` |
| Backend tests | `PYTHONPATH=. pytest tests/unit/ tests/clinical_validation/ -v --tb=short --cov=services --cov=shared --cov-report=xml --cov-fail-under=60` |
| Workstation build | `npm run build` |
| Workstation lint | `npm run lint` |
| Workstation e2e | `npm run test:e2e` |
| Admin build | `npm run build` |
| Admin lint | `npm run lint` |
| Mobile typecheck | `npm run type-check` |

### Static test evidence

- 787 Python `test_*` functions and 30 `Test*` classes.
- Three clinical-validation modules.
- Strong focused tests for pricing conservation, state transitions/hash chaining, interactions, catalog/coverage, PHI scrubbing, SSE tickets, and Iranian identity.
- Only five HTTP-level tenant-isolation tests.
- Endpoint tests rely heavily on mocks and dependency overrides.
- `tests/integration/` does not exist.
- DB-dependent canonical/enrichment/price tests skip when the dev database is unreachable.
- Schema drift is `xfail`.
- No frontend unit/component, admin, or mobile tests.
- k6 adjudication test is not wired into CI.

No current pass count is known.

### Playwright state

The configuration requires already-running services and uses one shared worker/DB.

Committed evidence shows:

- 20 browser cases after static expansion, plus authentication setup.
- `.artifacts/.last-run.json` reports failure with ten failed IDs.
- Six failures are stale queue selectors.
- Three expect an unmounted `Conversation AI` section.
- One records a security endpoint CORS/network symptom.
- Known POS and intelligence gaps are converted into unconditional passing assertions.
- Label coverage can skip based on mutable seed state.
- E2E is not run in CI.

### CI gaps

- “Lint & Type Check” never runs mypy.
- Workstation lint and Playwright are absent.
- Admin and mobile checks are absent.
- Integration command targets a missing directory and ends with `|| true`.
- Medium Bandit results and the password grep are ineffective gates.
- No dependency, container, IaC, npm, SBOM, or dedicated secret-scanning gates.
- CI uses hand-maintained unpinned dependency lists instead of the Poetry manifest/lock.
- Python CI uses 3.12; images use 3.11.

### Local development scripts

`scripts/dev.sh` and `scripts/start.sh` declare:

- API 8001.
- Workstation 3001.
- PostgreSQL 5433.
- Local Qdrant HTTP 6334.

They are macOS/Homebrew-oriented and mutate local state:

- Kill processes on fixed ports.
- Rewrite `.qdrant/config.yaml`.
- Rewrite workstation `.env.local`.
- Apply migrations.
- Potentially seed data.
- Start services.

`start.sh` starts the API before its explicit migration step even though the API migrates on startup. Its empty-DB seed path can report success even if no demo pharmacy exists.

### Compose and Terraform

Compose is not runnable as declared:

- Missing `Dockerfile.clinical_brain`.
- Missing `infrastructure/prometheus.yml`.
- Missing `services/platform/celery_app.py`.
- Audio/biometric/ML services lack commands.
- No frontend service.
- API uses 8000 instead of 8001.
- Qdrant port semantics conflict by execution mode.
- `scripts/init_db.sql` and Alembic create competing schema paths.
- No `.dockerignore`, non-root users, or container health checks.

Terraform references absent modules for VPC, KMS, RDS, Redis, ECS, WAF, and secrets. The generated `DATABASE_URL` is incomplete for the application.

### Deployment risks

- API/audio images and frontend S3 assets publish in parallel with tests.
- `latest` images and frontend content may therefore change before validation.
- ECS migration tasks are started but not awaited or exit-code checked.
- “Blue-green” deployment is actually a rolling ECS update.
- Smoke tests use only shallow `/health`.
- No evidenced automated rollback, restore test, RPO/RTO, or deployment of updated audio workers.
- Multiple API replicas may race boot-time migrations.

### Observability

Confirmed:

- Basic Prometheus ASGI app at `/metrics`.
- Configuration keys for Sentry and OTLP.

Missing or unverified:

- Request/DB/external-call instrumentation.
- Sentry or OpenTelemetry initialization.
- Alert rules and dashboards-as-code.
- SLOs.
- Dependency-aware readiness.
- Log retention.
- Backup restoration.

---

## 10. Documentation-to-code contradiction matrix

| Documentation claim | Current code/artifact evidence | Verdict |
|---|---|---|
| Product is a US NCPDP/Surescripts/PDMP pilot | Iran-first defaults, roadmap, IRC/NFI canonical corpus | Root README/runbook are legacy |
| Day 1 catalog is 11,400 NDCs | Canonical manifest has 39,044 IRC entries | Stale count and identity |
| Iranian pricing is “done” | Arithmetic exists; tariff constants remain marked VERIFY | Engine done; market correctness unproven |
| Reception affordability flow is done | Quote is mounted; send-to-filling only closes modal | Partial |
| Full reception-to-dispense lifecycle works | POS unmounted, stock not decremented, handoff inert, reversals incomplete | False |
| Each endpoint is permissioned, tenant-scoped, fail-safe, and audited | Patient/Rx child gaps, unauthenticated audio/biometric/WS routes, no automatic audit hook | False platform-wide |
| Redis prevents double verification | Main routes construct state machine without Redis | Inactive |
| Serious DDI acknowledgement blocks progress | Workstation gate only; transition API has no hash prerequisite | Bypassable |
| Status mutates only through `RxStateMachine` | POS and back-office raw SQL update status | Invariant violated |
| Dispense decrements inventory | State-machine side effect only logs future work | Not implemented |
| Full test suite is 402 passed/1 skipped | Static suite now has 787 functions; no run; e2e artifact failed | Stale/unsupported |
| Frontend typecheck is clean | Current roadmap records TypeScript baseline errors | Unsupported |
| Integration testing gates CI | Missing directory and `|| true` | False |
| Migrations end at 0024 | `0025_formulary_snapshots.py` exists | AGENTS stale |
| Offline law guarantees useful ≤2.5s local responses | Socket probes and Ollama/provider calls can take much longer | Not universal |
| PHI routes only to BAA providers | Direct registry/forced provider paths; BAA is static config | Not assured |
| React workstation uses React 18 | Workstation/admin use React 19.2 | Stale |
| Every intelligent panel displays tier provenance | Only selected panels use tier envelopes | False |
| Durable offline mutation queue exists | In-memory, unused queue without persistence/sync | False |
| Admin sections are “backend ready” | Seven placeholders; overview lacks auth | Misleading |
| Patient mobile has safe patient auth | Staff auth, saved password, missing APIs | Architecturally false |
| Admin is chain-wide | Analytics backend scopes to one authenticated pharmacy | Contract mismatch |
| Label workflow is fully wired | Missing pharmacy call, demo fallback, direct dispense transition | Partial |
| Demo fallbacks are benign preview behavior | Payments, alerts, money, inventory can appear plausibly successful/live | Unsafe |
| Compose represents runnable topology | Missing files/commands and port conflicts | Architectural sketch |
| Terraform represents deployable AWS infrastructure | Missing modules, undeclared secrets, invalid DB URL | Architectural sketch |
| Deployment tests before publishing | Images/S3 assets publish in parallel with tests | False |
| Quotes pin to historical prices | Current quote reads current catalog fields | False |
| Coverage removal handles all missing rows | Apply path iterates only a 50-row sample | Correctness bug |
| `AuditedBase` emits an audit event per write | It only carries fields; explicit logs are selective | False |
| Frontend READMEs document the apps | Both are generic Vite templates | No useful guidance |
| `.env.example` covers current configuration | Omits Iran/Qdrant/migration/vault/frontend keys | Stale/incomplete |

Recommended source-of-truth order:

1. Current code, migrations, tests, and committed runtime artifacts.
2. `docs/ROADMAP.md`, interpreting “done” as component completion unless exit criteria are met.
3. `AGENTS.md`, corrected to migration 0025.
4. Superpowers specs/plans as design rationale.
5. Root README and Day 1 runbook as historical US-era material only.

---

## 11. Technical debt and risk register

### P0 — release blockers

| Risk | Evidence | Consequence |
|---|---|---|
| Cross-tenant PHI access/mutation | Patient/Rx child routes and ID-only state-machine calls | Unauthorized patient and prescription access |
| Unauthenticated streams/ingest | Rx, biometric, audio, and security endpoints | PHI exposure, biometric abuse, arbitrary tenant writes |
| Audit-chain bypass | POS/back-office direct prescription status SQL | Incomplete or contradictory legal/audit record |
| Bypassable clinical acknowledgement | Interaction acknowledgement enforced only by UI | Client can progress without recorded review |
| POS correctness | Dynamic table, no pharmacy ID, global reports, apparent missing adjudication table | Cross-tenant financial leakage and broken payments |
| AI PHI routing bypass | Forced/custom provider and direct registry paths | Unapproved PHI egress |
| False operational UI | Fabricated payment success and live-looking financial/clinical alerts | Staff may act on invented data |

### P1 — correctness and pilot blockers

- No database row lock around transition/hash-chain generation.
- Redis verification lease is unused in primary routes.
- Inventory does not decrement on dispense.
- Returns, partial fills, reversals, payment, and label actions are not atomic.
- Coverage removal is capped at 50 sampled rows.
- Canonical catalog includes invalid-looking share percentages.
- Tariff, VAT, inpatient, armed-forces, and special-population rules are unverified.
- Pricing does not pin quotes to price history.
- Current prices are converted from `Decimal` to floats in API responses.
- IRC and NDC product identities remain parallel.
- Patient CRUD defaults to `identity_system="american"` and language `en`.
- Schema parity drift is non-gating.
- Harvest/task locks are process-local and non-durable.
- Interaction cache invalidation omits some clinically meaningful inputs.

### P2 — security, privacy, and maintainability

- Default database, Redis, Grafana, Neo4j, and JWT development credentials.
- No production configuration fail-fast.
- Workstation stores tokens in `localStorage`.
- Mobile retains username/password.
- Biometric/audio consent and retention policy are undefined.
- Provider keys stored in plaintext local JSON.
- Exception details can reach clients.
- Dynamic DDL bypasses Alembic.
- Transaction ownership is split between request dependency and services.
- Business logic and raw SQL are concentrated in routers.
- Two auth abstractions (`Staff` and plain dict) coexist.
- Three product eras coexist: US dispensing, Iran catalog/pricing, broad AI dashboards.
- Forty-eight routers and approximately 271 endpoints lack a uniform resource-authorizer abstraction.

### P2 — performance and reliability

- WebSockets poll PostgreSQL every three seconds per client.
- Connectivity probes block synchronously.
- Synchronous Anthropic calls occur in async code.
- Local model calls may wait 120 seconds.
- Audio/Whisper work occurs in HTTP request paths.
- In-process tasks disappear on restart.
- Security/harvest/provider state is process-local.
- Boot-time migrations can race under multiple replicas.
- Shallow health checks cannot detect database or sidecar failure.
- No verified load or latency baseline; the k6 scenario is stale and internally inconsistent.

### P3 — product and UX debt

- No URL routing or deep linking.
- No permission-aware navigation.
- Mixed Persian/English and no locale framework.
- Fixed-width workstation.
- Broken shortcuts and search placeholder.
- Unmounted workflow components.
- Admin and patient apps divert attention from the unfinished core pharmacist journey.
- Design token source is unused.
- Generated demo state is not visibly separated from live data.

---

## 12. Prioritized continuation roadmap

### First implementation slice: tenant-bound, audit-preserving Rx commands

This should precede new AI, admin, mobile, or dashboard work.

Scope:

1. Introduce one pharmacy-bound Rx loader/command boundary using `(rx_id, staff.pharmacy_id)`.
2. Require `pharmacy_id` in `RxStateMachine` claim, release, transition, history, alerts, and fills.
3. Lock the prescription row during state transitions and hash-chain creation.
4. Enforce serious-interaction acknowledgement and findings hash server-side before adjudication/fill.
5. Remove direct status writes from POS and back-office code; all status changes call the state machine.
6. Add two-pharmacy negative tests for every affected command and child read.
7. Add audit-chain tests proving POS/back-office paths cannot bypass events.

This slice is bounded, high-leverage, and requires minimal product-policy invention.

### Phase 0 — security boundary

Dependencies: device-auth decision for edge nodes; chosen WebSocket ticket mechanism.

- Authenticate and pharmacy-bind Rx, biometric, and security WebSockets.
- Authenticate audio/biometric/security ingest.
- Validate every child resource through its tenant-owned parent.
- Bind adjudication, labels, Phase 32, vault identities, and POS to staff pharmacy.
- Eliminate `demo-pharmacy-id`.
- Centralize AI egress scrubbing/provider authorization.
- Add production secret/configuration fail-fast.

### Phase 1 — truthful Iran counter workflow

Dependencies: human decision on payment order, label gate, lot allocation, and acknowledgement severity.

- Mount or deliberately remove Rx intake/scanner.
- Persist reception basket and quote provenance.
- Send provisional local quote into a real Rx/fill handoff.
- Clearly distinguish local estimate from authoritative insurer quote.
- Mount a Rial-native payment UI with no false-success fallback.
- Atomically record payment, label completion, lot decrement, and dispense transition.
- Implement partial fill, return-to-stock, and reversal semantics.
- Cover one deterministic scenario with PostgreSQL integration tests and Playwright.

### Phase 2 — Iranian data correctness and first live integration

Dependencies: licensed source access, insurer credentials, pharmacy-domain expert.

- Correct suspicious canonical `share_pct` values.
- Fix coverage removal beyond 50 rows.
- Complete NFI crawl/import and measure expected completeness.
- Populate and validate canonical crosswalk/override artifacts.
- Confirm tariff, VAT, technical fee, rounding, inpatient, armed-forces, special-disease, and special-population policy.
- Validate one real pharmacy receipt.
- Integrate one insurer end-to-end: eligibility, reference/tracking code, authoritative split, failure/retry/reconciliation.
- Pin quotes to dated price history.

### Phase 3 — quality and delivery gates

- Add a real `tests/integration/`.
- Reconcile migration/ORM drift and make parity failure gating.
- Repair TypeScript baseline.
- Replace stale Playwright selectors with semantic locators and isolated fixtures.
- Gate a small reception-to-dispense path.
- Run workstation lint/e2e, admin build/lint, and configured mobile typecheck in CI.
- Add a Python lock and align CI/image versions.
- Publish artifacts only after tests.
- Wait for migration task exit before service deployment.

### Phase 4 — operational architecture

- Choose a hardened modular-monolith topology.
- Normalize API, database, and Qdrant ports.
- Remove or complete nonfunctional Compose services.
- Disable boot-time migration in replicated production.
- Add `/ready` with database/migration/dependency checks.
- Instrument HTTP, DB, insurer, and AI latency/error metrics.
- Add alerting, SLOs, restore testing, rollback, container scanning, and `.dockerignore`.

### Phase 5 — product rationalization

- Decide whether the current market is exclusively Iran.
- Hide/deprecate US dashboards and terminology behind an explicit future-market flag.
- Replace all fabricated operational fallbacks with labelled demo fixtures or empty/error states.
- Decide whether mobile becomes a real patient app or a staff companion.
- Establish patient authentication/consent before further mobile work.
- Add admin authentication and define chain/multi-pharmacy semantics before expanding its placeholders.

---

## 13. Unknowns requiring human decisions

1. Is Iran the exclusive active market?
2. Should NCPDP, Surescripts, PDMP, EPCS, NDC, DIR, PBM, REMS, CMS, DEA, and NPI remain future seams or be removed from current UI?
3. What is the canonical product identity joining IRC reference data to inventory lots?
4. Which Iranian insurer will be integrated first?
5. What licenses and retention rules apply to NFI and دارونامه crawling/export?
6. Which tariff, technical-fee, VAT, rounding, and special-population rules are authoritative?
7. May the counter display a local fallback estimate, and what disclaimer/provenance is required?
8. Is payment mandatory before `dispensed`?
9. Does label print/confirmation gate dispense?
10. How should lots be allocated and decremented?
11. What are the exact partial-fill, return, cancellation, and insurer-reversal semantics?
12. Which interaction severities require acknowledgement, physician contact, or hard stop?
13. Who provides clinical validation/sign-off for deterministic corpora?
14. Is the national catalog global per deployment, per chain, or centrally governed SaaS data?
15. Are AI provider settings tenant-specific, chain-specific, or deployment-global?
16. Which provider accounts actually have contractual PHI/BAA approval?
17. Should biometrics, audio recording, surveillance, and evidence vault remain product scope?
18. What consent, retention, legal-hold, and deletion policies apply to biometric/audio data in Iran?
19. Is the mobile product truly patient-facing?
20. What patient identity, enrollment, consent, and recovery model is required?
21. Is admin single-pharmacy, chain-wide, or platform-super-admin?
22. What deployment environment actually exists outside the repository?
23. Are external ECS task definitions or Terraform modules maintained elsewhere?
24. What RPO, RTO, backup, and restore obligations apply?
25. Which dashboard demo fallbacks are intentional sales demonstrations rather than unfinished product behavior?

---

## 14. Coverage ledger

### Intake and inventory coverage

Examined all `.codex-intake` groups:

- `REPOSITORY_INVENTORY.md`
- `SOURCE_STATE.md`
- `DOCUMENT_LEDGER.txt`
- `SOURCE_LEDGER.txt`
- `CONFIG_LEDGER.txt`
- `TEST_LEDGER.txt`
- `TEXT_FILE_LEDGER.txt`
- `FILE_INVENTORY.json`

The 11,431-line file inventory was inspected structurally for path, type, size, and hash coverage; each hash record was not manually reread.

### Documentation coverage

Read fully:

- `AGENTS.md`
- `README.md`
- `DAY1_RUNBOOK.md`
- `docs/ROADMAP.md`
- `docs/MASTER_PROMPT_intelligent_services.md`
- Both frontend READMEs
- Migration README
- All 13 `docs/superpowers/specs/*`
- Intake source/inventory documents
- Playwright configuration, setup/helpers, three specs, `.last-run.json`, and all ten `error-context.md` files

The eleven superpowers plans total roughly 10,474 lines. All plan headings, task maps, relevant contracts, status claims, and companion specifications were read and reconciled with code. Every implementation snippet was **not** line-read in these six largest plans:

- `2026-06-21-interaction-engine.md`
- `2026-06-22-interaction-report-panel.md`
- `2026-06-24-physician-letter.md`
- `2026-06-25-interaction-bundle-installer.md`
- `2026-07-07-darunameh-crawler.md`
- `2026-07-07-harvest-diagnostics-logging.md`

The smaller depot, interaction-audit, interaction-bundle, DDInter, and enrichment plans were fully or near-fully read.

Intentionally excluded as non-product/generated material:

- Ten `.claude/graphify` operator-skill documents.
- Thirty-three generated `graphify-out/*/GRAPH_REPORT.md` reports.
- Binary/state details within `.qdrant`.
- Generated screenshot pixel analysis; their Markdown error contexts were read.

No `CLAUDE.md`, `docs/ai-context/CODEX_PROJECT_BIBLE.md`, or `.codex-intake/CODEX_PROJECT_DEEP_DIVE.md` exists in this snapshot. `SOURCE_STATE.md` indicates missing context files existed as untracked source-worktree files but were not captured; no attempt was made to access outside the snapshot.

### Source/module coverage

High-centrality paths examined:

- FastAPI composition, config, database, auth, migrations, all router registration.
- Auth/session/RBAC.
- Patient and prescription routes.
- State machine, intake precompute, patient context.
- POS, adjudication, labels, inventory movements.
- Iranian pricing, eligibility, insurer adapters.
- Catalog import, harvesting, coverage, crosswalk, enrichment, price history, canonical export.
- Interaction/CDS, physician letters, all major clinical service families.
- Intelligence tier, local LLM, PHI scrubber, provider registry, outbox.
- Knowledge/Qdrant architecture.
- Representative audio, biometric, security, vault, depot, package, shelf, graph, and integration code.
- Shared model registry and principal model domains.
- All migration filenames/revision chain and representative migration bodies.
- Workstation composition, queue, verification, patient, quote, label, payment, dashboard registry, dashboard families, offline utilities, API/store code.
- Admin composition/API.
- Every mobile tab, auth store, API client, and Expo configuration.
- Manifests, locks, environment template, CI/deploy, Dockerfiles, Compose, Terraform, scripts, load test.
- Static searches for TODO/FIXME/HACK/XXX, placeholders, mocks, `NotImplementedError`, skips, xfails, unmounted components, direct status writes, and unauthenticated streams.

Not exhaustively inspected:

- Every low-level ML/CV/audio algorithm.
- Every body of all approximately 271 routes.
- Every individual unit-test body.
- Generated graph reports and Qdrant state.
- Actual database contents beyond committed canonical/reference artifacts.
- External credentials, insurer availability, deployed AWS resources, hardware, printer/scanner behavior, or clinical pilot results.

The original data/API specialist stalled and was replaced by a bounded data/API recheck. Architecture, root inspection, and the replacement investigation overlap the omitted portions; the replacement did not reread the complete pricing engine or state machine, both of which were independently inspected by the primary and architecture investigations.

### Confidence

- **High:** product intent, source topology, implemented symbols/routes, static security gaps, frontend reachability, migration chain, CI/Compose/Terraform contradictions.
- **Medium:** runtime operability, real data quality, external integrations, model availability, performance, and clinically authoritative coverage.
- **Unknown:** current test/build status, deployed topology, live credentials, actual DB state, hardware behavior, contractual compliance, and real-pharmacy validation.

---

## 15. Handoff checklist for the next Codex session

1. Read `AGENTS.md`, this report, `docs/ROADMAP.md`, and the directly relevant source paths.
2. Treat the migration head as `0025`, not `0024`.
3. Treat root README and Day 1 material as historical US-era documentation.
4. Confirm whether the task concerns Iran-first current product or a future US seam.
5. Inspect `git status` before edits; preserve unrelated worktree changes.
6. Preserve the invariants:
   - `Prescription.status` changes only through `RxStateMachine`.
   - Every PHI resource is bound to `staff.pharmacy_id`.
   - Deterministic rules own clinical verdicts.
   - Pricing uses `Decimal`/Rial conservation.
   - External catalog/coverage/price changes require proposal/review.
7. For workflow work, begin with the tenant-bound Rx command boundary described in Section 12.
8. Do not mount the current POS without removing false success, dynamic schema creation, global reads, and direct status mutation.
9. Do not expose audio, biometric, security, or WebSocket features before authentication and tenant binding.
10. Do not route PHI through AI Hub/provider selection until a centralized egress guard exists.
11. Never display fabricated operational data as live.
12. Add targeted tenant-negative and audit-chain tests with each boundary change.
13. Use PostgreSQL-backed tests for state, migration, financial, inventory, and tenant behavior.
14. Run only the declared targeted checks appropriate to the change and available environment.
15. Do not report a test, build, typecheck, Compose, or deployment success without executing and evidencing it.
16. Review the final diff for direct status SQL, missing pharmacy predicates, float money, raw unvalidated external data, and unrelated churn.
17. Update durable context when behavior or architecture changes. The missing `docs/ai-context/CODEX_PROJECT_BIBLE.md` should be recreated when write access and task scope permit it.
18. Defer broad AI dashboards, admin expansion, and patient mobile work until the pharmacist counter workflow and security boundaries are sound.