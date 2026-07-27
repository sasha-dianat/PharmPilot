# Project Bible

## Reading contract

This document is a static audit of the 2026-07-22 repository snapshot. Labels mean:

- **Confirmed:** directly evidenced in code, manifests, data, tests, or snapshot ledgers.
- **Inference:** an engineering assessment based on confirmed evidence.
- **Unknown:** requires execution, external evidence, or a human decision.

Current code and tests outrank this document when the repository changes.

## Canonical platform description

**Confirmed:** PharmPilot AI is an Iran-first pharmacy operations and medication-intelligence platform. Its primary product surface is a pharmacist dispensing workstation backed by a FastAPI/PostgreSQL modular monolith. The strongest implemented area is Iranian catalog, coverage, price governance, deterministic pricing, and deterministic clinical support. AI supplies advisory prose, prioritization, extraction, and degraded-mode assistance.

**Inference:** This is an advanced prototype and pilot foundation, not a production-ready pharmacy system. The admin portal, patient-labeled mobile app, biometric/audio/security edge functions, US adjudication modules, and deployment manifests are incomplete or internally inconsistent.

## Snapshot and source authority

- **Confirmed:** `.codex-intake/SOURCE_STATE.md` records source commit `aeddb879e0265a7dbfe275e5f1227353b39136cd` on `feat/darunameh-crawler`, captured at `2026-07-22T06:29:01Z` from a dirty source worktree.
- **Confirmed:** the isolated snapshot checkout reports clean `master`, but that does not erase the dirty source provenance.
- **Confirmed:** inventory coverage is 1,905 copied files: 575 source, 86 documentation, 136 test-related, plus substantial generated/stateful material under `graphify-out/` and `.qdrant/`.
- **Confirmed:** `docs/ai-context/CODEX_PROJECT_BIBLE.md` is absent from this snapshot. This output is the corrected content intended for that path.

Source-of-truth order:

1. Current code, migrations, and tests.
2. `docs/ROADMAP.md`, treated as intent and maturity guidance rather than proof.
3. `.codex-intake/SOURCE_STATE.md` and the inventory ledgers for snapshot provenance.
4. This Project Bible.
5. `.codex-intake/CODEX_PROJECT_DEEP_DIVE.md`, with the corrections in this Bible.
6. `README.md` and `DAY1_RUNBOOK.md`, which contain stale US-era and test-pass claims.

## Canonical terminology

| Concept | Canonical usage |
|---|---|
| Market and locale | Iran; `DEFAULT_IDENTITY_SYSTEM = iranian`, `DEFAULT_LOCALE = fa-IR` in `services/platform/config.py` |
| Person identity | Iranian national code, کد ملی; Jalali and Gregorian dates may coexist |
| Product identity | IRC is the active Iranian catalog key. NDC remains in legacy inventory, Rx, and US-integration models |
| National catalog | NFI / فهرست رسمی دارویی; do not call the bundled data complete or authoritative without provenance validation |
| Insurer publication | دارونامه, formulary, or coverage publication |
| Basic insurers | تأمین اجتماعی / Tamin, بیمه سلامت / Salamat, Armed Forces; supplementary coverage is a separate category |
| Insurer inquiry | استعلام or eligibility inquiry. The pricing `EligibilityProvider` and Iranian identity adapter registry are separate abstractions and must not be conflated |
| Money | Rial for calculations; 1 Toman = 10 Rial. Use `Decimal`, not binary floats, for authoritative arithmetic |
| US terminology | NDC, NCPDP, PBM, Surescripts, EPCS, PDMP, DEA, NPI, FDB, Medi-Span, CMS, REMS, DIR, 340B, and US state fields are legacy or future seams unless a human explicitly makes them active requirements |

## Runtime architecture

**Confirmed topology:**

- `services/platform/main.py` is the FastAPI composition root.
- It mounts 48 routers. Static inspection finds 272 FastAPI route or WebSocket decorators in `services/platform/`.
- `services/platform/database.py:get_db` commits after a successful request and rolls back on exceptions.
- PostgreSQL/SQLAlchemy is the principal system of record.
- Alembic has a statically linear 25-revision chain from `0001` through `0025`; `0025_formulary_snapshots.py` is the current head.
- Redis is intended for caching and queue leases; Qdrant supports clinical retrieval.
- Kafka, Celery, ClickHouse, and Neo4j appear as optional or aspirational seams. The checked-in Compose topology does not make them a coherent verified runtime.
- `frontend/workstation/` is the main React client. `frontend/admin/` and `mobile/patient_app/` are secondary, immature surfaces.

