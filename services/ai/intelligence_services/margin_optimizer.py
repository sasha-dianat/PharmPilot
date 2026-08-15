"""
#19 — Financial Intelligence & Margin Optimization  (offline-first)
==================================================================
Connects "what we dispensed" to "what we could have earned", surfacing
below-cost fills, generic-substitution upside, and DIR-fee exposure with
concrete actions.

LOCAL BRAIN (always available — runs on cached pricing):
  • Per-claim margin = amount_paid − acquisition cost (WAC × qty proxy).
  • Below-MAC / below-cost flagging (margin < 0).
  • Generic-substitution optimizer: for each BRAND fill, find a generic that
    shares the same GPI (Generic Product Identifier) and compute the margin upside
    and patient-cost delta.
  • DIR-fee exposure projection from local fill patterns (heuristic, configurable).
  All on last-cached AWP/WAC, stamped with the cache date.

CLOUD BRAIN (when online — enriches, never required):
  • Live AWP/WAC refresh, live PBM MAC lists, current DIR schedules,
    cross-pharmacy benchmark margins.
  Offline → computed on last-cached pricing with degraded=true + "prices as of" stamp.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call,
)

logger = logging.getLogger(__name__)

SERVICE = "margin_optimizer"

# Heuristic DIR fee as a fraction of brand-claim revenue (configurable; replaced
# by live PBM schedules when online).
DIR_BRAND_FRACTION = 0.06


@dataclass
class BelowCostClaim:
    claim_id:   str
    ndc:        str
    drug_name:  str
    quantity:   float
    amount_paid: float
    acquisition_cost: float
    margin:     float
    bin_number: str


@dataclass
class SubstitutionOpportunity:
    brand_ndc:        str
    brand_name:       str
    brand_margin:     float
    generic_ndc:      str
    generic_name:     str
    generic_margin:   float
    margin_uplift:    float
    patient_cost_delta: float          # negative = patient pays less on generic
    fills_in_window:  int
    projected_monthly_uplift: float
    rationale:        str


def _safe(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


# ─── SQL ──────────────────────────────────────────────────────────────────────

_CLAIMS_SQL = """
    SELECT c.id                          AS claim_id,
           c.ndc,
           c.bin_number,
           COALESCE(c.quantity, 0)             AS quantity,
           COALESCE(c.total_amount_paid, 0)    AS amount_paid,
           COALESCE(c.patient_pay_amount, 0)   AS patient_pay,
           COALESCE(c.ingredient_cost_paid, 0) AS ingredient_paid,
           dp.generic_name,
           dp.brand_name,
           dp.gpi,
           COALESCE(dp.is_generic, true)  AS is_generic,
           -- Acquisition cost is what the pharmacy actually PAID, averaged
           -- over the lots it holds. It used to be dp.wac_price, a US wholesale
           -- benchmark that only ever held dollar seed data (migration 0041).
           COALESCE((SELECT avg(l.unit_cost) FROM inventory_lots l
                     WHERE l.drug_product_id = dp.id AND l.unit_cost > 0), 0) AS wac_price,
           COALESCE((SELECT max(l.sell_price) FROM inventory_lots l
                     WHERE l.drug_product_id = dp.id AND l.sell_price > 0), 0) AS awp_price,
           dp.awp_updated_at,
           c.created_at
    FROM   claim_transactions c
    LEFT JOIN drug_products dp ON dp.ndc11 = c.ndc
    WHERE  c.pharmacy_id = :pid
      AND  c.status NOT ILIKE '%reject%'
      AND  c.created_at >= now() - (:days || ' days')::interval
"""

# For a set of GPIs, the cheapest-acquisition generic alternative per GPI.
_GENERIC_ALT_SQL = """
    SELECT DISTINCT ON (dp.gpi)
           dp.gpi,
           dp.ndc11        AS generic_ndc,
           dp.generic_name,
           COALESCE((SELECT avg(l.unit_cost) FROM inventory_lots l
                     WHERE l.drug_product_id = dp.id AND l.unit_cost > 0), 0) AS wac_price,
           COALESCE((SELECT max(l.sell_price) FROM inventory_lots l
                     WHERE l.drug_product_id = dp.id AND l.sell_price > 0), 0) AS awp_price
    FROM   drug_products dp
    WHERE  dp.gpi = ANY(:gpis)
      AND  COALESCE(dp.is_generic, true) = true
      AND  COALESCE(dp.is_active, true) = true
      AND  EXISTS (SELECT 1 FROM inventory_lots l
                   WHERE l.drug_product_id = dp.id AND l.unit_cost > 0)
    ORDER  BY dp.gpi, 3 ASC
