"""
Seed financial activity so the Financial Operations dashboard renders live data:
  - ~45 prescription_fills + approved claim_transactions across the last 28 days
    (powers /analytics/financial/summary: revenue, PBM paid, copays, margin)
  - DIR fee adjustments posted this period (clawbacks/bonuses)
  - cancellation rx_state_events with canonical reasons
    (powers /analytics/cancellations)

Self-contained: reads the demo pharmacy's existing prescriptions + insurance and
generates fills/claims against them. Idempotent — synthetic fills use
fill_number >= 50 so they are isolated from the real demo fills (fill_number 1);
re-running clears this pharmacy's claims/DIR + synthetic fills/cancellations.

Usage:
    DATABASE_URL=... python scripts/seed_financials.py
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

N_CLAIMS = 45
WINDOW_DAYS = 28
CANCELL_REASONS = [
    "customer_declined", "prescriber_cancelled", "duplicate",
    "insurance_issue", "expired", "other",
]


def _hash() -> str:
    return uuid.uuid4().hex + uuid.uuid4().hex  # 64-char tamper-evidence stub


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

        staff_row = (await db.execute(
            text("SELECT id FROM staff WHERE pharmacy_id=:p ORDER BY role LIMIT 1"),
            {"p": pharmacy_id},
        )).one_or_none()
        pharmacist_id = str(staff_row[0]) if staff_row else str(uuid.uuid4())

        rxs = (await db.execute(text("""
            SELECT id, ndc, quantity_prescribed, days_supply, patient_id
            FROM prescriptions WHERE pharmacy_id = :p
        """), {"p": pharmacy_id})).all()
        if not rxs:
            print("ERROR: No prescriptions found. Run scripts/seed_demo_data.py first.")
            return

        ins = (await db.execute(text("""
            SELECT pi.patient_id, pi.id, pi.member_id, pi.bin_number, pi.pcn, pi.group_number
            FROM patient_insurances pi
            JOIN patients pt ON pt.id = pi.patient_id
            WHERE pt.pharmacy_id = :p
        """), {"p": pharmacy_id})).all()
        ins_by_patient = {str(r[0]): r for r in ins}

        rx_ids = [str(r[0]) for r in rxs]
        # ── idempotent cleanup (FK-safe order) ────────────────────────────
        await db.execute(text("DELETE FROM dir_fee_adjustments WHERE pharmacy_id = :p"),
                         {"p": pharmacy_id})
        await db.execute(text("DELETE FROM claim_transactions WHERE pharmacy_id = :p"),
                         {"p": pharmacy_id})
        await db.execute(text(
            "DELETE FROM prescription_fills WHERE fill_number >= 50 "
            "AND prescription_id = ANY(:rx)"), {"rx": rx_ids})
        await db.execute(text(
            "DELETE FROM rx_state_events WHERE to_status='cancelled' "
            "AND triggered_by_type='system' AND prescription_id = ANY(:rx)"), {"rx": rx_ids})
        await db.commit()

        gross = 0.0
        claim_ids: list[str] = []
        for i in range(N_CLAIMS):
            rid, ndc, qty, days, pat = rxs[i % len(rxs)]
            rid, ndc, pat = str(rid), str(ndc), str(pat)
            qty = float(qty or 30)
            days = int(days or 30)
            day_off = i % WINDOW_DAYS

            ins_row = ins_by_patient.get(pat)
            member = ins_row[2] if ins_row else "000000000"
            bin_no = ins_row[3] if ins_row else "610494"
            pcn = (ins_row[4] if ins_row else None) or "SALAMAT"
            grp = (ins_row[5] if ins_row else None) or "GRP001"
            pat_ins_id = str(ins_row[1]) if ins_row else None

            # synthetic fill (fill_number >= 50 = financial-seed marker)
            fill_id = str(uuid.uuid4())
            await db.execute(text("""
                INSERT INTO prescription_fills
                    (id, prescription_id, fill_number, ndc_dispensed,
                     quantity_dispensed, days_supply, fill_date,
                     dispensing_pharmacist_id, verifying_pharmacist_id,
                     created_at, updated_at)
                VALUES
                    (:id, :rx, :fn, :ndc, :qty, :days,
                     CURRENT_DATE - (:off || ' days')::interval,
                     :ph, :ph,
                     NOW() - (:off || ' days')::interval, NOW())
            """), {"id": fill_id, "rx": rid, "fn": 50 + i, "ndc": ndc,
                   "qty": qty, "days": days, "off": str(day_off), "ph": pharmacist_id})

            # realistic claim mix: ~60% generic, ~25% brand, ~15% specialty
            tier = i % 20
            if tier < 12:        # generic
                base = 22.0 + (i % 6) * 14.0          # $22–$92
            elif tier < 17:      # brand
                base = 240.0 + (i % 5) * 90.0         # $240–$600
            else:                # specialty / biologic
                base = 1400.0 + (i % 4) * 600.0       # $1400–$3200
            ic_sub = round(base + qty * 0.25, 2)
            ic_paid = round(ic_sub * 0.93, 2)
            disp_fee = 1.75
            copay = [5.0, 10.0, 20.0, 35.0][i % 4]
            total_paid = round(max(0.0, ic_paid + disp_fee - copay), 2)
            gross += total_paid + copay

            cid = str(uuid.uuid4())
            claim_ids.append(cid)
            await db.execute(text("""
                INSERT INTO claim_transactions
                    (id, fill_id, pharmacy_id, patient_insurance_id,
                     sequence_number, transaction_type,
                     bin_number, pcn, group_number, member_id, person_code,
                     ndc, quantity, days_supply, daw_code, date_of_service,
                     ingredient_cost_submitted, dispensing_fee_submitted,
                     status, response_status,
                     ingredient_cost_paid, dispensing_fee_paid,
                     total_amount_paid, patient_pay_amount,
                     submitted_at, responded_at, response_time_ms,
                     created_at, updated_at, is_deleted)
                VALUES
                    (:id, :fill, :p, :pi,
                     1, 'B1',
                     :bin, :pcn, :grp, :mem, '01',
                     :ndc, :qty, :days, '0',
                     CURRENT_DATE - (:off || ' days')::interval,
                     :ic_sub, :disp,
                     'approved', 'A',
                     :ic_paid, :disp,
                     :total, :copay,
                     NOW() - (:off || ' days')::interval,
                     NOW() - (:off || ' days')::interval, 420,
                     NOW(), NOW(), false)
            """), {"id": cid, "fill": fill_id, "p": pharmacy_id, "pi": pat_ins_id,
                   "bin": bin_no, "pcn": pcn, "grp": grp, "mem": member,
                   "ndc": ndc, "qty": qty, "days": days, "off": str(day_off),
                   "ic_sub": ic_sub, "disp": disp_fee, "ic_paid": ic_paid,
                   "total": total_paid, "copay": copay})

        # ── DIR fee adjustments (clawbacks + one bonus) ───────────────────
        # DIR exposure ~3-5% of reimbursement — scaled to the claim volume above
        dir_rows = [
            ("performance_based", -840.00, 5, "Iran Health Insurance", "Star-rating performance fee"),
            ("network_fee", -310.00, 11, "Iran Health Insurance", "Network participation fee"),
            ("admin_fee", -145.00, 17, "Iran Health Insurance", "Administrative fee"),
            ("performance_based", -520.00, 22, "Iran Health Insurance", "Generic dispensing rate adj."),
            ("quality_bonus", 180.00, 25, "Iran Health Insurance", "Adherence quality bonus"),
        ]
        for (atype, amt, off, payer, reason) in dir_rows:
            await db.execute(text("""
                INSERT INTO dir_fee_adjustments
                    (id, pharmacy_id, claim_transaction_id, adjustment_type,
                     adjustment_amount, payer_name, adjustment_reason, posted_date,
                     created_at, updated_at)
                VALUES
                    (:id, :p, :cid, :atype, :amt, :payer, :reason,
                     CURRENT_DATE - (:off || ' days')::interval, NOW(), NOW())
            """), {"id": str(uuid.uuid4()), "p": pharmacy_id,
                   "cid": claim_ids[off % len(claim_ids)] if claim_ids else None,
                   "atype": atype, "amt": amt, "payer": payer,
                   "reason": reason, "off": str(off)})

        # ── cancellation events (for /analytics/cancellations) ────────────
        cancels = [
            ("customer_declined", 2), ("customer_declined", 9),
            ("insurance_issue", 4), ("prescriber_cancelled", 12),
            ("duplicate", 18), ("expired", 24),
        ]
        for (reason, off) in cancels:
            await db.execute(text("""
                INSERT INTO rx_state_events
                    (id, prescription_id, from_status, to_status,
                     triggered_by_type, reason, event_hash,
                     created_at, updated_at, is_deleted)
                VALUES
                    (:id, :rx, 'intake', 'cancelled',
                     'system', :reason, :hash,
                     NOW() - (:off || ' days')::interval, NOW(), false)
            """), {"id": str(uuid.uuid4()), "rx": rx_ids[off % len(rx_ids)],
                   "reason": reason, "hash": _hash(), "off": str(off)})

        await db.commit()
    await engine.dispose()

    print()
    print(f"✓ {N_CLAIMS} approved claims over {WINDOW_DAYS}d  ·  gross revenue ≈ ${gross:,.2f}")
    print(f"✓ {len(dir_rows)} DIR fee adjustments  ·  {len(cancels)} cancellation events")
    print("→ Financial Operations (summary + cancellations) now live.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed PharmPilot financial data")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))
