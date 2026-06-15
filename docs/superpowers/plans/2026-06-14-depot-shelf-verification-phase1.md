# Depot→Shelf Dual-Verification — Phase 1 (Backend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the deterministic backend core of the regulated depot→shelf replenishment flow: schema, FEFO pick-list, two reconciled verification checkpoints (barcode + enveloped AI), all safety enforcement (cold-chain, capacity, near-expiry, API-enforced pharmacist attestation, split-pack), the surveillance-event seam, and shift handover.

**Architecture:** Model A — `inventory_lots` stays the batch source of truth; a new location/placement layer (`pharmacy_shelves`, `shelf_placements`), a `replenishment_sessions` state machine, a `shelf_transfer_events` ledger, plus `surveillance_events` and `shift_handover_reports`. Depot qty is computed (`lot.quantity_on_hand − Σ placements`). AI (`shelf-verify`) is behind the §1.2 envelope with graceful degradation; real CV is Phase 3. Frontend is Phase 2.

**Tech Stack:** FastAPI + SQLAlchemy (async) · Alembic (additive migration 0012, runs on boot) · Postgres · pytest. Python at `/Users/sashad85/miniforge3/bin/python`.

**Conventions to follow:** models extend `AuditedBase` (`shared/models/base.py`); routers under `services/platform/routers/` registered in `services/platform/main.py`; `require_permission("inventory:write"|"inventory:admin")` from `services/platform/auth`; unit tests in `tests/unit/`. Run tests: `cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python -m pytest tests/unit -q`.

---

## File structure

- Create `shared/models/depot.py` — new ORM models (shelves, placements, sessions, transfer events, surveillance events, shift reports) + enums.
- Modify `shared/models/inventory.py` — additive columns on `InventoryLot`, `DrugProduct`.
- Modify `shared/models/__init__.py` + `services/platform/main.py` — import new models.
- Create `data/migrations/versions/0012_depot_shelf_verification.py` — additive migration (chains 0011).
- Create `services/core/inventory/replenishment.py` — pure deterministic logic (FEFO, barcode gate, reconciliation, safety verdicts, depot-qty invariant). No I/O — fully unit-testable.
- Create `services/ai/shelf_vision/verifier.py` — §1.2 enveloped `shelf_verify()` with graceful degradation (no model → degraded advisory).
- Create `services/platform/routers/depot_transfer.py` — endpoints (session, depot-collect, shelf-verify, shelf-place, surveillance, shift-report), permission-gated, audit-logged, attestation enforced server-side.
- Create tests: `tests/unit/test_replenishment_logic.py`, `tests/unit/test_shelf_vision_envelope.py`, `tests/unit/test_depot_transfer_endpoints.py`.

---

## Task 1: Schema — models + migration 0012 (additive)

**Files:**
- Create: `shared/models/depot.py`
- Modify: `shared/models/inventory.py` (after `InventoryLot`/`DrugProduct` column blocks)
- Modify: `shared/models/__init__.py`, `services/platform/main.py`
- Create: `data/migrations/versions/0012_depot_shelf_verification.py`

- [ ] **Step 1: Add additive columns to `inventory.py`**

In `shared/models/inventory.py`, inside `class InventoryLot`, after `is_quarantined`:
```python
    split_pack_open: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    split_pack_remaining_blisters: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cold_chain_breach: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    cold_chain_breach_log: Mapped[list | None] = mapped_column(JSONB, nullable=True)
```
Inside `class DrugProduct`, after `drug_db_metadata`:
```python
    storage_condition: Mapped[str | None] = mapped_column(String(20), nullable=True)  # ROOM_TEMP|REFRIGERATED|FROZEN|LIGHT_PROTECTED
    high_risk_flag: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lasa_group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    primary_shelf_id: Mapped[UUID | None] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=True)
    blisters_per_box: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_per_blister: Mapped[int | None] = mapped_column(Integer, nullable=True)
```

- [ ] **Step 2: Create `shared/models/depot.py`**

