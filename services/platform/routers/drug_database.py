"""
Drug Database API Router — Phase 21
=====================================
Endpoints:
  GET  /drug-database/lookup                — NDC / name → RxNorm info
  GET  /drug-database/{ndc}/pricing         — AWP/WAC/AAC margin for one NDC
  GET  /drug-database/{ndc}/label           — FDA-approved prescribing information
  GET  /drug-database/{ndc}/recalls         — Active FDA recalls for an NDC
  GET  /drug-database/{ndc}/patient-savings — Insurance vs. discount card comparison
  GET  /drug-database/{ndc}/alternatives    — Therapeutic alternatives (same ingredient)
  GET  /drug-database/recalls/active        — All active FDA drug recalls (dashboard)
  POST /drug-database/pricing/batch         — Bulk pricing for a list of NDCs
  POST /drug-database/dir-impact            — DIR fee impact report for a fill list
  POST /drug-database/margin-analysis       — Portfolio margin analysis report
  POST /drug-database/savings-scan          — Bulk patient savings opportunity scan
"""
from __future__ import annotations

from typing import Optional
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from services.platform.auth import get_current_user
from services.integrations.drug_database import (
    RxNormClient,
    OpenFDAClient,
    DrugPricingEngine,
    CouponService,
)

log    = structlog.get_logger(__name__)
router = APIRouter()

# ── Shared service instances (module-level singletons) ────────────────────────
_pricing = DrugPricingEngine()
_coupon  = CouponService(pricing_engine=_pricing)


# ── Request / Response Models ─────────────────────────────────────────────────

class NdcNamePair(BaseModel):
    ndc:  str
    name: str = "Unknown Drug"

class BatchPricingRequest(BaseModel):
    drugs:    list[NdcNamePair]
    qty:      float = 30.0
    dir_tier: str   = "medium"

class FillRecord(BaseModel):
    ndc:                  str
    drug_name:            str
    qty_dispensed:        float = 30.0
    reimbursement_amount: float = 0.0

class DIRImpactRequest(BaseModel):
    fills:    list[FillRecord]
    dir_tier: str = "medium"

class MarginAnalysisRequest(BaseModel):
    drugs:    list[NdcNamePair]
    qty:      float = 30.0
    dir_tier: str   = "medium"

class FillSavingsRecord(BaseModel):
    ndc:              str
    drug_name:        str
    qty:              float          = 30.0
    insurance_copay:  Optional[float] = None

class SavingsScanRequest(BaseModel):
    fills: list[FillSavingsRecord]


# ── NDC / Name Lookup ──────────────────────────────────────────────────────────

@router.get("/lookup")
async def lookup_drug(
    ndc:  Optional[str] = Query(None, description="11-digit NDC (any format)"),
    name: Optional[str] = Query(None, description="Drug brand or generic name"),
    _user = Depends(get_current_user),
):
    """
    Resolve an NDC or drug name to RxNorm canonical info.
    Returns RxCUI, normalized name, brand name, and pharmacological class.
    """
    if not ndc and not name:
        raise HTTPException(400, "Provide either 'ndc' or 'name' query parameter")

    async with RxNormClient() as rx:
        if ndc:
            result = await rx.full_drug_lookup(ndc)
        else:
            rxcui = await rx.name_to_rxcui(name)  # type: ignore[arg-type]
            if not rxcui:
                return {"found": False, "query": name}
            info    = await rx.rxcui_to_info(rxcui)
            classes = await rx.rxcui_drug_class(rxcui)
            result  = {
                "found":        True,
                "query":        name,
                "rxcui":        rxcui,
                "rxnorm_name":  info.get("rxnorm_name"),
                "brand_name":   info.get("brand_name"),
                "drug_classes": classes,
            }

    return result


# ── Single NDC Pricing ─────────────────────────────────────────────────────────

@router.get("/{ndc}/pricing")
async def get_drug_pricing(
    ndc:      str,
    name:     str   = Query("Unknown Drug", description="Drug name for display"),
    qty:      float = Query(30.0, description="Dispense quantity"),
    dir_tier: str   = Query("medium", description="DIR fee tier: zero|low|medium|high"),
    _user = Depends(get_current_user),
):
    """
    Return full pricing breakdown for one NDC:
    AWP, WAC, AAC, gross margin, DIR fee, net margin, U&C cash price.
    """
    price = _pricing.get_price(ndc, name)
    return price.summary(qty=qty, dir_tier=dir_tier)


# ── FDA Drug Label ─────────────────────────────────────────────────────────────

@router.get("/{ndc}/label")
async def get_drug_label(
    ndc:  str,
    name: Optional[str] = Query(None, description="Fallback name if NDC not in OpenFDA"),
    _user = Depends(get_current_user),
):
    """
    Return FDA-approved prescribing information (package insert) for an NDC.
    Falls back to name search if NDC is not found directly.
    """
    async with OpenFDAClient() as fda:
        label = await fda.get_label_by_ndc(ndc)
        if not label and name:
            label = await fda.get_label_by_name(name)

    if not label:
        return {"found": False, "ndc": ndc}

    return {"found": True, "ndc": ndc, "label": label}


# ── NDC-Specific Recalls ───────────────────────────────────────────────────────

@router.get("/{ndc}/recalls")
async def get_recalls_for_ndc(
    ndc: str,
    _user = Depends(get_current_user),
):
    """
    Check whether a specific NDC or product family is under active FDA recall.
    Returns empty list if no active recalls found.
    """
    async with OpenFDAClient() as fda:
        recalls = await fda.get_recalls_by_ndc(ndc)

    return {
        "ndc":          ndc,
        "recall_count": len(recalls),
        "has_recall":   len(recalls) > 0,
        "recalls":      recalls,
    }


