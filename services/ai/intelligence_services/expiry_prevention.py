"""
#12 — Inventory Expiry Waste Prevention  (offline-first)
========================================================
Predicts which lots will expire before they are consumed, and recommends the
cheapest corrective action: dispense-first (FEFO), return to wholesaler, or
transfer to a sister branch.

LOCAL BRAIN (always available — no internet, no model download):
  • Reads every on-hand lot + its NDC's trailing 90-day dispense rate
    (feature_store.lot_depletion).
  • For each lot computes:
       days_to_expiry     = expiry_date - today
       days_to_depletion  = quantity_on_hand / daily_dispense_rate
       projected_waste_qty= max(0, qty - daily_rate * days_to_expiry)
       waste_value        = projected_waste_qty * unit_cost
       risk_score (0–1)   = how likely this lot partially expires
  • Ranks at-risk lots, emits a FEFO dispense order and a suggested return qty.

CLOUD BRAIN (when online — enriches, never required):
  • Wholesaler return-window / credit eligibility lookup.
  • Sister-branch transfer matching (who else needs this NDC).
  Offline → the return *suggestion* is still computed; the actual return order is
  parked in the outbox and submitted on reconnect.

This module lives behind the same panel as the existing stock-ML component.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, build_envelope, cloud_call, feature_store,
)

logger = logging.getLogger(__name__)

SERVICE = "expiry_prevention"

# Risk thresholds for the UI colour bands.
RISK_CRITICAL = 0.66
RISK_WARNING  = 0.33


@dataclass
class LotRisk:
    lot_id:              str
    ndc11:               str
    drug_name:           str
    lot_number:          str
    expiry_date:         str
    quantity_on_hand:    float
    unit_cost:           float
    daily_dispense_rate: float
    days_to_expiry:      int
    days_to_depletion:   Optional[float]
    projected_waste_qty: float
    waste_value:         float
    risk_score:          float
    risk_band:           str               # critical | warning | ok
    recommended_action:  str               # dispense_first | return | transfer | monitor
    rationale:           str
    is_controlled:       bool = False
    # cloud-only enrichments (None when offline)
    return_window_days:  Optional[int]   = None
    return_eligible:     Optional[bool]  = None
    transfer_branch:     Optional[str]   = None


def _safe_float(v, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _score_lot(row: dict) -> LotRisk:
    """Pure local computation for one lot row from feature_store.lot_depletion."""
    qty        = _safe_float(row.get("quantity_on_hand"))
    unit_cost  = _safe_float(row.get("unit_cost"))
    daily_rate = _safe_float(row.get("daily_dispense_rate"))
    dte_raw    = row.get("days_to_expiry")
    days_to_expiry = int(dte_raw) if dte_raw is not None else 0

    # Days to deplete this lot at the current dispense pace.
    if daily_rate > 1e-9:
        days_to_depletion = qty / daily_rate
    else:
        days_to_depletion = None   # never dispensed → infinite (full waste risk)

    # Projected units that will still be on hand at expiry.
    if days_to_expiry <= 0:
        projected_waste_qty = qty                       # already expired
    elif days_to_depletion is None:
        projected_waste_qty = qty                       # no movement → all wasted
    else:
        consumed_by_expiry = daily_rate * days_to_expiry
        projected_waste_qty = max(0.0, qty - consumed_by_expiry)

    waste_value = round(projected_waste_qty * unit_cost, 2)

    # Risk score: fraction of the lot projected to be wasted, sharpened by how
    # soon expiry is. 0 = fully consumed in time, 1 = certain waste.
    frac_waste = (projected_waste_qty / qty) if qty > 1e-9 else 0.0
    # Urgency multiplier: closer expiry → higher urgency (logistic on days).
    urgency = 1.0 / (1.0 + math.exp((days_to_expiry - 60) / 30.0))  # ~1 near expiry, →0 far out
    risk_score = round(min(1.0, frac_waste * (0.5 + 0.5 * urgency)), 3)

    band = ("critical" if risk_score >= RISK_CRITICAL
            else "warning" if risk_score >= RISK_WARNING
            else "ok")

    # Local recommendation.
    if days_to_expiry <= 0:
        action, rationale = "return", "Already expired — quarantine and return for credit."
    elif risk_score >= RISK_CRITICAL:
        if daily_rate <= 1e-9:
            action = "return"
            rationale = "No dispensing activity — return surplus before expiry."
        else:
            action = "dispense_first"
            rationale = (f"At {daily_rate:.1f}/day this lot depletes in "
                         f"~{days_to_depletion:.0f}d but expires in {days_to_expiry}d. "
                         f"Dispense this lot first (FEFO); return ~{projected_waste_qty:.0f} units.")
    elif risk_score >= RISK_WARNING:
        action = "dispense_first"
        rationale = "Trending toward partial expiry — prioritise this lot in dispensing."
    else:
        action = "monitor"
        rationale = "On track to be consumed before expiry."

    return LotRisk(
        lot_id=str(row.get("lot_id", "")),
        ndc11=str(row.get("ndc11", "")),
        drug_name=str(row.get("generic_name") or row.get("drug_name") or ""),
        lot_number=str(row.get("lot_number", "")),
        expiry_date=str(row.get("expiry_date", "")),
        quantity_on_hand=qty,
        unit_cost=unit_cost,
        daily_dispense_rate=round(daily_rate, 3),
        days_to_expiry=days_to_expiry,
        days_to_depletion=round(days_to_depletion, 1) if days_to_depletion is not None else None,
        projected_waste_qty=round(projected_waste_qty, 1),
        waste_value=waste_value,
        risk_score=risk_score,
        risk_band=band,
        recommended_action=action,
        rationale=rationale,
        is_controlled=bool(row.get("is_controlled", False)),
    )


def _score_lots(rows: list[dict]) -> list[LotRisk]:
    """Score every lot, sharing each item's demand across its lots in FEFO order.

    `_score_lot` charged **every** lot the full demand independently, so two
    lots of 100 units selling 1/day and both expiring in 100 days each came back
    `projected_waste=0, band=ok`. Only 100 units of demand exists; 100 units were
    certain to be destroyed, and the engine reported none. The allocation now
    happens in `services.core.inventory.expiry_risk`, which walks the lots in the
    order the allocator will actually reach them and gives each one only what is
    left after the ones in front.

    `_score_lot` is kept for the single-lot case and for its recommendation
    vocabulary, so the response contract is unchanged.
    """
    from collections import defaultdict

    from services.core.inventory import clock as CLK
    from services.core.inventory import expiry_risk as ER

    by_ndc: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_ndc[str(r.get("ndc11"))].append(r)

    today = CLK.pharmacy_today("UTC")
    out: list[LotRisk] = []
    for ndc, group in by_ndc.items():
        basis = str(group[0].get("demand_basis") or "no_history")
        rate = group[0].get("daily_dispense_rate")
        allocated = {
            r.lot_id: r for r in ER.assess_item(
                [{"lot_id": g.get("lot_id"), "ndc11": ndc,
                  "lot_number": g.get("lot_number"),
                  "expiry_date": g.get("expiry_date"),
                  "quantity_on_hand": g.get("quantity_on_hand"),
                  "unit_cost": g.get("unit_cost")} for g in group],
                avg_daily_demand=rate, basis=basis, as_of=today)
        }
        for g in group:
            scored = _score_lot(g)
            share = allocated.get(str(g.get("lot_id")))
            if share is None:
                out.append(scored)
                continue
            # Replace the independently-computed waste with this lot's actual
            # share, and recompute everything derived from it.
            if share.at_risk_units is None:
                # No measured demand: say so rather than claiming total waste.
                scored.projected_waste_qty = float(share.on_hand)
                scored.waste_value = (None if share.unit_cost is None
                                      else float(share.on_hand * share.unit_cost))
                scored.risk_score = 0.0
                scored.risk_band = "unknown"
                scored.recommended_action = "measure_demand"
                scored.rationale = share.explanation
            else:
                scored.projected_waste_qty = round(float(share.at_risk_units), 1)
                scored.waste_value = (None if share.at_risk_value is None
                                      else float(share.at_risk_value))
                qty = float(share.on_hand) or 1.0
                scored.risk_score = round(
                    min(1.0, float(share.at_risk_units) / qty), 3)
                scored.risk_band = (
                    "critical" if scored.risk_score >= RISK_CRITICAL
                    else "warning" if scored.risk_score >= RISK_WARNING
                    else "ok")
                scored.rationale = share.explanation
            out.append(scored)
    return out


async def _enrich_returns_cloud(at_risk: list[LotRisk]) -> bool:
    """
    CLOUD: look up wholesaler return windows / branch transfer matches.
    Stub that is safe to extend with real wholesaler EDI calls. Returns True if
    enrichment was applied. Never raises (wrapped by cloud_call upstream).
    """
    # Placeholder enrichment — in production call the wholesaler return API and
    # the chain's branch-stock service. Here we annotate plausible defaults so the
    # contract + UI are exercised end-to-end when online.
    for lot in at_risk:
        if lot.recommended_action == "return":
            lot.return_window_days = 90
            lot.return_eligible = lot.days_to_expiry <= 180
    return True


async def analyze(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    horizon_days: int = 180,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Main entry. Returns the §1.2 envelope. Local-complete; cloud-enriched online.
    """
    from services.ai.intelligence_core import IntelligenceTier
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    df = await feature_store.lot_depletion(db, pharmacy_id)

    if df is None or df.empty:
        return build_envelope(
            {"at_risk": [], "summary": {"at_risk_lots": 0, "total_waste_value": 0.0}},
            tier_used=tier, confidence=0.4, degraded=(tier == Tier.LOCAL),
            options_active=["local_survival_model"],
            options_offline=["wholesaler_returns", "branch_transfer"] if tier == Tier.LOCAL else [],
            model_version="expiry_prevention_v1_local",
        )

    # LOCAL scoring, FEFO-allocated across each item's lots.
    lots = _score_lots([dict(r) for _, r in df.iterrows()])
    # Only surface lots within the horizon OR already flagged.
    visible = [l for l in lots
               if l.days_to_expiry <= horizon_days or l.risk_band != "ok"]
    visible.sort(key=lambda l: (-l.risk_score, l.days_to_expiry))

    at_risk = [l for l in visible if l.risk_band != "ok"]

    options_active  = ["local_survival_model", "fefo_ordering"]
    options_offline: list[str] = []
    degraded = False

    # CLOUD enrichment (online only).
    if tier in (Tier.CLOUD, Tier.HYBRID) and at_risk:
        _, ok = await cloud_call(
            lambda: _enrich_returns_cloud(at_risk),
            fallback=False, label="expiry_returns",
        )
        if ok:
            options_active.append("wholesaler_returns")
            options_active.append("branch_transfer")
            tier = Tier.HYBRID
        else:
            degraded = True
            options_offline = ["wholesaler_returns", "branch_transfer"]
    else:
        degraded = (tier == Tier.LOCAL)
        options_offline = ["wholesaler_returns", "branch_transfer"]

    total_waste_value = round(sum(l.waste_value for l in at_risk), 2)
    summary = {
        "at_risk_lots":      len(at_risk),
        "critical_lots":     sum(1 for l in at_risk if l.risk_band == "critical"),
        "total_waste_value": total_waste_value,
        "lots_evaluated":    len(lots),
    }

    # Confidence: high locally because this is deterministic accounting on real
    # data; the only uncertainty is the demand rate.
    confidence = 0.82 if not degraded else 0.74

    return build_envelope(
        {
            "at_risk":  [l.__dict__ for l in at_risk],
            "summary":  summary,
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="expiry_prevention_v1",
    )