```python
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import AuditedBase

STORAGE_CONDITIONS = ("ROOM_TEMP", "REFRIGERATED", "FROZEN", "LIGHT_PROTECTED")
SESSION_STATES = ("PICK_LIST", "DEPOT_COLLECTING", "IN_TRANSIT", "SHELF_PLACING", "COMPLETE", "CANCELLED")
SURVEILLANCE_EVENT_TYPES = ("ANOMALY", "WRONG_BIN", "EXTRA_ITEM", "BEHAVIOR", "UNSCANNED_PICK", "AFTER_HOURS")


class PharmacyShelf(AuditedBase):
    __tablename__ = "pharmacy_shelves"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(40), nullable=False)        # e.g. "A-03-B-05"
    zone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    capacity_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # denormalized cache of Σ placements
    storage_condition: Mapped[str] = mapped_column(String(20), nullable=False, default="ROOM_TEMP")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class ShelfPlacement(AuditedBase):
    __tablename__ = "shelf_placements"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    inventory_lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False, index=True)
    shelf_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=False, index=True)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False, index=True)
    units: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    placed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReplenishmentSession(AuditedBase):
    __tablename__ = "replenishment_sessions"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PICK_LIST", index=True)
    pick_list: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    depot_checkpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconciliation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShelfTransferEvent(AuditedBase):
    __tablename__ = "shelf_transfer_events"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("replenishment_sessions.id"), nullable=True, index=True)
    inventory_lot_id: Mapped[UUID] = mapped_column(ForeignKey("inventory_lots.id"), nullable=False)
    shelf_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacy_shelves.id"), nullable=False)
    ndc11: Mapped[str] = mapped_column(String(11), nullable=False)
    quantity_delta: Mapped[int] = mapped_column(Integer, nullable=False)
    performed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    barcode_verification_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ai_verification_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    depot_checkpoint_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    pharmacist_attestation_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    pharmacist_attestation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    temperature_logged_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1), nullable=True)
    near_expiry_placement_confirmed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class SurveillanceEvent(AuditedBase):
    __tablename__ = "surveillance_events"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    camera_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    session_id: Mapped[UUID | None] = mapped_column(ForeignKey("replenishment_sessions.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(10), nullable=False, default="low")
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    clip_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reviewed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    owner_notified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ShiftHandoverReport(AuditedBase):
    __tablename__ = "shift_handover_reports"
    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    shift_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    shift_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    performed_by: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    transfers_completed: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    fefo_overrides: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    anomaly_signals: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    open_split_packs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    cold_chain_events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
```

- [ ] **Step 3: Register models for mapper resolution**

In `shared/models/__init__.py` add near the inventory import:
```python
from . import depot       # noqa: F401  (PharmacyShelf, ShelfPlacement, ReplenishmentSession, ShelfTransferEvent, SurveillanceEvent, ShiftHandoverReport)
```
In `services/platform/main.py`, after the inventory model import block:
```python
from shared.models.depot import (  # noqa: F401
    PharmacyShelf, ShelfPlacement, ReplenishmentSession,
    ShelfTransferEvent, SurveillanceEvent, ShiftHandoverReport,
)
```

- [ ] **Step 4: Create migration `0012_depot_shelf_verification.py`**

