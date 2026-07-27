# Next Build Plan

## Priority 0 — Truth and safety repairs

Best first slice: harden tenant/auth boundaries for patient/Rx child routes and WebSockets.

Relevant files:

- `services/platform/routers/patients.py`
- `services/platform/routers/prescriptions.py`
- `services/platform/auth.py`
- `tests/unit/test_router_http_tenant_isolation.py`
- `tests/unit/test_queue_ws_first_push.py`
- `tests/unit/test_sse_ticket_auth.py`
- `frontend/workstation/src/stores/rxQueue.ts`

Acceptance criteria:

- Patient allergy/insurance/lab/note routes verify the parent patient belongs to `staff.pharmacy_id` before read/write/delete.
- Rx child routes (`history`, `dur-alerts`, `fills`, `dur-override`, claim/release where applicable) verify prescription pharmacy ownership.
- Rx queue WebSocket requires authenticated access via short-lived SSE-style ticket or an equivalent WebSocket auth pattern, and the requested pharmacy must match the authenticated staff pharmacy.
- Tests cover same-tenant success and cross-tenant 404/403 for each repaired route class.
- Workstation WebSocket client is updated to acquire/pass the accepted auth credential without placing long-lived access tokens in URLs.

Risks:

- Browser WebSocket auth cannot set arbitrary headers like normal axios calls; avoid leaking long-lived JWTs in URLs.
- Existing e2e tests may depend on unauthenticated sockets and need updates.

Stopping point:

- Stop after route and client auth/scoping are repaired and targeted tests are added. Do not broaden into biometric/audio/security in the same slice unless explicitly requested.

## Priority 1 — Harden biometric/audio/security ingress

Relevant files:

- `services/platform/routers/biometric.py`
- `services/platform/routers/audio.py`
- `services/platform/routers/security_events.py`
- `services/biometric/**`
- `shared/models/biometric.py`, `shared/models/audio.py`

Acceptance criteria:

- All PHI/biometric/audio transcript reads and writes require authenticated staff or a documented edge-node credential.
- Edge ingest endpoints use a separate, scoped machine credential rather than arbitrary `pharmacy_id` payload trust.
- Biometric/audio preload queries tenant-scope by pharmacy.
- Transcript approval uses authenticated reviewer identity, not client-supplied `pharmacist_id`.
- Tests cover unauthorized, wrong-pharmacy, and authorized cases.

## Priority 2 — Fix auth/session correctness

Relevant files:

- `services/platform/auth.py`
- `services/platform/routers/auth.py`
- `shared/models/auth.py`
- auth tests under `tests/unit/`

Acceptance criteria:

- Refresh sessions expire according to `REFRESH_TOKEN_EXPIRE_DAYS`, while access JWTs expire according to `ACCESS_TOKEN_EXPIRE_MINUTES`.
- Logout revokes only the current session unless product explicitly wants global logout.
- Staff creation removes dead code and validates same-pharmacy behavior.
- Tests cover refresh rotation, expired refresh rejection, current-session logout, and lockout behavior.

## Priority 3 — Make Rx fulfillment real end-to-end

Relevant files:

- `services/core/pharmacy_workflow/state_machine.py`
- `services/platform/routers/prescriptions.py`
- `services/platform/routers/pos.py`
- `services/platform/routers/label_engine.py`
- `services/core/inventory/movements.py`
- `frontend/workstation/src/components/VerificationCenter.tsx`
- `frontend/workstation/src/components/PaymentCollection.tsx`
- `frontend/workstation/src/components/LabelPreview.tsx`

Acceptance criteria:

- Dispense cannot bypass required payment/adjudication/label checkpoints where applicable.
- Inventory movement is recorded/decremented on dispense and reversed/returned where appropriate.
- Label print/handwritten label event is part of final workflow audit.
- Rx state history remains hash-chained.
- Targeted backend tests and one workstation e2e happy path are updated.

## Priority 4 — Reconcile pricing/data truth

Relevant files:

- `services/platform/routers/pricing.py`
- `services/core/pricing_ir/`
- `services/core/drug_catalog/`
- `shared/models/coverage.py`, `shared/models/crosswalk.py`, `shared/models/price_history.py`
- `frontend/workstation/src/dashboards/CoverageAdmin.tsx`
- `frontend/workstation/src/dashboards/DrugCatalogAdmin.tsx`

Acceptance criteria:

- At least one real insurer دارونامه source is loaded through staged review.
- Pricing is validated against real receipt fixtures.
- Franchise, technical fee, VAT, and special-population rules are confirmed by a domain expert and encoded in regression tests.
- Crosswalk/override behavior survives re-imports.

## Priority 5 — Build/CI/docs stabilization

Relevant files:

- `README.md`, `DAY1_RUNBOOK.md`, `docs/ROADMAP.md`
- `.github/workflows/ci.yml`, `.github/workflows/deploy.yml`
- `docker-compose.yml`, `infrastructure/docker/*`
- `pyproject.toml`, frontend/mobile package manifests

Acceptance criteria:

- README/runbook status matches code and roadmap.
- CI integration tests are meaningful or explicitly removed; no `|| true` masking for required gates.
- Python dependency manifest matches imports used in CI/runtime.
- Docker Compose either works as official local topology or is labeled unsupported.
- API port convention is unified.

## Feature work and polish after repairs

- Promote or hide admin sections instead of showing broad placeholders.
- Decide whether the mobile app is a real patient surface; if yes, add patient auth and patient-specific APIs.
- Improve workstation navigation/routing only after high-risk backend boundaries are fixed.
- Expand clinical modules only with deterministic source facts, audit logging, and tests.
