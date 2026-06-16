"""
Seed a realistic inventory snapshot so the Inventory-AI dashboards render real
data (turnover, dead-stock, expiry-risk, supply-risk, procurement, stockout).

Creates per demo pharmacy:
  - ~16 drug_products (common outpatient meds + 1 cold-chain)
  - one stock_levels row each, with the ML-precomputed fields populated
    (avg_daily_demand, reorder_point, safety_stock, par_level_max,
     stockout_probability_7d, last_dispensed_at) so the intelligence
    endpoints have signal without months of simulated dispensing
  - 1-2 inventory_lots each, with varied expiry (some <30d = expiry-risk,
    some dead/no-movement, some healthy) and unit_cost for value math

The distribution is engineered to exercise every panel:
  fast movers, slow movers, dead stock (no dispense ~200d), below-reorder
  items (procurement fires), near-expiry lots, a high stockout-risk item,
  and a refrigerated (cold-chain) drug.

Idempotent: re-running replaces this pharmacy's stock for these NDCs.

Usage:
    DATABASE_URL=... python scripts/seed_inventory.py
    python scripts/seed_inventory.py --reset   # also clears movements ledger
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot",
)
PHARMACY_NAME = "PharmPilot Demo%"  # ILIKE — matches "PharmPilot Demo" / "… Pharmacy"

# (ndc11, brand, generic, strength, form, storage, unit_cost, on_hand,
#  avg_daily_demand, last_dispensed_days_ago, reorder_point, safety_stock,
#  par_max, stockout_prob_7d, lots=[(lot_no, expiry_days_from_now, qty)])
# on_hand must equal sum of lot quantities.
DRUGS = [
    # --- healthy fast movers ---
    ("00093721256", "Glucophage", "Metformin HCl", "500mg", "Tablet", "ROOM_TEMP",
     0.05, 600, 12.0, 1, 120, 60, 900, 0.04, [("MET500-A", 400, 600)]),
    ("00071015423", "Lipitor", "Atorvastatin", "20mg", "Tablet", "ROOM_TEMP",
     0.12, 400, 9.0, 1, 90, 45, 600, 0.06, [("ATO20-A", 380, 400)]),
    ("00069152430", "Norvasc", "Amlodipine", "5mg", "Tablet", "ROOM_TEMP",
     0.06, 250, 5.0, 2, 60, 30, 400, 0.10, [("AML5-A", 300, 250)]),
    ("00093014956", "Prilosec", "Omeprazole", "20mg", "Capsule", "ROOM_TEMP",
     0.10, 500, 8.0, 1, 80, 40, 700, 0.05, [("OME20-A", 410, 500)]),
    ("00378180177", "Synthroid", "Levothyroxine", "50mcg", "Tablet", "ROOM_TEMP",
     0.09, 350, 7.0, 1, 70, 35, 560, 0.07, [("LEV50-A", 360, 350)]),
    # --- below reorder → procurement should fire (low days of stock) ---
    ("00093507456", "Prinivil", "Lisinopril", "10mg", "Tablet", "ROOM_TEMP",
     0.08, 45, 10.0, 1, 90, 45, 600, 0.62, [("LIS10-A", 300, 45)]),
    ("00093310705", "Amoxil", "Amoxicillin", "500mg", "Capsule", "ROOM_TEMP",
     0.14, 90, 14.0, 1, 150, 70, 800, 0.74, [("AMX500-A", 200, 90)]),
    ("00071080840", "Neurontin", "Gabapentin", "300mg", "Capsule", "ROOM_TEMP",
     0.11, 80, 11.0, 1, 110, 55, 700, 0.91, [("GAB300-A", 250, 80)]),
    # --- normal movers ---
    ("00781182901", "Lopressor", "Metoprolol Tartrate", "50mg", "Tablet", "ROOM_TEMP",
     0.07, 300, 4.0, 3, 60, 30, 480,
     0.18, [("MTP50-OLD", 20, 180), ("MTP50-NEW", 365, 120)]),  # near-expiry lot
    ("00185003101", "Zoloft", "Sertraline", "50mg", "Tablet", "ROOM_TEMP",
     0.11, 200, 3.0, 5, 45, 22, 320, 0.12, [("SER50-A", 330, 200)]),
    ("00054327463", "Deltasone", "Prednisone", "20mg", "Tablet", "ROOM_TEMP",
     0.20, 60, 5.0, 6, 60, 30, 300, 0.42, [("PRD20-A", 150, 60)]),
    ("00056017275", "Coumadin", "Warfarin", "5mg", "Tablet", "ROOM_TEMP",
     0.15, 120, 3.0, 2, 40, 20, 240, 0.15, [("WAR5-A", 280, 120)]),
    # --- slow mover ---
    ("00310013010", "Microzide", "Hydrochlorothiazide", "25mg", "Tablet", "ROOM_TEMP",
     0.04, 150, 0.4, 20, 20, 10, 200, 0.05, [("HCTZ25-A", 300, 150)]),
    # --- dead stock (no movement ~200d, capital tied up) ---
    ("00781100101", "Ceftin", "Cefuroxime", "250mg", "Tablet", "ROOM_TEMP",
     1.95, 60, 0.0, 220, 30, 15, 120,
     0.0, [("CEF250-OLD", 16, 60)]),  # dead AND near-expiry
    ("63653117101", "Plavix", "Clopidogrel", "75mg", "Tablet", "ROOM_TEMP",
     2.20, 90, 0.2, 200, 30, 15, 150, 0.0, [("CLO75-A", 240, 90)]),
    # --- cold-chain (refrigerated) ---
    ("00088502101", "Lantus", "Insulin Glargine", "100u/mL", "Vial", "REFRIGERATED",
     12.40, 40, 2.0, 4, 30, 15, 120, 0.20, [("INS-A", 210, 40)]),
]


async def seed(reset: bool = False) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(DB_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        row = (await db.execute(
            text("SELECT id FROM pharmacies WHERE name ILIKE :n ORDER BY created_at LIMIT 1"),
            {"n": PHARMACY_NAME},
        )).one_or_none()
        if not row:
            print("ERROR: Demo pharmacy not found. Run scripts/onboard_pharmacy.py first.")
            return
        pharmacy_id = str(row[0])
        ndcs = [d[0] for d in DRUGS]
        print(f"Pharmacy: {PHARMACY_NAME} ({pharmacy_id[:8]}…) — seeding {len(DRUGS)} drugs")

        # Idempotent: clear this pharmacy's stock for these NDCs (and optionally
        # the movements ledger) before re-inserting.
        if reset:
            await db.execute(text("DELETE FROM inventory_movements WHERE pharmacy_id = :p"),
                             {"p": pharmacy_id})
        await db.execute(text(
            "DELETE FROM inventory_lots WHERE pharmacy_id = :p AND ndc11 = ANY(:n)"),
            {"p": pharmacy_id, "n": ndcs})
        await db.execute(text(
            "DELETE FROM stock_levels WHERE pharmacy_id = :p AND ndc11 = ANY(:n)"),
            {"p": pharmacy_id, "n": ndcs})
        await db.commit()

        fast = slow = dead = below = nearexp = 0
        for (ndc, brand, generic, strength, form, storage, unit_cost, on_hand,
             adq, last_days, reorder_pt, safety, par_max, stockout_p, lots) in DRUGS:

            # drug_products (catalog) — create if missing, capture id
            dp_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO drug_products
                    (id, ndc11, brand_name, generic_name, strength, dosage_form,
                     is_generic, is_otc, is_controlled, storage_condition,
                     awp_unit_price, wac_price, drug_db_metadata,
                     created_at, updated_at, is_deleted)
                VALUES
                    (:id, :ndc, :brand, :generic, :strength, :form,
                     true, false, false, :storage,
                     :awp, :wac, '{}'::jsonb, NOW(), NOW(), false)
                ON CONFLICT (ndc11) DO NOTHING
            """), {"id": dp_id, "ndc": ndc, "brand": brand, "generic": generic,
                   "strength": strength, "form": form, "storage": storage,
                   "awp": round(unit_cost * 1.6, 4), "wac": round(unit_cost * 1.2, 4)})
            # Resolve the real drug_product_id (existing or just-inserted)
            dp_id = str((await db.execute(
                text("SELECT id FROM drug_products WHERE ndc11 = :ndc"), {"ndc": ndc},
            )).scalar_one())

            # stock_levels with precomputed intelligence fields
            reorder_qty = max(0, int(adq * 21 + safety - on_hand))
            await db.execute(text("""
                INSERT INTO stock_levels
                    (id, pharmacy_id, ndc11, drug_product_id,
                     quantity_on_hand, quantity_reserved, quantity_on_order,
                     par_level_min, par_level_max, reorder_point, reorder_quantity,
                     safety_stock, avg_daily_demand, stockout_probability_7d,
                     forecast_updated_at, last_dispensed_at, last_received_at,
                     created_at, updated_at)
                VALUES
                    (:id, :p, :ndc, :dp,
                     :oh, 0, 0,
                     :pmin, :pmax, :rp, :rq,
                     :ss, :adq, :sp,
                     NOW(), NOW() - (:last || ' days')::interval,
                     NOW() - INTERVAL '14 days',
                     NOW(), NOW())
            """), {"id": str(uuid.uuid4()), "p": pharmacy_id, "ndc": ndc, "dp": dp_id,
                   "oh": on_hand, "pmin": safety, "pmax": par_max, "rp": reorder_pt,
                   "rq": reorder_qty, "ss": safety, "adq": adq, "sp": stockout_p,
                   "last": str(last_days)})

            # inventory_lots
            for (lot_no, exp_days, qty) in lots:
                await db.execute(text("""
                    INSERT INTO inventory_lots
                        (id, pharmacy_id, drug_product_id, ndc11,
                         lot_number, expiry_date, quantity_received, quantity_on_hand,
                         quantity_reserved, unit_cost, storage_location, received_at,
                         is_recalled, is_quarantined, created_at, updated_at, is_deleted)
                    VALUES
                        (:id, :p, :dp, :ndc,
                         :lot, CURRENT_DATE + (:exp || ' days')::interval, :qty, :qty,
                         0, :cost, :loc, NOW() - INTERVAL '30 days',
                         false, false, NOW(), NOW(), false)
                """), {"id": str(uuid.uuid4()), "p": pharmacy_id, "dp": dp_id, "ndc": ndc,
                       "lot": lot_no, "exp": str(exp_days), "qty": qty, "cost": unit_cost,
                       "loc": "REFRIGERATOR-1" if storage == "REFRIGERATED" else "SHELF-A"})

            # tally for the summary
            if adq == 0 or last_days >= 180:
                dead += 1
            elif adq >= 6:
                fast += 1
            elif adq < 1:
                slow += 1
            if on_hand < reorder_pt:
                below += 1
            if any(e <= 30 for (_, e, _) in lots):
                nearexp += 1

        await db.commit()
    await engine.dispose()

    print()
    print(f"✓ {len(DRUGS)} drugs seeded with stock_levels + inventory_lots")
    print(f"   fast movers: {fast}   slow: {slow}   dead stock: {dead}")
    print(f"   below reorder (procurement fires): {below}   near-expiry lots: {nearexp}")
    print("   cold-chain: Insulin Glargine (REFRIGERATED)")
    print("→ Inventory-AI panels (turnover/dead-stock/expiry/supply/procurement/stockout) now live.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed PharmPilot inventory snapshot")
    parser.add_argument("--reset", action="store_true", help="Also clear the movements ledger")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))