```python
"""depot shelf dual-verification

Revision ID: 0012
Revises: 0011
Create Date: 2026-06-14
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def _audit():
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("updated_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _id():
    return sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text("uuid_generate_v4()"))


def upgrade() -> None:
    op.create_table("pharmacy_shelves", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("label", sa.String(40), nullable=False),
        sa.Column("zone", sa.String(40), nullable=True),
        sa.Column("capacity_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("storage_condition", sa.String(20), nullable=False, server_default="ROOM_TEMP"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        *_audit())
    op.create_index("ix_pharmacy_shelves_pharmacy_id", "pharmacy_shelves", ["pharmacy_id"])

    op.create_table("shelf_placements", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("inventory_lot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_lots.id"), nullable=False),
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("placed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        *_audit())
    op.create_index("ix_shelf_placements_lot", "shelf_placements", ["inventory_lot_id"])
    op.create_index("ix_shelf_placements_shelf", "shelf_placements", ["shelf_id"])

    op.create_table("replenishment_sessions", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="PICK_LIST"),
        sa.Column("pick_list", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("depot_checkpoint", postgresql.JSONB, nullable=True),
        sa.Column("reconciliation", postgresql.JSONB, nullable=True),
        sa.Column("started_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_audit())
    op.create_index("ix_replenishment_sessions_pharmacy", "replenishment_sessions", ["pharmacy_id"])

    op.create_table("shelf_transfer_events", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_sessions.id"), nullable=True),
        sa.Column("inventory_lot_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("inventory_lots.id"), nullable=False),
        sa.Column("shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=False),
        sa.Column("ndc11", sa.String(11), nullable=False),
        sa.Column("quantity_delta", sa.Integer(), nullable=False),
        sa.Column("performed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("barcode_verification_result", postgresql.JSONB, nullable=True),
        sa.Column("ai_verification_result", postgresql.JSONB, nullable=True),
        sa.Column("depot_checkpoint_result", postgresql.JSONB, nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("override_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacist_attestation_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("pharmacist_attestation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature_logged_c", sa.Numeric(4, 1), nullable=True),
        sa.Column("near_expiry_placement_confirmed", sa.Boolean(), nullable=False, server_default="false"),
        *_audit())
    op.create_index("ix_shelf_transfer_events_session", "shelf_transfer_events", ["session_id"])

    op.create_table("surveillance_events", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("camera_id", sa.String(40), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("replenishment_sessions.id"), nullable=True),
        sa.Column("event_type", sa.String(20), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False, server_default="low"),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clip_ref", sa.Text(), nullable=True),
        sa.Column("ai_result", postgresql.JSONB, nullable=True),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("owner_notified", sa.Boolean(), nullable=False, server_default="false"),
        *_audit())
    op.create_index("ix_surveillance_events_pharmacy", "surveillance_events", ["pharmacy_id"])
    op.create_index("ix_surveillance_events_type", "surveillance_events", ["event_type"])

    op.create_table("shift_handover_reports", _id(),
        sa.Column("pharmacy_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacies.id"), nullable=False),
        sa.Column("shift_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("shift_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("performed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("transfers_completed", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("fefo_overrides", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("anomaly_signals", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("open_split_packs", postgresql.JSONB, nullable=False, server_default="[]"),
        sa.Column("cold_chain_events", postgresql.JSONB, nullable=False, server_default="[]"),
        *_audit())
    op.create_index("ix_shift_handover_reports_pharmacy", "shift_handover_reports", ["pharmacy_id"])

    # additive columns
    op.add_column("inventory_lots", sa.Column("split_pack_open", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("inventory_lots", sa.Column("split_pack_remaining_blisters", sa.Integer(), nullable=True))
    op.add_column("inventory_lots", sa.Column("cold_chain_breach", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("inventory_lots", sa.Column("cold_chain_breach_log", postgresql.JSONB, nullable=True))
    op.add_column("drug_products", sa.Column("storage_condition", sa.String(20), nullable=True))
    op.add_column("drug_products", sa.Column("high_risk_flag", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("drug_products", sa.Column("lasa_group", sa.String(80), nullable=True))
    op.add_column("drug_products", sa.Column("primary_shelf_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("pharmacy_shelves.id"), nullable=True))
    op.add_column("drug_products", sa.Column("blisters_per_box", sa.Integer(), nullable=True))
    op.add_column("drug_products", sa.Column("units_per_blister", sa.Integer(), nullable=True))


def downgrade() -> None:
    for col in ("units_per_blister", "blisters_per_box", "primary_shelf_id", "lasa_group", "high_risk_flag", "storage_condition"):
        op.drop_column("drug_products", col)
    for col in ("cold_chain_breach_log", "cold_chain_breach", "split_pack_remaining_blisters", "split_pack_open"):
        op.drop_column("inventory_lots", col)
    for t in ("shift_handover_reports", "surveillance_events", "shelf_transfer_events",
              "replenishment_sessions", "shelf_placements", "pharmacy_shelves"):
        op.drop_table(t)
```

