"""
Analytics & Reporting router.
Financial reporting, CMS Star Ratings, operational dashboards, DIR fee tracking.
Reads from PostgreSQL for real-time; ClickHouse for historical analytics.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.claims import ClaimTransaction, DIRFeeAdjustment
from shared.models.prescription import Prescription, PrescriptionFill, RxStatus
from shared.models.inventory import StockLevel, InventoryLot

router = APIRouter()


# ── Operational Dashboard ──────────────────────────────────────────────────

@router.get("/dashboard/operational")
async def operational_dashboard(
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Real-time operational metrics for the pharmacy manager dashboard.
    Refreshes every 30 seconds via frontend polling.
    """
    pharmacy_id = staff.pharmacy_id
    today = date.today()
    now = datetime.now(timezone.utc)

    # Today's fills
    fills_today_result = await db.execute(
        select(func.count(PrescriptionFill.id)).where(
            PrescriptionFill.fill_date == today,
        )
    )
    fills_today = fills_today_result.scalar() or 0

    # Queue counts by status
    queue_result = await db.execute(
        select(Prescription.status, func.count(Prescription.id))
        .where(
            Prescription.pharmacy_id == pharmacy_id,
            Prescription.status.notin_([
                RxStatus.DISPENSED.value,
                RxStatus.CANCELLED.value,
                RxStatus.RETURNED_TO_STOCK.value,
            ]),
        )
        .group_by(Prescription.status)
    )
    queue_by_status = {row[0]: row[1] for row in queue_result.all()}

    # Total queue depth
    total_queue = sum(queue_by_status.values())

    # Rejected claims today
    rejected_today_result = await db.execute(
        select(func.count(ClaimTransaction.id)).where(
            ClaimTransaction.pharmacy_id == pharmacy_id,
            ClaimTransaction.status == "rejected",
            func.date(ClaimTransaction.created_at) == today,
        )
    )
    rejected_claims = rejected_today_result.scalar() or 0

    # Avg adjudication response time today
    avg_response_result = await db.execute(
        select(func.avg(ClaimTransaction.response_time_ms)).where(
            ClaimTransaction.pharmacy_id == pharmacy_id,
            ClaimTransaction.response_time_ms.isnot(None),
            func.date(ClaimTransaction.created_at) == today,
        )
    )
    avg_response_ms = avg_response_result.scalar()

    # Expiring soon (30 days)
    expiring_result = await db.execute(
        select(func.count(InventoryLot.id)).where(
            InventoryLot.pharmacy_id == pharmacy_id,
            InventoryLot.expiry_date <= date.today() + timedelta(days=30),
            InventoryLot.quantity_on_hand > 0,
        )
    )
    expiring_lots = expiring_result.scalar() or 0

    # Stockouts (quantity_on_hand = 0 for items with recent dispensing)
    stockout_result = await db.execute(
        select(func.count(StockLevel.id)).where(
            StockLevel.pharmacy_id == pharmacy_id,
            StockLevel.quantity_on_hand <= 0,
            StockLevel.last_dispensed_at >= datetime.now(timezone.utc) - timedelta(days=30),
        )
    )
    stockouts = stockout_result.scalar() or 0

    return {
        "as_of": now.isoformat(),
        "pharmacy_id": str(pharmacy_id),
        "fills": {
            "today": fills_today,
            "queue_depth": total_queue,
            "by_status": queue_by_status,
        },
        "claims": {
            "rejected_today": rejected_claims,
            "avg_adjudication_ms": round(float(avg_response_ms), 0) if avg_response_ms else None,
        },
        "inventory": {
            "expiring_lots_30d": expiring_lots,
            "stockouts": stockouts,
        },
    }


# ── Financial Reporting ────────────────────────────────────────────────────

