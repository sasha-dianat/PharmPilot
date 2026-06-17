"""
Seed a realistic spread of DUR alerts across existing prescriptions so the
Clinical Intelligence dashboard (DUR Alert Types) renders live data.

Idempotent: re-running clears this seed's alerts (source='phase_d_demo') first.

Usage:
    DATABASE_URL=... python scripts/seed_clinical_alerts.py
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

# (alert_type, severity, is_hard_stop, overridden, interacting_drug, description)
TEMPLATES = [
    ("drug_interaction",     "major",    False, False, "Aspirin",      "Warfarin × Aspirin — additive bleeding risk; monitor INR closely."),
    ("drug_interaction",     "major",    False, True,  "Amiodarone",   "QTc prolongation: macrolide + amiodarone; baseline ECG advised."),
    ("drug_interaction",     "moderate", False, False, "Fluconazole",  "CYP3A4 inhibition may raise substrate levels."),
    ("renal_dosing",         "major",    True,  False, None,           "Metformin contraindicated at eGFR <30; current eGFR 28."),
    ("renal_dosing",         "moderate", False, True,  None,           "Dose reduction recommended for CrCl 30–50 mL/min."),
    ("high_risk_medication", "moderate", False, False, None,           "Opioid — estimated MME exceeds CDC 90 mg/day threshold; offer naloxone."),
    ("high_risk_medication", "moderate", False, True,  None,           "Benzodiazepine — dependence risk; reassess at 2 weeks."),
    ("allergy",              "critical", True,  False, None,           "Documented penicillin allergy; cross-reactivity risk."),
    ("allergy",              "major",    True,  False, None,           "Sulfa allergy on file; verify before dispensing."),
    ("duplicate_therapy",    "moderate", False, False, None,           "Therapeutic duplication: two agents in the same class."),
    ("duplicate_therapy",    "minor",    False, True,  None,           "Possible duplicate — confirm intentional combination."),
    ("beers_criteria",       "moderate", False, False, None,           "Beers 2023: potentially inappropriate in age ≥65 (fall risk)."),
    ("beers_criteria",       "moderate", False, True,  None,           "Beers 2023: anticholinergic burden — consider alternative."),
    ("drug_interaction",     "minor",    False, False, "Antacid",      "Absorption reduced by polyvalent cations; separate dosing."),
    ("renal_dosing",         "moderate", False, False, None,           "Monitor renal function; adjust per eGFR trend."),
    ("high_risk_medication", "major",    True,  False, None,           "Anticoagulant — verify indication and bleeding precautions."),
    ("drug_interaction",     "moderate", False, False, "Simvastatin",  "Statin + CYP3A4 inhibitor — myopathy risk."),
    ("duplicate_therapy",    "moderate", False, True,  None,           "Two PPIs detected on active profile."),
]


async def seed() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(DB_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        rx_ids = [str(r[0]) for r in (await db.execute(
            text("SELECT id FROM prescriptions ORDER BY created_at LIMIT 30"))).all()]
        if not rx_ids:
            print("ERROR: no prescriptions found. Run scripts/seed_demo_data.py first.")
            return

        await db.execute(text("DELETE FROM dur_alerts WHERE source = 'phase_d_demo'"))

        now = datetime.now(timezone.utc)
        n = 0
        for i, (atype, sev, hard, overr, interacting, desc) in enumerate(TEMPLATES):
            rx = rx_ids[i % len(rx_ids)]
            created = now - timedelta(days=i % 28)
            await db.execute(text("""
                INSERT INTO dur_alerts
                    (id, prescription_id, alert_type, severity, source,
                     description, interacting_drug_name, is_hard_stop, was_overridden,
                     created_at, updated_at)
                VALUES
                    (:id, :rx, :atype, :sev, 'phase_d_demo',
                     :desc, :inter, :hard, :overr,
                     :created, :created)
            """), {"id": str(uuid.uuid4()), "rx": rx, "atype": atype, "sev": sev,
                   "desc": desc, "inter": interacting, "hard": hard, "overr": overr,
                   "created": created})
            n += 1
        await db.commit()
    await engine.dispose()

    types = sorted({t[0] for t in TEMPLATES})
    print(f"✓ {n} DUR alerts seeded across {len(types)} types: {', '.join(types)}")
    print("→ Clinical Intelligence · DUR Alert Types now live.")


if __name__ == "__main__":
    asyncio.run(seed())