- [ ] **Step 5: Apply migration + verify import**

Run: `cd /Users/sashad85/PharmPilot-Claude && /Users/sashad85/miniforge3/bin/python -c "import shared.models.depot, shared.models.inventory; print('models import ok')"`
Expected: `models import ok`
Run (apply on a live DB if available): restart the API or `alembic upgrade head`; confirm `alembic_version` = `0012`.

- [ ] **Step 6: Commit**

```bash
git add shared/models/depot.py shared/models/inventory.py shared/models/__init__.py services/platform/main.py data/migrations/versions/0012_depot_shelf_verification.py
git commit -m "feat(depot): schema 0012 — shelves, placements, sessions, transfer/surveillance/handover"
```

---

## Task 2: Deterministic replenishment logic (pure, unit-tested)

All pure functions — no DB, no I/O — so they're fully testable and hold the safety rules in one auditable place.

**Files:**
- Create: `services/core/inventory/replenishment.py`
- Test: `tests/unit/test_replenishment_logic.py`

- [ ] **Step 1: Write failing tests**

```python
from datetime import date, timedelta
from services.core.inventory import replenishment as R


def _lot(lot_id, expiry, qty, ndc="00000000001", placed=0):
    return {"id": lot_id, "expiry_date": expiry, "quantity_on_hand": qty, "ndc11": ndc, "placed_units": placed}


def test_fefo_orders_earliest_expiry_first():
    today = date(2026, 6, 14)
    lots = [_lot("b", today + timedelta(days=90), 50), _lot("a", today + timedelta(days=10), 50)]
    ordered = R.fefo_order(lots)
    assert [l["id"] for l in ordered] == ["a", "b"]


def test_depot_qty_is_lot_minus_placements():
    assert R.depot_units(_lot("a", date(2026, 12, 1), 100, placed=30)) == 70


def test_barcode_gate_blocks_ndc_mismatch():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1)}
    scan = {"ndc11": "222", "lot_number": "L1", "expiry_date": date(2027, 1, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "block" and "ndc" in v["reason"].lower()


def test_barcode_gate_blocks_expired():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 1)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "block" and "expir" in v["reason"].lower()


def test_barcode_gate_warns_near_expiry():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 30)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 30), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "warn"


def test_barcode_gate_blocks_duplicate_serial():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials={"S1"})
    assert v["verdict"] == "block" and "serial" in v["reason"].lower()


def test_ai_count_verdict_thresholds():
    assert R.ai_count_verdict(0) == "pass"
    assert R.ai_count_verdict(1) == "warn"
    assert R.ai_count_verdict(-1) == "warn"
    assert R.ai_count_verdict(2) == "block"


def test_cold_chain_block_out_of_range():
    v = R.cold_chain_check("REFRIGERATED", logged_c=12.0)
    assert v["verdict"] == "block"
    assert R.cold_chain_check("REFRIGERATED", logged_c=5.0)["verdict"] == "pass"
    assert R.cold_chain_check("REFRIGERATED", logged_c=None)["verdict"] == "block"  # missing temp required


def test_capacity_warns_over_capacity():
    v = R.capacity_check(staged=40, current=70, capacity=100)
    assert v["verdict"] == "warn"
    assert R.capacity_check(staged=10, current=10, capacity=100)["verdict"] == "pass"


def test_attestation_required_for_high_risk():
    assert R.requires_pharmacist_attestation({"high_risk_flag": True, "is_controlled": False, "lasa_group": None}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": True, "lasa_group": None}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": False, "lasa_group": "INSULIN"}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": False, "lasa_group": None}) is False


def test_reconcile_flags_discrepancy():
    assert R.reconcile(depot_out=10, shelf_in=10)["match"] is True
    bad = R.reconcile(depot_out=10, shelf_in=8)
    assert bad["match"] is False and bad["delta"] == -2


def test_split_pack_decrement():
    assert R.split_pack_remaining(blisters_per_box=10, blisters_taken=4) == 6
```

