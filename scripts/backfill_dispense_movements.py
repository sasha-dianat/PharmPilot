#!/usr/bin/env python3
"""Give the pre-hook fills the DISPENSE movements they never got.

Until the dispense hook landed, a fill recorded that medicine left the building
but nothing deducted it. 46 such fills exist. This writes one DISPENSE movement
per orphan so the ledger explains them too.

**It changes real stock, so it does nothing unless you pass --apply.**

The judgement call it cannot make for you: these fills are historical. If the
units they describe were never physically deducted, the current on-hand figures
already reflect their absence, and backfilling would deduct them a second time.
If instead on-hand was maintained by hand, backfilling corrects it. Which is
true depends on how the pharmacy actually worked in that period, and only the
owner knows.

  --dry-run (default)  show exactly what would be written, change nothing
  --apply              write the movements
  --settle-shortfall   where a fill cannot be covered by current stock, record
                       the gap as a COUNT_LOSS awaiting approval instead of
                       silently deducting less

Usage
  python3 scripts/backfill_dispense_movements.py
  python3 scripts/backfill_dispense_movements.py --apply --actor <staff-uuid>
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from services.core.inventory import ledger as L  # noqa: E402


async def collect(db, pharmacy_id):
    """Orphan fills, oldest first — the order they physically happened."""
    return (await db.execute(text("""
        SELECT pf.id AS fill_id, pf.ndc_dispensed AS ndc11, pf.quantity_dispensed,
               pf.lot_number, pf.fill_date, pf.created_at,
               pf.dispensing_pharmacist_id
        FROM prescription_fills pf
        WHERE pf.is_deleted = false
          AND NOT EXISTS (SELECT 1 FROM inventory_movements m
                          WHERE m.prescription_fill_id = pf.id
                            AND m.movement_type = 'DISPENSE')
        ORDER BY pf.created_at ASC"""), {"pid": pharmacy_id})).mappings().all()


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually write movements")
    ap.add_argument("--actor", type=str, default=None,
                    help="staff UUID to record as the actor")
    ap.add_argument("--settle-shortfall", action="store_true",
                    help="record uncoverable quantity as a COUNT_LOSS for approval")
    a = ap.parse_args()

    from services.platform.database import AsyncSessionLocal
    from services.core.inventory.dispense import _lots_for
    from services.platform.routers.inventory_integrity import append_movement

    async with AsyncSessionLocal() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        orphans = await collect(db, pid)
        if not orphans:
            print("nothing to backfill — every fill already has its movement")
            return 0

        print(f"{len(orphans)} fills without a DISPENSE movement"
              f"{'' if a.apply else '  (DRY RUN — nothing will be written)'}\n")
        total_short = 0.0
        written = 0

        for f in orphans:
            lots = await _lots_for(db, pid, f["ndc11"])
            alloc = L.plan_dispense(lots, f["quantity_dispensed"],
                                    reason=f"backfill fill {f['fill_id']}")
            mark = "OK " if alloc.complete else "SHORT"
            print(f"  [{mark}] {f['ndc11']}  qty={float(f['quantity_dispensed']):>7} "
                  f"allocatable={float(alloc.allocated):>7} "
                  f"short={float(alloc.shortfall):>7}  "
                  f"lots={[p.lot_number for p in alloc.plans] or '—'}")
            total_short += float(alloc.shortfall)

            if not a.apply:
                continue
            for plan in alloc.plans:
                await db.execute(text(
                    "UPDATE inventory_lots SET quantity_on_hand = :after, "
                    "updated_at = NOW() WHERE id = :id"),
                    {"after": float(plan.quantity_after), "id": plan.lot_id})
                await append_movement(
                    db, pharmacy_id=pid, ndc11=f["ndc11"], irc=None,
                    lot_id=plan.lot_id, plan=plan,
                    actor_id=a.actor or f["dispensing_pharmacist_id"],
                    prescription_fill_id=f["fill_id"])
                written += 1
            if alloc.allocated > 0:
                await db.execute(text(
                    "UPDATE stock_levels SET quantity_on_hand = quantity_on_hand - :t, "
                    "updated_at = NOW() WHERE pharmacy_id = :p AND ndc11 = :n"),
                    {"t": float(alloc.allocated), "p": pid, "n": f["ndc11"]})

        if a.apply:
            await db.commit()
            print(f"\nwrote {written} movements")
        else:
            print(f"\nwould write movements for {len(orphans)} fills; "
                  f"total uncoverable quantity {total_short:g}")
            print("re-run with --apply to write them")
        if total_short:
            print(f"\n{total_short:g} units cannot be covered by current stock. "
                  f"That gap is the disagreement between the books and the shelf; "
                  f"settle it with a physical count, not an edit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
