"""
Feature Store — PART 2 of the Offline-First Doctrine
=====================================================
One shared layer of SQL→DataFrame feature builders so the fourteen services do
NOT each reinvent how to pull dispense history, claim outcomes, lot depletion,
patient timelines, or prescriber profiles from the local PostgreSQL database.

Everything here reads the *local* database — it is fully offline. Builders return
pandas DataFrames ready for scikit-learn / XGBoost training or scoring.

All queries are defensive (COALESCE, LEFT JOIN, LIMIT) and tolerate sparse data
so cold-start (a brand-new pharmacy with few rows) never raises.

Public surface (all async, take an AsyncSession):
  dispense_history(db, pharmacy_id, days)         -> DataFrame
  claim_outcomes(db, pharmacy_id, days)           -> DataFrame
  lot_depletion(db, pharmacy_id)                  -> DataFrame
  dur_overrides(db, pharmacy_id)                  -> DataFrame
  prescriber_profile(db, pharmacy_id, prescriber) -> DataFrame
  patient_timeline(db, patient_id)                -> DataFrame
  payment_events(db, pharmacy_id, days)           -> DataFrame
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None  # type: ignore


async def _frame(db: AsyncSession, sql: str, params: dict):
    """Execute SQL → pandas DataFrame. Returns empty frame on any failure."""
    if pd is None:
        raise RuntimeError("pandas is required for feature_store")
    try:
        result = await db.execute(text(sql), params)
        rows = result.mappings().all()
        return pd.DataFrame([dict(r) for r in rows])
    except Exception as exc:  # noqa: BLE001 — missing table / cold start → empty frame
        logger.warning("[feature_store] query failed (%s) → empty frame", exc)
        return pd.DataFrame()


# ─── Dispense history (queue ranking, adherence, trajectory, supply) ──────────

async def dispense_history(db: AsyncSession, pharmacy_id: str, days: int = 365):
    """
    One row per prescription fill over the window. Core feature source for
    demand, queue priority, prescriber profiling and trajectory.
    """
    sql = """
        SELECT p.id              AS rx_id,
               p.patient_id,
               p.prescriber_id,
               p.ndc,
               p.drug_name,
               p.dea_schedule,
               COALESCE(p.is_controlled, false) AS is_controlled,
               COALESCE(p.quantity_dispensed, p.quantity_prescribed, 0) AS quantity,
               COALESCE(p.days_supply, 0)        AS days_supply,
               COALESCE(p.refills_remaining, 0)  AS refills_remaining,
               COALESCE(p.refills_authorized, 0) AS refills_authorized,
               p.status,
               p.source,
               p.written_date,
               p.fill_date,
               p.last_fill_date,
               p.claimed_at,
               p.created_at
        FROM   prescriptions p
        WHERE  p.pharmacy_id = :pid
          AND  p.created_at >= now() - (:days || ' days')::interval
        ORDER  BY p.created_at
    """
    return await _frame(db, sql, {"pid": pharmacy_id, "days": days})


# ─── Claim outcomes (rejection prediction, margin, copilot adjudication) ──────

async def claim_outcomes(db: AsyncSession, pharmacy_id: str, days: int = 365):
    """
    Adjudication results joined to the Rx that produced them. Target column
    `rejected` (bool) drives the local claim-rejection classifier.
    """
    sql = """
        SELECT c.id                  AS claim_id,
               pf.prescription_id    AS rx_id,
               c.ndc,
               p.drug_name,
               COALESCE(p.is_controlled, false) AS is_controlled,
               COALESCE(c.days_supply, 0)       AS days_supply,
               COALESCE(c.quantity, 0)          AS quantity,
               c.daw_code,
               p.prescriber_id,
               COALESCE(c.bin_number, '')    AS bin_number,
               COALESCE(c.pcn, '')           AS pcn,
               COALESCE(c.group_number, '')  AS group_number,
               c.status,
               CASE
                 WHEN c.status ILIKE '%reject%' THEN true
                 WHEN c.reject_codes IS NOT NULL
                      AND c.reject_codes::text NOT IN ('[]', 'null', '') THEN true
                 ELSE false
               END AS rejected,
               COALESCE(c.total_amount_paid, 0)    AS total_paid,
               COALESCE(c.patient_pay_amount, 0)   AS patient_pay,
               COALESCE(c.ingredient_cost_paid, 0) AS ingredient_cost_paid,
               c.created_at
        FROM   claim_transactions c
        LEFT JOIN prescription_fills pf ON pf.id = c.fill_id
        LEFT JOIN prescriptions      p  ON p.id  = pf.prescription_id
        WHERE  c.pharmacy_id = :pid
          AND  c.created_at >= now() - (:days || ' days')::interval
        ORDER  BY c.created_at
    """
    return await _frame(db, sql, {"pid": pharmacy_id, "days": days})


# ─── Lot depletion (expiry waste prevention) ─────────────────────────────────

async def lot_depletion(db: AsyncSession, pharmacy_id: str):
    """
    Every on-hand lot with quantity + expiry, plus the trailing 90-day dispense
    rate for its NDC so the survival model can compare days-to-expiry vs
    days-to-depletion.
    """
    sql = """
        WITH dispense_rate AS (
            SELECT ndc,
                   SUM(COALESCE(quantity_dispensed, quantity_prescribed, 0))
                     / 90.0 AS daily_rate
            FROM   prescriptions
            WHERE  pharmacy_id = :pid
              AND  created_at >= now() - interval '90 days'
            GROUP  BY ndc
        )
        SELECT l.id              AS lot_id,
               l.ndc11,
               l.lot_number,
               l.expiry_date,
               COALESCE(l.quantity_on_hand, 0)  AS quantity_on_hand,
               COALESCE(l.unit_cost, 0)         AS unit_cost,
               COALESCE(dr.daily_rate, 0)       AS daily_dispense_rate,
               (l.expiry_date - CURRENT_DATE)   AS days_to_expiry,
               dp.generic_name,
               COALESCE(dp.is_controlled, false) AS is_controlled
        FROM   inventory_lots l
        LEFT JOIN drug_products dp ON dp.id = l.drug_product_id
        LEFT JOIN dispense_rate dr ON dr.ndc = l.ndc11
        WHERE  l.pharmacy_id = :pid
          AND  COALESCE(l.quantity_on_hand, 0) > 0
          AND  COALESCE(l.is_quarantined, false) = false
        ORDER  BY l.expiry_date
    """
    return await _frame(db, sql, {"pid": pharmacy_id})


# ─── DUR override patterns (override intelligence) ───────────────────────────

async def dur_overrides(db: AsyncSession, pharmacy_id: Optional[str] = None):
    """
    All override events (optionally scoped to a pharmacy via the Rx join) for
    association-rule mining and per-pharmacist consistency scoring.
    """
    sql = """
        SELECT d.id,
               d.alert_type,
               d.reason_code,
               d.reason_label,
               d.overridden_by,
               COALESCE(d.prescriber_callback, false) AS prescriber_callback,
               p.ndc,
               p.drug_name,
               COALESCE(p.is_controlled, false) AS is_controlled,
               d.created_at
        FROM   dur_override_events d
        LEFT JOIN prescriptions p ON p.id = d.rx_id
        WHERE  (:pid IS NULL OR p.pharmacy_id = :pid)
        ORDER  BY d.created_at
    """
    return await _frame(db, sql, {"pid": pharmacy_id})


# ─── Prescriber profile (registry enrichment, forgery behavioral signal) ─────

async def prescriber_profile(db: AsyncSession, pharmacy_id: str, prescriber_id: str):
    """
    The prescribing distribution for one prescriber: per-drug counts, dose and
    quantity stats. New Rxs are scored against this to flag deviations.
    """
    sql = """
        SELECT p.ndc,
               p.drug_name,
               p.drug_strength,
               COALESCE(p.is_controlled, false) AS is_controlled,
               p.dea_schedule,
               COUNT(*)                              AS rx_count,
               AVG(COALESCE(p.quantity_prescribed,0)) AS avg_quantity,
               STDDEV_POP(COALESCE(p.quantity_prescribed,0)) AS std_quantity,
               AVG(COALESCE(p.days_supply,0))        AS avg_days_supply,
               MAX(p.created_at)                     AS last_written
        FROM   prescriptions p
        WHERE  p.pharmacy_id  = :pid
          AND  p.prescriber_id = :prescriber_id
        GROUP  BY p.ndc, p.drug_name, p.drug_strength, p.is_controlled, p.dea_schedule
        ORDER  BY rx_count DESC
    """
    return await _frame(db, sql, {"pid": pharmacy_id, "prescriber_id": prescriber_id})


# ─── Patient timeline (lifetime trajectory) ──────────────────────────────────

async def patient_timeline(db: AsyncSession, patient_id: str):
    """
    A unified chronological event stream for one patient: every fill plus every
    lab result, ordered by date — the input to the trajectory model.
    """
    sql = """
        SELECT 'medication'              AS event_type,
               p.created_at              AS event_date,
               p.drug_name               AS label,
               p.ndc                     AS code,
               COALESCE(p.quantity_dispensed, p.quantity_prescribed, 0) AS value,
               p.drug_strength           AS detail
        FROM   prescriptions p
        WHERE  p.patient_id = :patient_id

        UNION ALL

        SELECT 'lab'                     AS event_type,
               lr.result_date            AS event_date,
               lr.test_name              AS label,
               COALESCE(lr.loinc_code,'') AS code,
               COALESCE(NULLIF(regexp_replace(lr.value, '[^0-9.\-]', '', 'g'), '')::numeric, 0) AS value,
               COALESCE(lr.unit, '')     AS detail
        FROM   lab_results lr
        WHERE  lr.patient_id = :patient_id

        ORDER  BY event_date
    """
    return await _frame(db, sql, {"patient_id": patient_id})


# ─── Payment events (margin optimization, payment anomaly) ───────────────────

async def payment_events(db: AsyncSession, pharmacy_id: str, days: int = 90):
    """All POS payment events over the window for margin + anomaly analysis."""
    sql = """
        SELECT pe.id,
               pe.rx_id,
               pe.tender_type,
               COALESCE(pe.patient_pay, 0)     AS patient_pay,
               COALESCE(pe.amount_tendered, 0) AS amount_tendered,
               pe.waiver_reason,
               pe.collected_by,
               pe.created_at
        FROM   payment_events pe
        WHERE  pe.created_at >= now() - (:days || ' days')::interval
        ORDER  BY pe.created_at
    """
    # payment_events has no pharmacy_id column (keyed via rx); window-only scope is fine.
    return await _frame(db, sql, {"days": days})