- [ ] **Step 2: Run, verify fail**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_replenishment_logic.py -q`
Expected: FAIL (module not found).

- [ ] **Step 3: Implement `services/core/inventory/replenishment.py`**

```python
"""Deterministic depot→shelf replenishment logic. Pure functions; no I/O.
Single auditable home for the regulated safety rules."""
from __future__ import annotations
from datetime import date, timedelta
from typing import Any

NEAR_EXPIRY_WARN_DAYS = 30
COLD_RANGES = {"REFRIGERATED": (2.0, 8.0), "FROZEN": (-25.0, -10.0)}


def fefo_order(lots: list[dict]) -> list[dict]:
    return sorted(lots, key=lambda l: l["expiry_date"])


def depot_units(lot: dict) -> int:
    return int(lot["quantity_on_hand"]) - int(lot.get("placed_units", 0))


def barcode_gate(staged: dict, scan: dict, *, today: date, seen_serials: set[str]) -> dict:
    def block(reason): return {"verdict": "block", "reason": reason}
    if scan.get("ndc11") != staged.get("ndc11"):
        return block("NDC mismatch")
    if scan.get("lot_number") != staged.get("lot_number"):
        return block("Lot mismatch")
    if scan.get("expiry_date") != staged.get("expiry_date"):
        return block("Expiry mismatch")
    serial = scan.get("serial")
    if serial and serial in seen_serials:
        return block("Serial duplicate (already scanned/dispensed)")
    exp = scan.get("expiry_date")
    if exp is not None and exp < today:
        return block("Expired — never allow to shelf")
    if exp is not None and exp < today + timedelta(days=NEAR_EXPIRY_WARN_DAYS):
        return {"verdict": "warn", "reason": "Expires within 30 days — supervisor PIN required"}
    return {"verdict": "pass", "reason": ""}


def ai_count_verdict(delta: int) -> str:
    a = abs(int(delta))
    if a == 0:
        return "pass"
    if a == 1:
        return "warn"
    return "block"


def cold_chain_check(storage_condition: str | None, logged_c: float | None) -> dict:
    rng = COLD_RANGES.get((storage_condition or "").upper())
    if rng is None:
        return {"verdict": "pass", "reason": ""}  # not cold-chain
    if logged_c is None:
        return {"verdict": "block", "reason": "Temperature required for cold-chain item"}
    lo, hi = rng
    if lo <= float(logged_c) <= hi:
        return {"verdict": "pass", "reason": ""}
    return {"verdict": "block", "reason": f"Temp {logged_c}C outside {lo}-{hi}C — cold chain breach"}


def capacity_check(*, staged: int, current: int, capacity: int) -> dict:
    if capacity and staged + current > capacity:
        pct = round((staged + current) / capacity * 100)
        return {"verdict": "warn", "reason": f"Shelf will be at {pct}% capacity"}
    return {"verdict": "pass", "reason": ""}


def requires_pharmacist_attestation(drug: dict) -> bool:
    return bool(drug.get("high_risk_flag") or drug.get("is_controlled") or drug.get("lasa_group"))


def reconcile(*, depot_out: int, shelf_in: int) -> dict:
    delta = int(shelf_in) - int(depot_out)
    return {"match": delta == 0, "delta": delta, "depot_out": depot_out, "shelf_in": shelf_in}


def split_pack_remaining(*, blisters_per_box: int, blisters_taken: int) -> int:
    return int(blisters_per_box) - int(blisters_taken)
```

- [ ] **Step 4: Run, verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_replenishment_logic.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add services/core/inventory/replenishment.py tests/unit/test_replenishment_logic.py
git commit -m "feat(depot): deterministic FEFO + verification + safety logic (pure, tested)"
```

---

## Task 3: AI `shelf-verify` — §1.2 envelope with graceful degradation

**Files:**
- Create: `services/ai/shelf_vision/__init__.py` (empty), `services/ai/shelf_vision/verifier.py`
- Test: `tests/unit/test_shelf_vision_envelope.py`

- [ ] **Step 1: Write failing tests**

