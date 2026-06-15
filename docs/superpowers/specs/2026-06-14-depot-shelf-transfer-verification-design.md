# Depot → Shelf Transfer: Dual-Verification Replenishment — Design

**Date:** 2026-06-14
**Status:** Approved (brainstorm) — pending spec review → implementation plan
**Branch:** `feat/depot-shelf-verification`

## 1. Problem & context

PharmPilot must enforce a regulated **dual-verification gate** before any unit
moves from the depot (back-stock) onto pharmacy dispensing shelves. The original
spec assumed a two-tier `depot_batches` / `pharmacy_shelves` / `depot_transactions`
schema that **does not exist** in this codebase.

**Actual inventory model (verified):**
- `drug_products` — drug master (has `is_controlled`, `dea_schedule`, `requires_refrigeration`, `package_quantity`).
- `inventory_lots` — per-lot record: `lot_number`, `expiry_date`, `quantity_on_hand`, `serial_number` (DSCSA), `storage_location` (free-text string), `is_recalled`. FEFO, `/expiring`, recalls all build on this.
- `stock_levels` — denormalized aggregate per pharmacy/NDC.
- No `pharmacy_shelves`, no depot/shelf two-tier model, no transfer flow.

**Operational reality (from owner):** depot and shelves are **physically separate
locations**. Daily, inventory staff (1) audit shelves for low stock → build a pick
list, (2) collect those items at the **depot** (barcode + count + camera
surveillance), (3) transport to the pharmacy, (4) place on **shelves** (barcode +
AI photo verify). The two checkpoints are **reconciled** to catch transit loss.

## 2. Architecture decision — Model A (reuse `inventory_lots`)

`inventory_lots` remains the **single source of truth** for each batch
(lot/expiry/serial/FEFO/recall unchanged). We add a **location/placement layer**
on top rather than a parallel `depot_batches` pool (which would create two sources
of truth for the same physical pills).

- The spec's `depot_batches` → **existing `inventory_lots`**.
- The spec's `pharmacy_shelves` → **new first-class table** (locations are currently
  free-text strings, which can't enforce capacity or storage condition).
- The spec's `depot_transactions` → **new `shelf_transfer_events` ledger**.
- **Depot (back-stock) quantity = `inventory_lots.quantity_on_hand − Σ shelf_placements.units`** — computed, never double-stored.

## 3. Data model (migration 0012, additive only)

**New tables**
- `pharmacy_shelves`: `id, pharmacy_id, label, zone, capacity_units, current_units,
  storage_condition (ENUM ROOM_TEMP|REFRIGERATED|FROZEN|LIGHT_PROTECTED), is_active`.
- `shelf_placements`: `id, pharmacy_id, inventory_lot_id→inventory_lots, shelf_id→pharmacy_shelves,
  ndc11, units, placed_at, placed_by`. (Front-stock layer; depot = lot total − Σ placements.)
- `replenishment_sessions`: `id, pharmacy_id, status (PICK_LIST|DEPOT_COLLECTING|IN_TRANSIT|SHELF_PLACING|COMPLETE|CANCELLED),
  pick_list (JSONB), depot_checkpoint (JSONB), reconciliation (JSONB), started_by, started_at, completed_at`.
- `shelf_transfer_events` (ledger): `id, pharmacy_id, session_id, inventory_lot_id, shelf_id, ndc11,
  quantity_delta, performed_by, barcode_verification_result (JSONB), ai_verification_result (JSONB),
  depot_checkpoint_result (JSONB), override_reason (TEXT), override_by (UUID→staff),
  pharmacist_attestation_by (UUID→staff), pharmacist_attestation_at (TIMESTAMPTZ),
  temperature_logged_c (DECIMAL(4,1)), near_expiry_placement_confirmed (BOOL), created_at`.
- `shift_handover_reports`: `id, pharmacy_id, shift_start, shift_end, performed_by,
  transfers_completed (JSONB), fefo_overrides (JSONB), anomaly_signals (JSONB),
  open_split_packs (JSONB), cold_chain_events (JSONB), created_at`.