# ── Patient Savings Comparison ─────────────────────────────────────────────────

@router.get("/{ndc}/patient-savings")
async def get_patient_savings(
    ndc:             str,
    drug_name:       str            = Query("Unknown Drug"),
    qty:             float          = Query(30.0),
    insurance_copay: Optional[float] = Query(None, description="Patient's insurance copay for comparison"),
    patient_insured: bool            = Query(True),
    _user = Depends(get_current_user),
):
    """
    Full patient savings comparison:
    insurance copay vs. discount cards vs. manufacturer assistance.
    Returns ranked options and pharmacist recommendation.
    """
    result = _coupon.compare_for_patient(
        ndc=ndc,
        drug_name=drug_name,
        qty=qty,
        insurance_copay=insurance_copay,
        patient_insured=patient_insured,
    )
    return result


# ── Therapeutic Alternatives ───────────────────────────────────────────────────

@router.get("/{ndc}/alternatives")
async def get_therapeutic_alternatives(
    ndc:  str,
    name: Optional[str] = Query(None, description="Drug name (used for RxCUI lookup if NDC fails)"),
    _user = Depends(get_current_user),
):
    """
    Return therapeutic alternatives (drugs with the same active ingredient).
    Useful for shortage-driven substitution and formulary management.
    """
    async with RxNormClient() as rx:
        rxcui = await rx.ndc_to_rxcui(ndc)
        if not rxcui and name:
            rxcui = await rx.name_to_rxcui(name)
        if not rxcui:
            return {"ndc": ndc, "found": False, "alternatives": []}

        alts = await rx.get_therapeutic_alternatives(rxcui)

    # Attach pricing to each alternative
    priced_alts = []
    for alt in alts:
        # Alt doesn't have an NDC directly — use name lookup for demo pricing
        alt_price = _pricing.get_price(
            ndc=f"RXCUI{alt['rxcui']}",  # synthetic NDC for pricing seed
            drug_name=alt.get("name", "Unknown"),
        )
        priced_alts.append({
            **alt,
            "unit_awp": float(alt_price.unit_price_awp),
            "unit_aac": float(alt_price.unit_price_aac),
        })

    return {
        "ndc":          ndc,
        "source_rxcui": rxcui,
        "found":        True,
        "alternatives": priced_alts,
    }


# ── All Active Recalls (Dashboard Feed) ───────────────────────────────────────

@router.get("/recalls/active")
async def get_active_recalls(
    limit: int = Query(50, le=100, description="Maximum recalls to return"),
    _user = Depends(get_current_user),
):
    """
    Fetch the most recent active FDA drug recall/enforcement actions.
    Powers the recall alert banner in Command Center and Inventory dashboards.
    Class I = critical (death/serious injury risk).
    """
    async with OpenFDAClient() as fda:
        recalls = await fda.get_active_recalls(limit=limit)

    # Group by severity for dashboard summary
    critical = [r for r in recalls if r.get("severity_level") == "critical"]
    high     = [r for r in recalls if r.get("severity_level") == "high"]
    low      = [r for r in recalls if r.get("severity_level") == "low"]

    return {
        "total":          len(recalls),
        "critical_count": len(critical),
        "high_count":     len(high),
        "low_count":      len(low),
        "recalls":        recalls,
    }


# ── Batch Pricing ──────────────────────────────────────────────────────────────

@router.post("/pricing/batch")
async def batch_pricing(
    body: BatchPricingRequest,
    _user = Depends(get_current_user),
):
    """
    Return pricing summaries for a list of NDC/name pairs.
    Used by Inventory AI dashboard to populate the pricing margin table.
    """
    pairs   = [(d.ndc, d.name) for d in body.drugs]
    results = _pricing.batch_summary(
        ndc_name_pairs=pairs,
        qty=body.qty,
        dir_tier=body.dir_tier,
    )
    return {"count": len(results), "drugs": results}


# ── DIR Impact Report ──────────────────────────────────────────────────────────

@router.post("/dir-impact")
async def dir_impact_report(
    body: DIRImpactRequest,
    _user = Depends(get_current_user),
):
    """
    Calculate aggregate DIR fee exposure for a batch of fills.
    Shows total retroactive clawback risk by PBM tier.
    Used by Financial Operations dashboard.
    """
    fills = [f.model_dump() for f in body.fills]
    report = _pricing.dir_impact_report(fills=fills, dir_tier=body.dir_tier)
    return report


# ── Portfolio Margin Analysis ──────────────────────────────────────────────────

@router.post("/margin-analysis")
async def margin_analysis(
    body: MarginAnalysisRequest,
    _user = Depends(get_current_user),
):
    """
    Full margin analysis for a drug list (formulary / inventory snapshot).
    Returns per-drug profitability flags and portfolio-level summary.
    """
    pairs  = [(d.ndc, d.name) for d in body.drugs]
    report = _pricing.margin_analysis_report(
        ndc_name_pairs=pairs,
        qty=body.qty,
        dir_tier=body.dir_tier,
    )
    return report


# ── Patient Savings Scan ───────────────────────────────────────────────────────

@router.post("/savings-scan")
async def savings_scan(
    body: SavingsScanRequest,
    _user = Depends(get_current_user),
):
    """
    Scan a patient's medication list for discount card savings opportunities.
    Returns fills where a discount card saves >$5 vs. their insurance copay,
    sorted by highest savings.
    """
    fills  = [f.model_dump() for f in body.fills]
    result = _coupon.bulk_savings_scan(fills=fills)
    return result
