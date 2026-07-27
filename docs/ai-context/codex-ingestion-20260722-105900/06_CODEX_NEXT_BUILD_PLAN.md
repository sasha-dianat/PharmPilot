# Next Build Plan

## Ordering principle

Security, tenant isolation, clinical truthfulness, and audit integrity are dependencies for feature work. Do not widen the pilot or connect live insurers while authenticated users, unauthenticated edge clients, or AI routes can cross pharmacy boundaries or persist fabricated identity data.

## Immediate containment before implementation

**Priority:** Critical safety and credential hygiene

- Decide whether the bearer/refresh material in the ignored snapshot file `frontend/workstation/tests/e2e/.auth/pharmacist.json` could reach a live environment. Revoke or rotate it if uncertain, then ensure intake/export tooling excludes auth state.
- Disable or gateway-block unauthenticated clinical, audio, biometric, security-ingest, and WebSocket routes in any shared environment until application-level authentication exists.
- Explicitly forbid `INTEGRATIONS_SANDBOX=true` in non-demo deployments.

**Stopping point:** Do not expose the current API to real PHI or connect live insurer credentials until containment is documented.

## Best first implementation slice: tenant-bound, provenance-safe identity

**Why first:** The current identity flow combines an authorization bypass with the ability to manufacture and persist plausible patient and family records.

**Relevant files:**

- `services/platform/routers/identity.py`
- `services/biometric/identity_resolution/identity_orchestrator.py`
- `services/biometric/identity_resolution/person_links.py`
- `services/integrations/iranian_insurance/base.py`
- `services/integrations/iranian_insurance/adapters.py`
- `services/integrations/iranian_insurance/registry.py`
- `services/platform/config.py`
- `tests/unit/test_iranian_identity.py`
- `tests/unit/test_router_http_tenant_isolation.py`

**Implementation:**

1. Remove request-controlled pharmacy IDs from normal identity and link commands, or reject any value different from `staff.pharmacy_id`.
2. Add reusable pharmacy-bound patient and link loaders. Bind candidate, link, and insurer lookup operations to the authenticated pharmacy and an authorized patient/workflow purpose.
3. Add explicit insurer result provenance such as `live`, `sandbox`, and `unavailable`.
4. Never fall back from a failed live call to fake demographics. Return unavailable/degraded status.
5. Never auto-create or update a clinical patient or family link from sandbox data. Require explicit demo mode and human confirmation for non-live identity proposals.
6. Audit identity lookup, selected candidate, source, pharmacy, staff, and decision without logging full national codes.
7. Validate `identity_system` against deployment policy rather than accepting an unrestricted request override.

**Acceptance criteria:**

- Pharmacy A cannot identify, link, list, or enrich Pharmacy B patients even with known UUIDs.
- A live insurer outage creates no patient, insurance, customer identity, or person-link rows.
- Sandbox results are visibly labeled and never persisted as authoritative records.
- National codes remain masked in logs and minimum-necessary in responses.
- Existing Iranian extraction and valid local-patient matching behavior remains intact.
- Focused identity and two-pharmacy tests pass.

**Risks:** Existing demos may depend on automatic sandbox creation. Preserve that only behind an unmistakable demo-only adapter or fixture.

**Stopping point:** Do not proceed to reception auto-load until a negative tenant test and a live-outage test prove no writes.

## Phase 2: centralized authorization and tenant-bound resource loading

**Priority:** Critical safety repair

Create a small, explicit authorization layer rather than patching isolated predicates. Define loaders for patient, Rx, fill, claim, insurance, PO, document, transcript, clinical context, and identity links. Return 404 for inaccessible cross-tenant resources unless policy requires 403.

**Route groups:**

- Patient children: `patients.py`
- Rx children and WebSocket: `prescriptions.py`
- Claims: `adjudication.py`
- Clinical context: `clinical_brain.py`, `phase32.py`, `knowledge.py`, `clinical_services.py`
- Documents and transcription: `rx_documents.py`, `rx_transcription.py`
- Audit/operations: `dur_overrides.py`, `analytics.py`, `inventory.py`
- Edge: `audio.py`, `biometric.py`, `security_events.py`
- AI administration: `ai_hub.py`, `ai_settings.py`
- Vault: bind identity ownership as well as staff role.

**Acceptance criteria:**

- Every PHI-bearing HTTP route and WebSocket has an authenticated staff, patient, or service principal.
- Tenant scope comes from that principal; arbitrary body/path/query pharmacy values cannot expand access.
- Child mutations verify their parent before insert/update/delete.
- Claim submit/reverse/history and PO submit are pharmacy-bound.
- Analytics never defaults to global data for pharmacy staff.
- Documents/transcripts carry pharmacy and Rx foreign keys and cannot be accessed cross-tenant.
- An authorization matrix and two-pharmacy regression suite cover all mounted routers.

**Tests:** Extend `tests/unit/test_router_http_tenant_isolation.py`; add focused route tests where direct-function tests cannot exercise dependency and HTTP behavior.

**Stopping point:** No feature work in a route group until its resource loader and negative tests exist.

## Phase 3: audit-preserving Rx command boundary

**Priority:** High clinical and operational correctness

**Relevant files:** `state_machine.py`, `prescriptions.py`, `cds.py`, `pos.py`, `adjudication.py`, `backoffice_agents.py`, prescription/inventory models, and workstation verification components.

**Implementation:**