- `surveillance_events` (security/behavioral layer): `id, pharmacy_id, camera_id,
  session_id (NULL→replenishment_sessions), event_type (ENUM ANOMALY|WRONG_BIN|
  EXTRA_ITEM|BEHAVIOR|UNSCANNED_PICK|AFTER_HOURS), severity (low|medium|high),
  detected_at, clip_ref (TEXT NULL), ai_result (JSONB §1.2 envelope), reviewed_by
  (UUID NULL→staff), reviewed_at (TIMESTAMPTZ NULL), owner_notified (BOOL DEFAULT false)`.

**Additive columns**
- `inventory_lots`: `split_pack_open BOOL DEFAULT false`, `split_pack_remaining_blisters INT NULL`,
  `cold_chain_breach BOOL DEFAULT false`, `cold_chain_breach_log JSONB NULL`.
- `drug_products`: `storage_condition ENUM NULL`, `high_risk_flag BOOL DEFAULT false`,
  `lasa_group TEXT NULL`, `primary_shelf_id UUID NULL→pharmacy_shelves`,
  `blisters_per_box INT NULL`, `units_per_blister INT NULL`.

No existing columns modified. Migration `0012` chains `0011`.

## 4. Workflow (two reconciled checkpoints)

1. **Shelf audit / pick list** — shelves whose `current_units < par` (par from
   `stock_levels.par_level_min` for the NDC) surface a FEFO-ordered pick list of
   depot lots (earliest expiry first; skipping a nearer-expiry lot requires a reason).
2. **Depot checkpoint** (`DEPOT_COLLECTING`) — staff scans each item's barcode
   (GTIN→NDC/lot/expiry/serial) + enters count. Continuous depot **surveillance feed
   is a Phase 3 plug-in**; Phase 1 records the deterministic barcode+count result into
   `depot_checkpoint`. Blocks: NDC/lot/expiry mismatch, expired, serial duplicate.
3. **Transport** (`IN_TRANSIT`).
4. **Shelf placement** (`SHELF_PLACING`) — **dual gate**:
   - *Barcode gate:* re-scan; cross-check against staged lot; same blocks as depot +
     expiry<today BLOCK, expiry<30d WARN (supervisor PIN).
   - *AI camera gate:* `POST /inventory/ai/shelf-verify` (§1.2 envelope) — count delta
     (0=pass, ±1=warn+manual recount, ≥±2=block+override), drug-name OCR match, form
     match, confidence<0.75 ⇒ advisory only. **Phase 1: graceful-degradation stub**
     (no model ⇒ `degraded:true`, advisory, staff manual confirm — never silently passes).
   - *Reconcile* depot-out count vs shelf-in count; discrepancy ⇒ logged.
5. **Finalize & commit** (`COMPLETE`) — enforce in order:
   - Cold chain: if drug `storage_condition` REFRIGERATED/FROZEN ⇒ mandatory
     `temperature_logged_c`; out of range ⇒ **BLOCK** + `cold_chain_breach` + owner alert.
   - Capacity: `staged + current_units > capacity_units` ⇒ WARN (logged if overridden).
   - Near-expiry positioning: if shelf already holds same NDC with **later** expiry ⇒
     mandatory front-of-shelf placement confirmation.
   - **Pharmacist attestation (enforced at API):** if `high_risk_flag` or controlled or
     LASA ⇒ `POST /inventory/replenishment/{id}/shelf-place` **rejects without
     `pharmacist_attestation_by` + PIN**.
   - Split pack: partial box ⇒ lot `split_pack_open=true` + `split_pack_remaining_blisters`;
     next transfer prompts split pack first (FEFO).
   - Commit: `shelf_placements` upsert (+units), `inventory_lots` location/qty reflect,
     `pharmacy_shelves.current_units` +, `shelf_transfer_events` row, `stock_levels` synced.
   - Primary location: if `shelf_id != drug.primary_shelf_id` ⇒ one-tap prompt to update
     (surfaced to dispensing in PatientPanel/VerificationCenter).
   - Shift handover: session accrues into `shift_handover_reports` at shift close.

