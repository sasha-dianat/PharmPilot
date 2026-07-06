---
name: seed-demo
description: Seed or reset a realistic demo patient with a multi-drug basket (Rx rows, standing meds, low eGFR lab) so the queue basket, Medical Intelligence, and reception quote can be demonstrated end-to-end.
---

# Seed Demo Patient

Create (idempotently) the demo patient `dddddddd-0000-4000-8000-000000000001`
in pharmacy `a331c018-5904-4e50-a58d-c6343bcf2e17` on the dev DB
(psql: PGPASSWORD=change_in_production, host 127.0.0.1:5433, db pharmpilot).

Steps:
1. Delete existing rows for that patient id from lab_results, medications,
   prescriptions, interaction_reports, then patients (FK order).
2. Insert patient (gender is varchar(1) → 'M'; ndc column is varchar(11)).
3. Insert 4–5 prescriptions in status pending_verification with distinct
   rx_numbers (RX-DEMO-6xx) using drugs that exist in drug_catalog for good
   quotes: Omeprazole 20mg, Atorvastatin 20mg, Metformin 500mg, Amlodipine 5mg,
   Vitamin D3 (supplement lever). Prescriber: any existing prescribers.id.
4. Insert standing medication metformin (status active) and lab eGFR=25 —
   this fires the Contraindicated metformin renal finding and clopidogrel-style
   dispense findings in Medical Intelligence.
5. Verify: SELECT counts; then GET /api/v1/cds/interaction-report/{patient_id}
   (pharmacist login Pharmacist2024!) returns findings with both 'dispense' and
   'profile' sections.

Report the seeded basket and where to see it (Rx queue basket → استعلام قیمت,
middle panel → Medical Intelligence).
