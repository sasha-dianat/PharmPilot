"""
Seed inventory_movements (stock-loss events) so the Shrinkage feed renders live
data: damage write-offs, expiry/recall removals, and downward count corrections.

Idempotent: clears this seed's rows (source marker via reason prefix) first.

Usage:
    DATABASE_URL=... python scripts/seed_movements.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot",
)
PHARMACY_NAME = "PharmPilot Demo%"

# (movement_type, units_lost, days_ago, reason)
TEMPLATES = [
    ("DAMAGE",          6,  3,  "[seed] broken vials during handling"),
    ("DAMAGE",          4,  9,  "[seed] crushed blister pack"),
    ("EXPIRY_REMOVAL",  18, 14, "[seed] expired stock pulled from shelf"),
    ("RECALL_REMOVAL",  40, 21, "[seed] FDA recall lot removal"),
    ("ADJUSTMENT",      3,  6,  "[seed] cycle-count shortage"),
    ("ADJUSTMENT",      5,  17, "[seed] perpetual-inventory discrepancy"),
    ("DAMAGE",          2,  27, "[seed] cold-chain breach discard"),
    ("EXPIRY_REMOVAL",  9,  33, "[seed] short-dated stock removed"),
]


async def seed() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(DB_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        row = (await db.execute(text(
            "SELECT id FROM pharmacies WHERE name ILIKE :n ORDER BY created_at LIMIT 1"),
            {"n": PHARMACY_NAME})).one_or_none()
        if not row:
            print("ERROR: demo pharmacy not found.")
            return
        pharmacy_id = str(row[0])

        lots = (await db.execute(text(
            "SELECT id, ndc11, quantity_on_hand FROM inventory_lots "
            "WHERE pharmacy_id = :p AND quantity_on_hand > 0 ORDER BY created_at LIMIT 30"),
            {"p": pharmacy_id})).all()
        if not lots:
            print("ERROR: no inventory lots. Run scripts/seed_inventory.py first.")
            return

        await db.execute(text(
            "DELETE FROM inventory_movements WHERE pharmacy_id = :p AND reason LIKE '[seed]%'"),
            {"p": pharmacy_id})

        now = datetime.now(timezone.utc)
        n = 0
        for i, (mtype, units, days_ago, reason) in enumerate(TEMPLATES):
            lot_id, ndc, qoh = lots[i % len(lots)]
            before = float(qoh)
            delta = -float(min(units, before))  # never remove more than on hand
            await db.execute(text("""
                INSERT INTO inventory_movements
                    (id, pharmacy_id, ndc11, inventory_lot_id, movement_type, reason,
                     quantity_before, quantity_after, quantity_delta,
                     created_at, updated_at, is_deleted)
                VALUES
                    (:id, :p, :ndc, :lot, :mtype, :reason,
                     :before, :after, :delta,
                     :created, :created, false)
            """), {"id": str(uuid.uuid4()), "p": pharmacy_id, "ndc": str(ndc),
                   "lot": str(lot_id), "mtype": mtype, "reason": reason,
                   "before": before, "after": before + delta, "delta": delta,
                   "created": now - timedelta(days=days_ago)})
            n += 1
        await db.commit()
    await engine.dispose()

    lost = sum(min(u, 9999) for _, u, _, _ in TEMPLATES)
    print(f"✓ {n} shrinkage movements seeded (~{lost} units across damage/expiry/recall/adjustment)")
    print("→ Inventory Intelligence · Shrinkage feed now live.")


if __name__ == "__main__":
    asyncio.run(seed())