## 4b. Cameras & count-source (hardware reality)

Two distinct camera roles — never conflated:
- **Count source (precision):** a **scan-station camera + barcode** is the authoritative
  identity+count signal (posed, controlled, lit; reads count/expiry/lot/form per scan).
  Optional **load-cell shelves** give hands-free continuous count. These plug in behind a
  single `count_source` interface (`manual | scan_station | load_cell`) so hardware tiers
  swap without reworking the workflow. Phase 1 ships `manual` + `scan_station` (enveloped).
- **Surveillance (security/behavior):** **10 wall-mounted cameras** do anomaly / wrong-bin /
  extra-item / behavior / unscanned-pick / after-hours detection → write `surveillance_events`
  → surface in an **owner surveillance panel** + notify owner on high severity. These are
  **advisory/forensic only — they NEVER block a transfer or act as the counter**; they flag
  for human (owner) review. Real multi-camera ML inference is Phase 3 (GPU/edge); Phase 1
  ships the `surveillance_events` model + create/list endpoints + owner-report inclusion so
  the layer slots in without schema churn.

## 5. API (Phase 1 — deterministic core)

All routes `require_permission("inventory:write" | "inventory:admin")`.
- `POST /inventory/replenishment/session` — build session from pick list.
- `POST /inventory/replenishment/{id}/depot-collect` — barcode + count checkpoint.
- `POST /inventory/ai/shelf-verify` — §1.2 envelope, graceful degradation (Phase 1 stub).
- `POST /inventory/replenishment/{id}/shelf-place` — dual-gate finalize; **pharmacist
  attestation for high-risk enforced server-side** (reject 4xx without it).
- `GET  /inventory/replenishment/{id}` / `GET /inventory/shelves` / `GET /inventory/shift-report/{id}`.

`shelf-verify` envelope: `{ result: { counted_items, count_confidence, count_delta,
count_verdict, drug_name_ocr, drug_name_match, drug_form_detected, drug_form_match,
bounding_boxes, advisory_notes }, tier_used, confidence, degraded, options_offline }`.

## 6. Phasing (decomposition)

- **Phase 1 (this spec → plan → build):** schema 0012, replenishment workflow + state
  machine, two-checkpoint deterministic verification, all §3 safety enforcement,
  `shelf-verify` enveloped degraded stub, reconciliation, shift handover. Verified: `pytest`, `tsc`.
- **Phase 2:** multi-step frontend flow + 7 Playwright specs (`tests/e2e/shelf-transfer-verification.spec.ts`).
- **Phase 3 (GPU-deferred):** real YOLOv8 / PaddleOCR / MobileNet inference for the
  scan-station count + the **10 wall-camera surveillance ML** (anomaly / wrong-bin /
  extra-item / behavior) feeding the owner panel. (This box cannot train/run these —
  established hardware limit; Phase 1 ships the deterministic spine + envelopes + event model.)

## 7. Constraints

- Both gates mandatory; bypass = supervisor PIN + logged reason — never silently skippable.
- All AI local + §1.2 envelope + graceful degradation; never silently accept low-confidence.
- Schema additive only; no existing columns modified.
- Pharmacist attestation for high-risk enforced at the API layer, not just UI.
- `tsc --noEmit` passes; existing Playwright specs still pass; new spec added in Phase 2.

## 8. Test plan (Phase 1 backend)

`pytest` unit tests: FEFO ordering + reason-gated skip; barcode block on
NDC/lot/expiry/serial-dup/expired; AI verdict thresholds (pass/warn/block) on the
stub; cold-chain out-of-range block; capacity warn; near-expiry positioning prompt;
high-risk attestation rejection at API; split-pack blister decrement; depot↔shelf
reconciliation discrepancy; depot-qty = lot − Σ placements invariant.
(Playwright happy-path + 6 scenarios land in Phase 2.)