**Transaction warning:** routers and services frequently call `commit()` internally or create tables dynamically. Therefore request-level transaction ownership is not reliable across the application.

## Repository map

| Path | Responsibility and status |
|---|---|
| `services/platform/` | FastAPI composition, configuration, auth, database sessions, migrations, and API routers |
| `services/core/pharmacy_workflow/` | Rx state machine, DUR, triage, intake precompute, patient context, and back-office agents |
| `services/core/drug_catalog/` | IRC catalog import, NFI harvesting, coverage ingestion, staged runs, matching, enrichment, crosswalks, canonical export, and price history |
| `services/core/pricing_ir/` | Pure deterministic Rial pricing and tariff configuration |
| `services/core/inventory/` | Stock movements, replenishment, procurement, and stock intelligence; still primarily NDC-shaped |
| `services/core/adjudication/` | US/NCPDP-style sandbox adjudication; not a live Iranian insurer integration |
| `services/ai/clinical_decision_support/` | Deterministic rules and interaction analysis |
| `services/ai/` | Clinical advisory engines, RAG, provider routing, offline-first envelopes, transcription, vision, forecasting, and intelligence services |
| `services/integrations/iranian_insurance/` | Iranian insurer adapter contracts; only Salamat contains live HTTP mapping, while other adapters inherit sandbox fallback behavior |
| `services/biometric/` and `services/audio/` | Experimental identity, evidence-vault, security, transcription, and profile-enrichment capabilities |
| `shared/models/` | Main SQLAlchemy registry and domain models |
| `data/migrations/versions/` | Alembic migrations `0001`–`0025` |
| `data/canonical/` | Bundled canonical export, not automatically production-authoritative |
| `frontend/workstation/` | Three-column dispensing workstation plus 24 mounted dashboard sections |
| `frontend/admin/` | Overview shell; seven non-overview sections are placeholders |
| `mobile/patient_app/` | Expo patient-labeled prototype currently using staff authentication |
| `tests/` | Unit and clinical-validation suites; Playwright tests are under `frontend/workstation/tests/e2e/` |
| `docs/` | Roadmap plus historical specifications; verify all claims against code |
| `graphify-out/`, `.qdrant/`, `dump.rdb` | Generated or stateful artifacts; do not treat as primary source code |

## Principal data flows

### Staff authentication

`POST /api/v1/auth/login` authenticates a `Staff`, creates a `StaffSession`, and returns access and refresh tokens carrying staff, pharmacy, role, JTI, and expiry data. `get_current_staff` validates the JWT, session, staff status, and lockout. The workstation stores tokens in `localStorage`.

Known defects: session expiry uses `ACCESS_TOKEN_EXPIRE_MINUTES`, making `REFRESH_TOKEN_EXPIRE_DAYS` ineffective; logout revokes all active staff sessions; default production secrets do not fail fast; decoding exposes JWT exception text.

### Identity resolution

`services/platform/routers/identity.py` calls `IdentityOrchestrator`, which combines transcript/OCR extraction, national code, insurer results, biometrics, person links, and patient history.

This flow is currently unsafe:

- request bodies can supply an arbitrary `pharmacy_id`;
- linked-candidate and insurer lookups are not tenant-bound;
- `_SandboxFallbackAdapter` creates plausible fake people when credentials are absent or live calls fail;
- `IdentityOrchestrator._upsert_patient` may persist those results as patients and family coverage.

Sandbox output must never be treated as an authoritative patient identity or silently persisted.

### Prescription workflow

The intended lifecycle is modeled in `services/core/pharmacy_workflow/state_machine.py:RxStateMachine`:

`intake → pending_dur → pending_verification → verification_in_progress → pending_adjudication → ready_to_fill → filling → filled/will_call → dispensed`

with hold, rejection, PA, cancel, return, and terminal branches. Each state-machine transition creates a hash-chained `RxStateEvent`.