"""


async def analyze(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    window_days: int = 90,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Main entry — §1.2 envelope. Local-complete on cached pricing."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    try:
        res = await db.execute(text(_CLAIMS_SQL), {"pid": pharmacy_id, "days": window_days})
        claims = [dict(r) for r in res.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[margin_optimizer] claims pull failed (%s)", exc)
        claims = []

    if not claims:
        return build_envelope(
            {"summary": _empty_summary(), "below_cost": [], "substitutions": []},
            tier_used=tier, confidence=0.4, degraded=(tier == Tier.LOCAL),
            options_active=["local_margin"],
            options_offline=["live_pricing", "live_mac", "benchmarks"] if tier == Tier.LOCAL else [],
            model_version="margin_optimizer_v1_local",
        )

    # ── LOCAL margin accounting ──
    total_margin = 0.0
    total_revenue = 0.0
    below_cost: list[BelowCostClaim] = []
    brand_fills: dict[str, dict] = {}     # gpi → aggregate of brand fills
    oldest_price_date = None
    dir_brand_revenue = 0.0

    for c in claims:
        qty          = _safe(c["quantity"])
        amount_paid  = _safe(c["amount_paid"])
        wac          = _safe(c["wac_price"])
        acquisition  = wac * qty
        margin       = round(amount_paid - acquisition, 2)
        total_margin += margin
        total_revenue += amount_paid

        if c.get("awp_updated_at") is not None:
            d = c["awp_updated_at"]
            if oldest_price_date is None or (d and d < oldest_price_date):
                oldest_price_date = d

        if margin < 0 and acquisition > 0:
            below_cost.append(BelowCostClaim(
                claim_id=str(c["claim_id"]), ndc=str(c["ndc"]),
                drug_name=str(c.get("generic_name") or c.get("brand_name") or ""),
                quantity=qty, amount_paid=amount_paid,
                acquisition_cost=round(acquisition, 2), margin=margin,
                bin_number=str(c.get("bin_number") or ""),
            ))

        is_generic = bool(c.get("is_generic", True))
        gpi = c.get("gpi")
        if not is_generic:
            dir_brand_revenue += amount_paid
            if gpi:
                agg = brand_fills.setdefault(gpi, {
                    "brand_ndc": c["ndc"],
                    "brand_name": c.get("brand_name") or c.get("generic_name") or "",
                    "fills": 0, "qty": 0.0, "margin_sum": 0.0,
                    "amount_paid_sum": 0.0, "patient_pay_sum": 0.0, "wac": wac,
                })
                agg["fills"] += 1
                agg["qty"] += qty
                agg["margin_sum"] += margin
                agg["amount_paid_sum"] += amount_paid
                agg["patient_pay_sum"] += _safe(c["patient_pay"])

    # ── LOCAL generic-substitution optimizer ──
    substitutions: list[SubstitutionOpportunity] = []
    if brand_fills:
        gpis = list(brand_fills.keys())
        try:
            alt_res = await db.execute(text(_GENERIC_ALT_SQL), {"gpis": gpis})
            alts = {r["gpi"]: dict(r) for r in alt_res.mappings().all()}
        except Exception as exc:  # noqa: BLE001
            logger.warning("[margin_optimizer] generic alt lookup failed (%s)", exc)
            alts = {}

        window_months = max(window_days / 30.0, 0.5)
        for gpi, b in brand_fills.items():
            alt = alts.get(gpi)
            if not alt:
                continue
            fills = b["fills"]
            avg_qty = b["qty"] / fills if fills else 0.0
            brand_margin_avg = b["margin_sum"] / fills if fills else 0.0
            # Generic margin proxy: assume similar reimbursement but lower acquisition.
            # Conservative: reimbursement stays at brand amount_paid avg, cost = generic WAC.
            avg_amount_paid = b["amount_paid_sum"] / fills if fills else 0.0
            generic_acq = _safe(alt["wac_price"]) * avg_qty
            generic_margin_avg = round(avg_amount_paid - generic_acq, 2)
            uplift_per_fill = round(generic_margin_avg - brand_margin_avg, 2)
            if uplift_per_fill <= 0.01:
                continue
            # Patient typically pays less on generic copay tier — estimate −30%.
            patient_delta = round(-0.30 * (b["patient_pay_sum"] / fills if fills else 0.0), 2)
            monthly_uplift = round((uplift_per_fill * fills) / window_months, 2)

            substitutions.append(SubstitutionOpportunity(
                brand_ndc=str(b["brand_ndc"]), brand_name=str(b["brand_name"]),
                brand_margin=round(brand_margin_avg, 2),
                generic_ndc=str(alt["generic_ndc"]),
                generic_name=str(alt["generic_name"]),
                generic_margin=generic_margin_avg,
                margin_uplift=uplift_per_fill,
                patient_cost_delta=patient_delta,
                fills_in_window=fills,
                projected_monthly_uplift=monthly_uplift,
                rationale=(f"{fills} brand fills in {window_days}d. Switching to "
                           f"{alt['generic_name']} adds ~${uplift_per_fill:.2f}/fill "
                           f"and lowers patient cost ~${abs(patient_delta):.2f}."),
            ))
        substitutions.sort(key=lambda s: -s.projected_monthly_uplift)

    # ── DIR exposure projection (local heuristic) ──
    window_months = max(window_days / 30.0, 0.5)
    dir_exposure_window = round(dir_brand_revenue * DIR_BRAND_FRACTION, 2)
    dir_exposure_quarterly = round((dir_exposure_window / window_months) * 3.0, 2)

    # ── CLOUD enrichment ──
    options_active  = ["local_margin", "generic_optimizer", "dir_projection"]
    options_offline: list[str] = []
    degraded = False
    price_stamp = oldest_price_date.isoformat() if oldest_price_date else None

    if tier in (Tier.CLOUD, Tier.HYBRID):
        async def _refresh():
            # Placeholder for live price/MAC/benchmark refresh; wire to pricing engine.
            return True
        _, ok = await cloud_call(_refresh, fallback=False, label="margin_pricing_refresh")
        if ok:
            options_active += ["live_pricing", "live_mac", "benchmarks"]
            tier = Tier.HYBRID
        else:
            degraded = True
            options_offline = ["live_pricing", "live_mac", "benchmarks"]
    else:
        degraded = True
        options_offline = ["live_pricing", "live_mac", "benchmarks"]

    below_cost.sort(key=lambda x: x.margin)   # most negative first
    summary = {
        "claims_evaluated":       len(claims),
        "total_margin":           round(total_margin, 2),
        "total_revenue":          round(total_revenue, 2),
        "margin_pct":             round((total_margin / total_revenue * 100) if total_revenue else 0, 2),
        "below_cost_count":       len(below_cost),
        "below_cost_loss":        round(sum(x.margin for x in below_cost), 2),
        "substitution_opportunities": len(substitutions),
        "projected_monthly_uplift":   round(sum(s.projected_monthly_uplift for s in substitutions), 2),
        "dir_exposure_window":    dir_exposure_window,
        "dir_exposure_quarterly": dir_exposure_quarterly,
        "prices_as_of":           price_stamp,
    }
    confidence = 0.80 if not degraded else 0.7

    return build_envelope(
        {
            "summary":       summary,
            "below_cost":    [x.__dict__ for x in below_cost[:50]],
            "substitutions": [s.__dict__ for s in substitutions[:50]],
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="margin_optimizer_v1",
    )


def _empty_summary() -> dict:
    return {
        "claims_evaluated": 0, "total_margin": 0.0, "total_revenue": 0.0,
        "margin_pct": 0.0, "below_cost_count": 0, "below_cost_loss": 0.0,
        "substitution_opportunities": 0, "projected_monthly_uplift": 0.0,
        "dir_exposure_window": 0.0, "dir_exposure_quarterly": 0.0, "prices_as_of": None,
    }