@router.get("/financial/summary")
async def financial_summary(
    period_start: date = Query(default=None),
    period_end: date = Query(default=None),
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Pharmacy financial summary: gross revenue, total amount paid by PBMs,
    patient copays, DIR fee exposure, and estimated net margin.
    """
    if not period_start:
        period_start = date.today().replace(day=1)  # First of month
    if not period_end:
        period_end = date.today()

    pharmacy_id = staff.pharmacy_id

    # Claim totals
    claim_result = await db.execute(
        select(
            func.count(ClaimTransaction.id).label("claim_count"),
            func.sum(ClaimTransaction.total_amount_paid).label("total_paid"),
            func.sum(ClaimTransaction.patient_pay_amount).label("total_copay"),
            func.sum(ClaimTransaction.ingredient_cost_submitted).label("total_cost_submitted"),
        ).where(
            ClaimTransaction.pharmacy_id == pharmacy_id,
            ClaimTransaction.status == "approved",
            ClaimTransaction.date_of_service >= period_start,
            ClaimTransaction.date_of_service <= period_end,
        )
    )
    claim_row = claim_result.one()

    # DIR fees in period
    dir_result = await db.execute(
        select(func.sum(DIRFeeAdjustment.adjustment_amount)).where(
            DIRFeeAdjustment.pharmacy_id == pharmacy_id,
            DIRFeeAdjustment.posted_date >= period_start,
            DIRFeeAdjustment.posted_date <= period_end,
        )
    )
    dir_total = dir_result.scalar() or 0.0

    # Estimated DIR exposure (claims in period that may have future DIR clawbacks)
    # Rule of thumb: ~3-5% of Medicare Part D reimbursement
    total_paid = float(claim_row.total_paid or 0)
    estimated_future_dir = total_paid * 0.04  # 4% estimate

    # Gross margin estimate (revenue - estimated acquisition cost)
    # Simplified: actual margin requires drug cost data per fill
    gross_revenue = total_paid + float(claim_row.total_copay or 0)
    net_after_dir = gross_revenue - abs(float(dir_total))

    return {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "claim_count": claim_row.claim_count or 0,
        "gross_revenue": round(gross_revenue, 2),
        "pbm_paid": round(total_paid, 2),
        "patient_copays_collected": round(float(claim_row.total_copay or 0), 2),
        "dir_fees_posted": round(float(dir_total), 2),
        "estimated_future_dir_exposure": round(estimated_future_dir, 2),
        "net_after_known_dir": round(net_after_dir, 2),
        "avg_revenue_per_claim": round(gross_revenue / max(1, claim_row.claim_count or 1), 2),
    }


@router.get("/financial/dir-analysis")
async def dir_fee_analysis(
    lookback_months: int = Query(default=6, ge=1, le=24),
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    DIR fee analysis — shows how PBM clawbacks affect actual realized margin
    vs. reimbursement at time of dispense.
    """
    pharmacy_id = staff.pharmacy_id
    cutoff = date.today() - timedelta(days=lookback_months * 30)

    result = await db.execute(
        select(
            DIRFeeAdjustment.payer_name,
            DIRFeeAdjustment.adjustment_type,
            func.count(DIRFeeAdjustment.id).label("count"),
            func.sum(DIRFeeAdjustment.adjustment_amount).label("total_adjustment"),
            func.avg(DIRFeeAdjustment.adjustment_amount).label("avg_adjustment"),
        ).where(
            DIRFeeAdjustment.pharmacy_id == pharmacy_id,
            DIRFeeAdjustment.posted_date >= cutoff,
        ).group_by(
            DIRFeeAdjustment.payer_name,
            DIRFeeAdjustment.adjustment_type,
        ).order_by(func.sum(DIRFeeAdjustment.adjustment_amount))
    )

    rows = result.all()
    return {
        "lookback_months": lookback_months,
        "dir_by_payer": [
            {
                "payer": row.payer_name or "Unknown",
                "adjustment_type": row.adjustment_type,
                "count": row.count,
                "total": round(float(row.total_adjustment), 2),
                "avg_per_claim": round(float(row.avg_adjustment), 2),
            }
            for row in rows
        ],
        "total_dir_exposure": round(
            sum(float(row.total_adjustment) for row in rows), 2
        ),
    }


# ── CMS Star Ratings ───────────────────────────────────────────────────────

@router.get("/quality/star-ratings")
async def cms_star_ratings(
    measurement_year: int = Query(default=None),
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    CMS Star Ratings metrics — PDC (Proportion of Days Covered),
    CMR completion rate, statin adherence for diabetes.
    These drive Medicare Part D reimbursement bonuses.
    """
    if not measurement_year:
        measurement_year = date.today().year

    # PDC calculation requires patient-level adherence data
    # Simplified implementation — production uses HEDIS-spec calculation engine
    period_start = date(measurement_year, 1, 1)
    period_end = date(measurement_year, 12, 31)

    # Count fills for PDC drug classes (statins, RAS antagonists, diabetes meds)
    PDC_DRUG_CLASSES = {
        "statins": ["atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "lovastatin"],
        "diabetes": ["metformin", "glipizide", "glimepiride", "sitagliptin", "empagliflozin"],
        "ras_antagonists": ["lisinopril", "enalapril", "losartan", "valsartan", "amlodipine"],
    }

    pdc_results = {}
    for drug_class, drugs in PDC_DRUG_CLASSES.items():
        fills_result = await db.execute(
            select(func.count(PrescriptionFill.id)).where(
                PrescriptionFill.fill_date >= period_start,
                PrescriptionFill.fill_date <= period_end,
            )
        )
        # Simplified PDC — production requires patient-level days covered calculation
        fill_count = fills_result.scalar() or 0
        # Placeholder: production implementation uses HEDIS PDC formula
        pdc_results[drug_class] = {
            "eligible_fills": fill_count,
            "estimated_pdc": 0.85,  # Placeholder — replace with real calculation
            "star_threshold_3": 0.75,
            "star_threshold_4": 0.83,
            "star_threshold_5": 0.89,
            "estimated_stars": 4,
        }

    return {
        "measurement_year": measurement_year,
        "pdc_metrics": pdc_results,
        "note": "PDC calculation requires patient-level days-covered analysis. Contact PharmPilot support for full HEDIS-compliant calculation.",
    }


# ── Inventory Analytics ────────────────────────────────────────────────────

@router.get("/inventory/velocity")
async def inventory_velocity(
    top_n: int = Query(default=20, ge=5, le=100),
    period_days: int = Query(default=30, ge=7, le=365),
    staff: Staff = Depends(require_permission("reports:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Drug velocity report — top dispensed drugs by volume.
    Used for PAR level optimization and purchasing decisions.
    """
    cutoff = date.today() - timedelta(days=period_days)

    result = await db.execute(
        select(
            PrescriptionFill.ndc_dispensed,
            func.count(PrescriptionFill.id).label("fill_count"),
            func.sum(PrescriptionFill.quantity_dispensed).label("total_units"),
        ).where(
            PrescriptionFill.fill_date >= cutoff,
        ).group_by(
            PrescriptionFill.ndc_dispensed,
        ).order_by(
            func.sum(PrescriptionFill.quantity_dispensed).desc()
        ).limit(top_n)
    )

    rows = result.all()
    return {
        "period_days": period_days,
        "top_drugs": [
            {
                "ndc11": row.ndc_dispensed,
                "fill_count": row.fill_count,
                "total_units": float(row.total_units or 0),
                "avg_daily_units": round(float(row.total_units or 0) / period_days, 2),
            }
            for row in rows
        ],
    }