Current violations include unscoped child routes, intake accepting a patient from another pharmacy, state-machine queries lacking pharmacy predicates and row locks, direct status updates in POS and `AutoPAAgent`, unvalidated interaction acknowledgements, and no atomic payment/label/inventory/dispense command. Dispense and return inventory effects are log messages only.

### Catalog, coverage, and price governance

The governed path is:

1. ingest NFI or insurer rows;
2. normalize and match to IRC catalog records;
3. stage a `CoverageRun` with diff, review, unmatched rows, diagnostics, and formulary snapshots;
4. require an owner decision;
5. preserve accepted/rejected matching decisions in crosswalks or overrides;
6. produce price proposals and dated history.

`POST /pricing/coverage/import` is an important exception: it directly applies high-confidence coverage through `apply_coverage`, bypassing staged approval. Also, `remove_missing` clears only the capped sample of 50 removed IRCs rather than the complete removal set.

The bundled `data/canonical/manifest.json` reports 39,044 catalog rows, 32,506 current prices, and zero crosswalk or override records. These are snapshot counts, not completeness or authority guarantees. Static inspection found 254 catalog entries containing `share_pct: 10070`, requiring data-quality review.

### Deterministic pricing

`services/core/pricing_ir/engine.py` computes gross, covered base, insurer share, patient share, differential, VAT, and technical fee with `Decimal` and a conservation invariant. `config.py` explicitly leaves inpatient shares, Armed Forces shares, technical fee, VAT, and special populations partly unverified.

The API converts amounts to floats and uses current catalog values rather than pinning the quote to a price-history effective date. `/pricing/quote` requires `clinical:read`, excluding the manager, cashier, technician, and any not-yet-defined reception role under default RBAC.

### Clinical and AI processing

Deterministic rules must determine clinical facts and verdicts. LLMs may draft, summarize, rank, or explain, but must not silently override rule results or authorization gates.

Implemented domains include interactions, ADR review, counselling, physician messages, polypharmacy, PGx, lab safety, medication reconciliation, drug intelligence, second-brain/RAG, triage, and specialist-council reports. Their existence and tests do not establish regulatory or clinical validation.

`AIProviderRegistry` has important bypasses: pharmacy-specific routing returns before PHI filtering, and `force_provider` bypasses the provider chain. Provider BAA flags are static configuration, not proof of a deployment agreement. Routing, cost, and invocation audit are process-local; provider keys are stored in a global plaintext JSON file with mode `0600` rather than tenant-scoped secret storage.

## Non-negotiable engineering invariants

1. Derive the effective pharmacy from authenticated identity. Never trust a tenant ID from a request body, path, query, WebSocket URL, or AI payload without an explicit authorized cross-tenant role.
2. Validate parent ownership before reading or mutating patient, Rx, fill, claim, insurance, document, transcript, note, lab, allergy, or identity-link children.
3. Authenticate and bind WebSockets and edge-ingest routes to a pharmacy-scoped principal or service credential.
4. Change `Prescription.status` only through `RxStateMachine`; use row locking or equivalent serialization for status and hash-chain updates.
5. Make clinical acknowledgement, payment, inventory reservation/deduction, label finalization, and dispense prerequisites server-enforced.
6. Keep authoritative price arithmetic in `Decimal` Rial logic. Preserve effective dates, source, and rounding evidence.
7. Stage external catalog, price, and coverage facts for explicit approval unless a separately approved policy says otherwise. Never silently persist sandbox, degraded, OCR, crawler, or LLM output as fact.
8. Clinical AI is advisory. Deterministic evidence and human authorization decide safety-critical actions.
9. Classify and protect PHI before every local or external AI call; provider forcing must not bypass policy.
10. Manage schema with reviewed Alembic migrations, not request-time `CREATE TABLE` statements.
11. An audit claim is true only when an immutable, tenant-bound event is actually written. `AuditedBase` columns alone do not create audit events.
12. Do not fabricate success after payment, claim, persistence, or network failure. Return an explicit degraded, pending, or failed state.

## Implementation status