- Add pharmacy scope and row locking to state-machine transitions and prior-event lookup.
- Replace count-plus-one Rx numbers with a concurrency-safe sequence or database-enforced allocator.
- Remove every direct status update and route all transitions through domain commands.
- Validate intake patient ownership.
- Bind claims, fills, interaction reports, overrides, and acknowledgements to the same Rx and patient.
- Recompute or load the current interaction report server-side and require exact serious-finding acknowledgement before the transition gate opens.
- Define a finalization command that atomically validates payment disposition, claim/cash status, label state, counselling/override gates, and inventory reservation/deduction before dispense.
- Make returns and reversals symmetric and audited.

**Acceptance criteria:**

- Concurrent transitions cannot fork the Rx hash chain or create duplicate Rx numbers.
- No code outside `RxStateMachine` changes prescription status.
- Empty, stale, forged, wrong-patient, or wrong-Rx acknowledgements fail.
- Failed payment, label, inventory, or claim prerequisites leave the Rx undisposed and auditable.
- Dispense and return change inventory exactly once.
- Cross-pharmacy command attempts produce no state or audit changes.

**Tests:** `tests/unit/test_rx_state_machine.py`, tenant tests, concurrency tests against disposable PostgreSQL, and an end-to-end command test.

**Stopping point:** Keep the workstation dispense button behind the server command; do not preserve direct status-transition UI shortcuts.

## Phase 4: schema and transaction ownership

**Priority:** High reliability

- Create migrations and tenant-aware models for `payment_events`, `dur_override_events`, `rx_documents`, `rx_transcription_events`, `prescriber_medical_registrations`, and `intelligence_outbox`, or formally remove unused surfaces.
- Register `shared/models/drug_price_proposal.py`; decide whether knowledge ORM models belong in the shared registry or should be deleted in favor of Qdrant-only persistence.
- Remove request-time DDL and service-level commits where they break caller transaction ownership.
- Make schema parity fail rather than xfail after drift is reconciled.
- Replace unsafe legacy stamp-to-head logic with an explicit, verified migration path.
- Run migrations as a single deployment job and wait for successful completion before deploying application tasks.

**Acceptance criteria:** fresh migrated schema matches registered metadata; no mounted request path executes DDL; rollback tests prove multi-step commands are atomic.

## Phase 5: Iranian data and pricing correctness

**Priority:** High domain correctness

- Deprecate or convert `/pricing/coverage/import` to the staged-run workflow.
- Store the complete removed-IRC set so `remove_missing` applies all approved removals, not a 50-item sample.
- Quarantine impossible shares such as `10070` and resolve the 2,598-versus-2,600 Tamin harvest mismatch.
- Establish source hashes, retrieval timestamps, licensing, authority, effective dates, and replay evidence for NFI and insurer publications.
- Populate and validate crosswalk/override decisions; the bundled canonical export currently contains none.
- Pin quotes to effective-dated `PriceHistory` records and keep API money as integer Rial or decimal strings.
- Define a reception permission and purpose-bound live eligibility inquiry.
- Have an Iranian pharmacy/domain expert sign off tariffs, technical fee, VAT, special populations, and sample receipts.

**Acceptance criteria:** a replayable source-to-quote audit explains every amount; approved full removals are complete; sample real prescriptions match signed-off receipts.

**Stopping point:** Do not market authoritative coverage or live pricing until source and receipt validation is complete.

## Phase 6: truthful reception-to-dispense product slice

**Priority:** Feature work after Phases 1–5

Mount and connect the smallest real counter flow:

1. authorized patient identification or search;
2. Rx intake or insurer reference import;
3. deterministic quote with alternatives;
4. explicit patient choice and handoff;
5. clinical verification and valid acknowledgements;
6. claim or explicit cash/deferred disposition;
7. inventory-backed fill;
8. label generation/print acknowledgement;
9. payment record;
10. atomic dispense and receipt.

Remove synthetic successes: `submission_error` is not approval, reception send-to-fill must persist a command, payment failure must remain failure, and dictated notes must not claim to be saved without persistence.

**Acceptance criteria:** a seeded two-pharmacy Playwright scenario exercises the real API without tautological assertions or seed-dependent skips and proves no cross-tenant leakage.

## Phase 7: AI, knowledge, and edge hardening

**Priority:** High before real PHI egress

- Apply PHI policy after all routing overrides and reject unsafe `force_provider` choices.
- Persist tenant-scoped AI settings, approved-provider contracts, invocation audits, and cost records.
- Put provider secrets in managed secret storage.
- Add allowlists, private-network blocking, size/time limits, path roots, secret redaction, licensing controls, and administrator-only authorization to knowledge ingestion.
- Remove the UpToDate cookie-paste workflow unless legal and security review explicitly approves it.
- Add durable outbox handlers and a supervised worker, or remove claims that offline writes reconcile automatically.
- Define edge-device/service authentication, key rotation, replay protection, and pharmacy binding.
- Replace biometric fail-open liveness and in-memory-only identity mapping before real use.

## Phase 8: product and delivery rationalization

**Priority:** Later feature work and polish

- Decide whether to localize/retain or remove US adjudication, EPCS, PDMP, CMS, REMS, DIR, 340B, LTC, and specialty modules.
- Build real patient authentication before continuing the mobile app; never reuse staff credentials or store passwords for biometric re-login.
- Give admin a real chain authorization model and chain-scoped APIs before calling metrics chain-wide.
- Reduce workstation navigation by role and mount only complete workflows.
- Repair Compose, Terraform, health checks, migration orchestration, ports, missing files, non-root execution, and deployment gating.
- Make integration tests and schema parity gating; align supported Python versions and add admin/mobile checks.

**Final release stopping point:** Production or live-pharmacy claims require completed security/tenant review, domain sign-off, migration/backup drill, real integration contracts, tested recovery, clinical validation, and a clean evidenced release gate.