```python
from services.ai.shelf_vision.verifier import shelf_verify


def test_degrades_gracefully_with_no_model():
    env = shelf_verify(image_base64=None, staged_ndc="111", staged_lot="L1",
                       staged_quantity=10, expected_drug_name="metformin", expected_drug_form="tablet")
    assert env["degraded"] is True
    assert env["tier_used"] == "local"
    r = env["result"]
    assert r["count_verdict"] in ("pass", "warn", "block")
    assert r["counted_items"] is None          # no model → cannot count
    assert "manual confirmation" in " ".join(r["advisory_notes"]).lower()


def test_envelope_shape():
    env = shelf_verify(image_base64=None, staged_ndc="111", staged_lot="L1",
                       staged_quantity=5, expected_drug_name="x", expected_drug_form="tablet")
    for k in ("result", "tier_used", "confidence", "degraded", "options_offline"):
        assert k in env
    for k in ("counted_items", "count_confidence", "count_delta", "count_verdict",
              "drug_name_ocr", "drug_name_match", "drug_form_detected", "drug_form_match",
              "bounding_boxes", "advisory_notes"):
        assert k in env["result"]
```

- [ ] **Step 2: Run, verify fail**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_shelf_vision_envelope.py -q`
Expected: FAIL (import error).

- [ ] **Step 3: Implement `services/ai/shelf_vision/verifier.py`**

```python
"""Shelf-verification vision endpoint logic, §1.2 envelope.
Phase 1: graceful-degradation stub — real YOLOv8/PaddleOCR/MobileNet is Phase 3 (GPU).
Never silently 'passes': with no model the result is advisory and demands manual
confirmation, exactly like the platform's other local-AI surfaces."""
from __future__ import annotations
import os
from typing import Any

_MODEL_AVAILABLE = bool(os.environ.get("SHELF_VISION_MODEL"))  # set when Phase-3 weights present


def _envelope(result: dict, degraded: bool, confidence: float) -> dict:
    return {
        "result": result,
        "tier_used": "local",
        "confidence": confidence,
        "degraded": degraded,
        "options_offline": [] if not degraded else ["yolo_count", "label_ocr", "form_classifier"],
    }


def _degraded_result(advisory: str) -> dict:
    return {
        "counted_items": None, "count_confidence": 0.0, "count_delta": None,
        "count_verdict": "warn",  # advisory → staff must manually confirm; never auto-pass
        "drug_name_ocr": None, "drug_name_match": None,
        "drug_form_detected": None, "drug_form_match": None,
        "bounding_boxes": [], "advisory_notes": [advisory],
    }


def shelf_verify(*, image_base64: str | None, staged_ndc: str, staged_lot: str,
                 staged_quantity: int, expected_drug_name: str, expected_drug_form: str) -> dict:
    if not _MODEL_AVAILABLE or not image_base64:
        return _envelope(
            _degraded_result("Vision model unavailable — manual confirmation required (Phase 3 enables auto count/OCR)."),
            degraded=True, confidence=0.0,
        )
    # Phase 3 will run YOLOv8 + PaddleOCR + MobileNet here and populate a real result.
    # Until weights ship, treat presence-without-inference as degraded to avoid silent pass.
    return _envelope(
        _degraded_result("Vision inference not yet wired — manual confirmation required."),
        degraded=True, confidence=0.0,
    )
```

- [ ] **Step 4: Run, verify pass**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_shelf_vision_envelope.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/ai/shelf_vision/ tests/unit/test_shelf_vision_envelope.py
git commit -m "feat(depot): shelf-verify §1.2 envelope w/ graceful degradation (Phase-1 stub)"
```

---

## Task 4: Router — endpoints, permissions, attestation enforcement, audit, commit

**Files:**
- Create: `services/platform/routers/depot_transfer.py`
- Modify: `services/platform/main.py` (register router)
- Test: `tests/unit/test_depot_transfer_endpoints.py`

Reuse the audit + permission patterns from `services/platform/routers/inventory.py` and the
`ClinicalAuditLog`-style logging used in `cds.py` (write an audit row per state change).
The **pharmacist attestation gate is enforced here, server-side** — `shelf-place` returns
HTTP 422 when the drug requires attestation and `pharmacist_attestation_by`/PIN is absent.