| Area | Audited status |
|---|---|
| FastAPI/backend | Broad modular monolith with many implemented routes; authorization and transaction boundaries are inconsistent |
| Iranian catalog/coverage | Most mature area; staged governance, crosswalk, snapshots, history, and RTL admin UI exist, but source authority and data quality remain unverified |
| Pricing | Deterministic core is strong; tariff completeness, date pinning, live eligibility, permissions, and API numeric representation remain gaps |
| Rx workflow | States and UI exist; finalization is not a safe end-to-end transaction |
| Inventory/depot | Scoped movement and depot paths exist, but inventory remains NDC-shaped and is not integrated with dispense |
| Clinical support | Wide deterministic/advisory surface with narrow fixtures; not externally validated |
| AI/offline | Many services and envelopes exist; policy enforcement, persistence, workers, and provider governance are incomplete |
| Workstation | Main three-column app plus 24 dashboards; role navigation is not filtered, several workflows are unmounted, and some failures become synthetic success |
| Admin | Overview shell without bearer injection; claimed chain metrics are actually pharmacy-scoped; seven sections are placeholders |
| Mobile | Nonfunctional patient architecture: staff login, staff ID used as patient ID, saved password re-login, and missing server endpoints |
| Infrastructure | Compose, Terraform, and deployment workflows are aspirational and internally inconsistent |

## Static test and command evidence

No application code, tests, builds, migrations, or project scripts were executed during this read-only audit.

**Confirmed static evidence:**

- 787 Python `test_*` functions and 30 `Test*` classes were found under `tests/`; pass state is unknown.
- The ignored Playwright artifact `frontend/workstation/tests/e2e/.artifacts/.last-run.json` records a failed run with 10 failed test IDs.
- Playwright includes tautological checks and seed-dependent skipping.
- `frontend/workstation/tests/e2e/.auth/pharmacist.json` is present in the intake snapshot and contains bearer/refresh material. It is ignored and not tracked in the snapshot Git index, so it must not be described as a committed secret; it is still a snapshot-handling and possible credential-rotation concern.
- `README.md` and `DAY1_RUNBOOK.md` claims such as 402 passed, 1 skipped and clean frontend typecheck are stale and unevidenced for this snapshot.
- CI calls a missing `tests/integration/` directory with `|| true`; schema drift is `xfail`; mypy is installed but not run; medium Bandit findings are ignored.

Declared commands are documented in `AGENTS.md`. Their presence in a manifest is not evidence that they pass.

## Established conventions

- Prefer focused changes following existing local patterns.
- Use `services/core/` for deterministic domain logic and keep routers thin.
- Keep Iran-specific identity, localization, pricing, and source terminology explicit.
- Add all ORM model modules to the shared registry and Alembic metadata path.
- Put tenant filters in reusable loaders or policy helpers, then test two-pharmacy negative cases.
- Keep data transformation stages replayable and provenance-bearing.
- Update this Bible, `AGENTS.md`, the roadmap, or a task-specific design note when architecture, behavior, commands, invariants, or terminology changes.

## Human decisions and known unknowns

- Which Iranian institutions and publications are contractually authoritative for catalog, price, coverage, and eligibility?
- What are the effective-date rules, current franchise percentages, technical fee, VAT treatment, Rial/Toman display policy, and special-population overrides?
- Is the product strictly Iran-first, or must any US modules remain supported? If retained, where is the jurisdiction boundary?
- What is the patient authentication and consent model for mobile, biometrics, audio, family links, and insurer lookup?
- What retention, deletion, data-residency, and lawful-purpose rules apply to PHI, biometric evidence, audio, and AI prompts?
- Are catalogs and AI settings platform-global, chain-level, or pharmacy-specific?
- Which AI providers have approved contracts, data-processing terms, residency, and PHI authorization?
- What external validation is required before deterministic clinical corpora can influence dispensing?
- What production topology is intended, and which services are genuinely required?

## Last validated against snapshot

- **Validation date:** 2026-07-22
- **Source commit:** `aeddb879e0265a7dbfe275e5f1227353b39136cd`
- **Source branch:** `feat/darunameh-crawler`
- **Source worktree:** dirty per `.codex-intake/SOURCE_STATE.md`
- **Isolated snapshot Git status:** clean `master`
- **Method:** safe file reads, searches, counts, manifest inspection, and Git metadata only
- **Not performed:** dependency installation, network access, application execution, project scripts, tests, builds, migrations, or file modification
