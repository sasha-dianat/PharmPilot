"""
Seed security/surveillance events so the Security & Surveillance dashboard
(/security/events + /security/summary) renders live data.

Idempotent: clears this seed's rows (description prefix marker) first.

Usage:
    DATABASE_URL=... python scripts/seed_security_events.py
"""
from __future__ import annotations

import asyncio
import json
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

# (event_type, severity, resolved, hours_ago, camera_zone, confidence, description)
EVENTS = [
    ("vault_zone_intrusion", "critical", False, 1,   "vault_room",    0.94, "[seed] Unauthorized entry detected in controlled-substance vault"),
    ("after_hours_access",   "critical", False, 3,   "back_entrance", 0.88, "[seed] Badge access outside operating hours"),
    ("loitering",            "warning",  False, 2,   "waiting_area",  0.78, "[seed] Person stationary 9 minutes near counter"),
    ("tailgating",           "warning",  False, 5,   "staff_door",    0.81, "[seed] Two people entered on a single badge scan"),
    ("dispensing_anomaly",   "warning",  False, 7,   "dispensing_1",  0.72, "[seed] Controlled-substance count variance flagged"),
    ("unrecognized_face",    "info",     False, 9,   "front_entrance",0.65, "[seed] Unrecognized individual at entrance"),
    ("camera_offline",       "info",     True,  20,  "stockroom",     1.00, "[seed] Camera feed dropped and auto-recovered"),
    ("forced_entry_attempt", "critical", True,  30,  "rear_door",     0.91, "[seed] Door-forcing attempt detected overnight"),
    ("loitering",            "warning",  True,  26,  "parking_lot",   0.70, "[seed] Extended loitering in parking area"),
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

        await db.execute(text(
            "DELETE FROM security_events WHERE pharmacy_id = :p AND description LIKE '[seed]%'"),
            {"p": pharmacy_id})

        now = datetime.now(timezone.utc)
        n = 0
        for (etype, sev, resolved, hours, zone, conf, desc) in EVENTS:
            detected = now - timedelta(hours=hours)
            await db.execute(text("""
                INSERT INTO security_events
                    (id, pharmacy_id, event_type, severity, description, detected_at,
                     resolved, resolved_at, camera_footage_retained, footage_retention_until,
                     event_metadata, created_at, updated_at)
                VALUES
                    (:id, :p, :etype, :sev, :desc, :detected,
                     :resolved, :resolved_at, true, :retain,
                     CAST(:meta AS jsonb), :detected, :detected)
            """), {"id": str(uuid.uuid4()), "p": pharmacy_id, "etype": etype, "sev": sev,
                   "desc": desc, "detected": detected, "resolved": resolved,
                   "resolved_at": (detected + timedelta(minutes=20)) if resolved else None,
                   "retain": now + timedelta(days=90),
                   "meta": json.dumps({"camera_zone": zone, "confidence": conf})})
            n += 1
        await db.commit()
    await engine.dispose()

    unresolved = sum(1 for e in EVENTS if not e[2])
    crit = sum(1 for e in EVENTS if e[1] == "critical" and not e[2])
    print(f"✓ {n} security events seeded ({unresolved} unresolved, {crit} critical-active)")
    print("→ Security & Surveillance dashboard now live.")


if __name__ == "__main__":
    asyncio.run(seed())