- [ ] **Step 1: Write failing endpoint tests** (stub DB like `tests/unit/test_med_reconciliation.py`'s `StubDb`)

```python
import pytest
from fastapi import HTTPException
from services.platform.routers import depot_transfer as D


def test_shelf_place_rejects_high_risk_without_attestation():
    body = D.ShelfPlaceRequest(
        session_id="00000000-0000-0000-0000-000000000001",
        shelf_id="00000000-0000-0000-0000-000000000002",
        inventory_lot_id="00000000-0000-0000-0000-000000000003",
        ndc11="111", quantity=10,
        barcode_scans=[], ai_verification={"count_verdict": "pass"},
        temperature_logged_c=None,
        pharmacist_attestation_by=None, pharmacist_attestation_pin=None,
    )
    drug = {"high_risk_flag": True, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(body, drug=drug, shelf={"capacity_units": 100, "current_units": 0})
    assert exc.value.status_code == 422
    assert "attestation" in str(exc.value.detail).lower()


def test_finalize_guards_block_cold_chain_out_of_range():
    body = D.ShelfPlaceRequest(
        session_id="00000000-0000-0000-0000-000000000001",
        shelf_id="00000000-0000-0000-0000-000000000002",
        inventory_lot_id="00000000-0000-0000-0000-000000000003",
        ndc11="111", quantity=10, barcode_scans=[], ai_verification={"count_verdict": "pass"},
        temperature_logged_c=12.0, pharmacist_attestation_by=None, pharmacist_attestation_pin=None,
    )
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": "REFRIGERATED"}
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(body, drug=drug, shelf={"capacity_units": 100, "current_units": 0})
    assert exc.value.status_code == 422
    assert "cold chain" in str(exc.value.detail).lower()


def test_finalize_guards_pass_when_clean():
    body = D.ShelfPlaceRequest(
        session_id="00000000-0000-0000-0000-000000000001",
        shelf_id="00000000-0000-0000-0000-000000000002",
        inventory_lot_id="00000000-0000-0000-0000-000000000003",
        ndc11="111", quantity=10, barcode_scans=[], ai_verification={"count_verdict": "pass"},
        temperature_logged_c=None, pharmacist_attestation_by=None, pharmacist_attestation_pin=None,
    )
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    # should not raise
    D._enforce_finalize_guards(body, drug=drug, shelf={"capacity_units": 100, "current_units": 0})
```

- [ ] **Step 2: Run, verify fail**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_depot_transfer_endpoints.py -q`
Expected: FAIL (import error).

- [ ] **Step 3: Implement `services/platform/routers/depot_transfer.py`**

Define the Pydantic request models (`SessionCreateRequest`, `DepotCollectRequest`, `ShelfVerifyRequest`, `ShelfPlaceRequest`, `SurveillanceEventRequest`) and a pure guard helper `_enforce_finalize_guards(body, *, drug, shelf)` that calls `replenishment.cold_chain_check`, `capacity_check` (warn, non-blocking → recorded), and `requires_pharmacist_attestation` (→ raise `HTTPException(422, "pharmacist attestation required")` if attestation missing), plus AI verdict `block` → require `override_reason` + `override_by`. Endpoints:
```python
@router.post("/replenishment/session")          # build session from FEFO pick list
@router.post("/replenishment/{sid}/depot-collect")  # barcode gate + count → depot_checkpoint
@router.post("/ai/shelf-verify")                 # calls shelf_vision.verifier.shelf_verify
@router.post("/replenishment/{sid}/shelf-place") # dual-gate + _enforce_finalize_guards + commit
@router.get("/replenishment/{sid}")
@router.get("/shelves")
@router.post("/surveillance/events")             # create (camera/manual) → owner panel
@router.get("/surveillance/events")
@router.post("/shift-report")                    # generate handover summary
```
All gated `require_permission("inventory:write")` (admin for surveillance/shift-report). Commit logic in `shelf-place`: upsert `ShelfPlacement` (+units), decrement effective depot via lot, increment `PharmacyShelf.current_units`, write `ShelfTransferEvent` (with the JSONB verification blobs + attestation), sync `StockLevel`, and if `shelf_id != drug.primary_shelf_id` include a `primary_location_prompt` in the response. Keep `_enforce_finalize_guards` pure (no DB) so it's unit-tested above.

- [ ] **Step 4: Register router in `main.py`**

Add `depot_transfer` to the router import tuple and:
```python
app.include_router(depot_transfer.router, prefix="/api/v1/inventory", tags=["depot transfer"])
```

- [ ] **Step 5: Run tests + tsc-irrelevant backend check**

Run: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_depot_transfer_endpoints.py -q`
Expected: PASS.
Run full suite: `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit -q`
Expected: all prior tests still pass + new ones.

- [ ] **Step 6: Commit**

```bash
git add services/platform/routers/depot_transfer.py services/platform/main.py tests/unit/test_depot_transfer_endpoints.py
git commit -m "feat(depot): transfer endpoints — dual-gate, server-enforced attestation, audit, commit"
```

---

## Task 5: Shift handover aggregation

**Files:**
- Modify: `services/core/inventory/replenishment.py` (add `build_shift_summary`)
- Test: add to `tests/unit/test_replenishment_logic.py`

- [ ] **Step 1: Write failing test**

```python
def test_build_shift_summary_groups_sections():
    events = [{"ndc11": "111", "quantity_delta": 10, "shelf_id": "s1", "override_reason": "FEFO skip: damaged"}]
    splits = [{"lot": "L1", "remaining": 6}]
    cold = [{"ndc11": "222", "temp": 12.0}]
    s = R.build_shift_summary(transfer_events=events, open_split_packs=splits, cold_chain_events=cold)
    assert s["transfers_completed"][0]["ndc11"] == "111"
    assert s["fefo_overrides"] and "FEFO skip" in s["fefo_overrides"][0]["reason"]
    assert s["open_split_packs"] == splits
    assert s["cold_chain_events"] == cold
```

- [ ] **Step 2: Run → fail.** `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit/test_replenishment_logic.py::test_build_shift_summary_groups_sections -q` → Expected: FAIL (function not defined).

- [ ] **Step 3: Implement**

```python
def build_shift_summary(*, transfer_events: list[dict], open_split_packs: list[dict],
                        cold_chain_events: list[dict]) -> dict:
    return {
        "transfers_completed": [
            {"ndc11": e["ndc11"], "quantity_delta": e["quantity_delta"], "shelf_id": e.get("shelf_id")}
            for e in transfer_events
        ],
        "fefo_overrides": [
            {"ndc11": e["ndc11"], "reason": e["override_reason"]}
            for e in transfer_events if e.get("override_reason")
        ],
        "anomaly_signals": [],
        "open_split_packs": open_split_packs,
        "cold_chain_events": cold_chain_events,
    }
```

- [ ] **Step 4: Run → pass.** `pytest tests/unit/test_replenishment_logic.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/core/inventory/replenishment.py tests/unit/test_replenishment_logic.py
git commit -m "feat(depot): shift handover summary aggregation"
```

---

## Final verification

- [ ] Run `/Users/sashad85/miniforge3/bin/python -m pytest tests/unit -q` → all pass (baseline + new).
- [ ] Restart API (applies migration 0012) → confirm `/openapi.json` lists `/api/v1/inventory/replenishment/session` and `/api/v1/inventory/ai/shelf-verify`.
- [ ] Confirm no existing endpoints changed (additive only).

## Out of scope (later phases)
- **Phase 2:** multi-step frontend flow (pick list → depot collect → shelf dual-gate → confirm) + owner surveillance panel + `tests/e2e/shelf-transfer-verification.spec.ts` (7 specs).
- **Phase 3 (GPU):** real YOLOv8/PaddleOCR/MobileNet inference for scan-station count + 10 wall-camera surveillance ML; `count_source=load_cell` driver.
