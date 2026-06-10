"""
Seed demo patients, prescribers, and prescriptions for PharmPilot workstation testing.

Creates:
  - 3 patients  (Iranian identity — کد ملی, Jalali DOB)
  - 2 prescribers
  - 2 insurance records
  - 8 prescriptions spanning every workflow state
  - DUR alerts on high-risk prescriptions
  - Prescription fills for dispensed-state Rxs

Usage:
    python scripts/seed_demo_data.py
    python scripts/seed_demo_data.py --reset   # drop existing demo data first
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot",
)
PHARMACY_NAME = "PharmPilot Demo Pharmacy"


async def seed(reset: bool = False) -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    engine = create_async_engine(DB_URL, echo=False)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        # ── Pharmacy ──────────────────────────────────────────────────────
        row = (await db.execute(
            text("SELECT id FROM pharmacies WHERE name = :n LIMIT 1"),
            {"n": PHARMACY_NAME},
        )).one_or_none()
        if not row:
            print("ERROR: Demo pharmacy not found. Run scripts/onboard_pharmacy.py first.")
            return
        pharmacy_id = str(row[0])
        print(f"Pharmacy: {PHARMACY_NAME} ({pharmacy_id[:8]}…)")

        # ── Staff IDs (for fill records) ──────────────────────────────────
        staff_row = (await db.execute(
            text("SELECT id FROM staff WHERE role='pharmacist' AND pharmacy_id=:pharm LIMIT 1"),
            {"pharm": pharmacy_id},
        )).one_or_none()
        pharmacist_id = str(staff_row[0]) if staff_row else None
        if not pharmacist_id:
            # Fallback: any staff
            staff_row = (await db.execute(
                text("SELECT id FROM staff WHERE pharmacy_id=:pharm LIMIT 1"),
                {"pharm": pharmacy_id},
            )).one_or_none()
            pharmacist_id = str(staff_row[0]) if staff_row else str(uuid.uuid4())

        if reset:
            print("Resetting existing demo data…")
            # FK-safe order: each table is deleted before every table it
            # references (claims chain → fills → prescriptions → patients).
            for tbl in [
                "dir_fee_adjustments", "claim_transactions",
                "label_events", "dur_alerts", "rx_state_events",
                "prescription_fills", "prescriptions",
                "patient_insurances", "patient_allergies",
                "lab_results", "clinical_notes", "biometric_identities",
                "pharmacy_visits", "audio_transcripts",
                "profile_enrichment_actions",
                "patients", "prescribers",
            ]:
                await db.execute(text(f"DELETE FROM {tbl}"))
            await db.commit()

        # ── Insurance plan ────────────────────────────────────────────────
        ins_plan_id = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO insurance_plans
                (id, plan_name, payer_name, bin_number, pcn, plan_type, is_active,
                 created_at, updated_at)
            VALUES
                (:id, 'سازمان بیمه سلامت ایران', 'Iran Health Insurance',
                 '610494', 'SALAMAT', 'government', true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {"id": ins_plan_id})

        # ── Prescribers ───────────────────────────────────────────────────
        presc1 = str(uuid.uuid4())
        presc2 = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO prescribers
                (id, first_name, last_name, npi, specialty, phone,
                 is_active, created_at, updated_at)
            VALUES
                (:id1, 'علی', 'حسینی', '1234567890', 'Internal Medicine',
                 '021-88001234', true, NOW(), NOW()),
                (:id2, 'سارا', 'کریمی', '0987654321', 'Cardiology',
                 '021-88009876', true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {"id1": presc1, "id2": presc2})

        # ── Patients ──────────────────────────────────────────────────────
        p1 = str(uuid.uuid4())
        p2 = str(uuid.uuid4())
        p3 = str(uuid.uuid4())
        await db.execute(text("""
            INSERT INTO patients
                (id, pharmacy_id, first_name, last_name, date_of_birth,
                 date_of_birth_jalali, national_id, identity_system,
                 gender, phone_primary, phone_secondary,
                 address_line1, city, state, zip_code, preferred_language,
                 status, created_at, updated_at)
            VALUES
                (:p1, :pharm, 'محمد', 'رضایی', '1955-03-14',
                 '1333/12/23', '0012345678', 'iranian',
                 'M', '09121234567', NULL,
                 'خیابان ولیعصر، پلاک ۱۲', 'تهران', 'TX', '10001', 'fa',
                 'active', NOW(), NOW()),
                (:p2, :pharm, 'فاطمه', 'احمدی', '1978-09-22',
                 '1357/06/31', '0087654321', 'iranian',
                 'F', '09371234567', '09121112222',
                 'بلوار کشاورز، پلاک ۴۵', 'تهران', 'TX', '10002', 'fa',
                 'active', NOW(), NOW()),
                (:p3, :pharm, 'علی', 'کریمی', '1998-04-12',
                 '1377/01/23', '0076543210', 'iranian',
                 'M', '09301234567', NULL,
                 'خیابان آزادی، پلاک ۷', 'تهران', 'TX', '10003', 'fa',
                 'active', NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {"p1": p1, "p2": p2, "p3": p3, "pharm": pharmacy_id})

        # ── Patient Allergies ─────────────────────────────────────────────
        await db.execute(text("""
            INSERT INTO patient_allergies
                (id, patient_id, allergen_type, allergen_name,
                 reaction, severity, created_at, updated_at)
            VALUES
                (:a1, :p1, 'drug', 'پنی‌سیلین', 'کهیر', 'moderate', NOW(), NOW()),
                (:a2, :p2, 'drug', 'سولفا', 'آنافیلاکسی', 'severe', NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {
            "a1": str(uuid.uuid4()), "p1": p1,
            "a2": str(uuid.uuid4()), "p2": p2,
        })

        # ── Patient Insurance ─────────────────────────────────────────────
        await db.execute(text("""
            INSERT INTO patient_insurances
                (id, patient_id, insurance_plan_id, bin_number, pcn, group_number,
                 member_id, priority, is_active, created_at, updated_at)
            VALUES
                (:i1, :p1, :plan, '610494', 'SALAMAT', 'GRP001',
                 '123456789', 1, true, NOW(), NOW()),
                (:i2, :p2, :plan, '610494', 'SALAMAT', 'GRP001',
                 '987654321', 1, true, NOW(), NOW())
            ON CONFLICT DO NOTHING
        """), {
            "i1": str(uuid.uuid4()), "p1": p1,
            "i2": str(uuid.uuid4()), "p2": p2,
            "plan": ins_plan_id,
        })

        # ── Prescriptions ─────────────────────────────────────────────────
        # (rx_num, patient, prescriber, drug, ndc, status, sig, qty, days, refills, controlled)
        rxs = [
            ("RX000001", p1, presc1,
             "Metformin HCl 500mg Tablet",     "00093721256",
             "intake",                          # newly entered
             "یک قرص دو بار در روز همراه غذا", 60, 30, 5, False),
            ("RX000002", p1, presc1,
             "Lisinopril 10mg Tablet",          "00093507456",
             "pending_dur",                     # awaiting DUR check
             "یک قرص روزانه",                  30, 30, 11, False),
            ("RX000003", p2, presc2,
             "Atorvastatin 20mg Tablet",        "00071015423",
             "pending_verification",
             "یک قرص شب هنگام خواب",           30, 30, 5,  False),
            ("RX000004", p2, presc2,
             "Amlodipine 5mg Tablet",           "00069152430",
             "verification_in_progress",
             "یک قرص روزانه",                  30, 30, 5,  False),
            ("RX000005", p1, presc1,
             "Omeprazole 20mg Capsule",         "00093014956",
             "pending_adjudication",
             "یک کپسول نیم‌ساعت قبل از صبحانه", 30, 30, 5, False),
            ("RX000006", p3, presc1,
             "Warfarin 5mg Tablet",             "00056017275",
             "ready_to_fill",                   # PBM approved — fill queue
             "طبق INR بیمار تنظیم شود",        30, 30, 2,  False),
            ("RX000007", p2, presc2,
             "Alprazolam 0.25mg Tablet",        "00009001903",
             "filling",
             "نیم قرص دو بار در روز",           60, 30, 0,  True),
            ("RX000008", p1, presc1,
             "Metoprolol Tartrate 50mg Tablet", "00781182901",
             "dispensed",
             "یک قرص دو بار در روز",            60, 30, 5,  False),
        ]

        rx_ids: dict[str, str] = {}
        for (rx_num, pat_id, pr_id, drug, ndc, status,
             sig, qty, days, refills, controlled) in rxs:
            rx_id = str(uuid.uuid4())
            rx_ids[rx_num] = rx_id
            await db.execute(text("""
                INSERT INTO prescriptions
                    (id, pharmacy_id, patient_id, prescriber_id,
                     rx_number, drug_name, ndc, sig_text,
                     quantity_prescribed, days_supply,
                     refills_authorized, refills_remaining,
                     is_controlled, dea_schedule, status, source,
                     written_date, expiry_date, created_at, updated_at)
                VALUES
                    (:id, :pharm, :pat, :presc,
                     :rxn, :drug, :ndc, :sig,
                     :qty, :days, :refills, :refills,
                     :ctrl, :dea, :status, 'paper',
                     CURRENT_DATE, CURRENT_DATE + INTERVAL '1 year',
                     NOW(), NOW())
                ON CONFLICT (rx_number) DO NOTHING
            """), {
                "id": rx_id, "pharm": pharmacy_id,
                "pat": pat_id, "presc": pr_id,
                "rxn": rx_num, "drug": drug, "ndc": ndc,
                "sig": sig, "qty": float(qty), "days": days,
                "refills": refills, "ctrl": controlled, "status": status,
                # Alprazolam is the only controlled drug in the demo set (C-IV)
                "dea": "C-IV" if controlled else None,
            })

            # Create fill for dispensed prescriptions
            if status == "dispensed":
                await db.execute(text("""
                    INSERT INTO prescription_fills
                        (id, prescription_id, fill_number,
                         ndc_dispensed, quantity_dispensed, days_supply,
                         fill_date, dispensing_pharmacist_id, verifying_pharmacist_id,
                         created_at, updated_at)
                    VALUES
                        (:id, :rx, 1,
                         :ndc, :qty, :days,
                         CURRENT_DATE, :pharm_id, :pharm_id,
                         NOW(), NOW())
                    ON CONFLICT DO NOTHING
                """), {
                    "id": str(uuid.uuid4()), "rx": rx_id,
                    "ndc": ndc, "qty": float(qty), "days": days,
                    "pharm_id": pharmacist_id,
                })

            # DUR alert: Warfarin
            if drug.startswith("Warfarin"):
                await db.execute(text("""
                    INSERT INTO dur_alerts
                        (id, prescription_id, alert_type, severity, source,
                         description, interacting_drug_name,
                         is_hard_stop, created_at, updated_at)
                    VALUES
                        (:id, :rx, 'drug_interaction', 'major', 'clinical_brain',
                         'Warfarin × Aspirin — خطر خونریزی افزایش می‌یابد.',
                         'Aspirin', false, NOW(), NOW())
                """), {"id": str(uuid.uuid4()), "rx": rx_id})

            # DUR alert: Alprazolam
            if drug.startswith("Alprazolam"):
                await db.execute(text("""
                    INSERT INTO dur_alerts
                        (id, prescription_id, alert_type, severity, source,
                         description, interacting_drug_name,
                         is_hard_stop, created_at, updated_at)
                    VALUES
                        (:id, :rx, 'high_risk_medication', 'moderate', 'clinical_brain',
                         'بنزودیازپین — خطر وابستگی. ارزیابی مجدد پس از ۲ هفته.',
                         NULL, false, NOW(), NOW())
                """), {"id": str(uuid.uuid4()), "rx": rx_id})

        await db.commit()

    await engine.dispose()

    print()
    print("✓ Insurance:    سازمان بیمه سلامت ایران (BIN 610494)")
    print("✓ Prescribers:  Dr. علی حسینی (Internal Medicine)  ·  Dr. سارا کریمی (Cardiology)")
    print("✓ Patients:     محمد رضایی  ·  فاطمه احمدی  ·  علی کریمی  (Iranian identity)")
    print("✓ Prescriptions:")
    print("   RX000001  Metformin 500mg      → intake")
    print("   RX000002  Lisinopril 10mg      → pending_dur")
    print("   RX000003  Atorvastatin 20mg    → pending_verification")
    print("   RX000004  Amlodipine 5mg       → verification_in_progress  ← voice notes visible here")
    print("   RX000005  Omeprazole 20mg      → pending_adjudication")
    print("   RX000006  Warfarin 5mg (+DUR)  → ready_to_fill")
    print("   RX000007  Alprazolam 0.25mg    → filling  (C-IV, +DUR)")
    print("   RX000008  Metoprolol 50mg      → dispensed")
    print()
    print("✓ DUR alerts on RX000006 (Warfarin×Aspirin) and RX000007 (benzodiazepine)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed PharmPilot demo data")
    parser.add_argument("--reset", action="store_true", help="Drop existing demo data first")
    args = parser.parse_args()
    asyncio.run(seed(reset=args.reset))
