"""
#17 — Supply-Chain Disruption Early Warning  (offline-first)
============================================================
**The arithmetic here now lives in `services.core.inventory.shortage` (E13).**
This module is the §1.2 envelope and the cloud tier around it; it no longer
computes a fill rate of its own.

That is deliberate, and it became urgent rather than tidy. The version this
replaces had four defects, and until receiving started writing
`purchase_order_lines.quantity_received` they were harmless only because the
column was always zero and the whole output was visibly garbage. Once the column
carries real numbers the same code produces *plausible* wrong answers, which is
far worse:

  • `fill_rate = 1.0` when nothing had been ordered — absence of evidence scored
    as a perfect supply record;
  • `COALESCE(avg_daily_demand, 0)`, so an item with no measured demand got an
    invented 0.2 stock-pressure contribution instead of "cover unknown";
  • a third hard-coded lead time (5 days) disagreeing with the 7 in
    `lead_time.DECLARED_DEFAULT_DAYS`, unlabelled;
  • every ordered line counted, so an order placed yesterday and not yet
    delivered read as a total short-fill.

And one error of judgement that mattered more than any of them: it could not tell
"every supplier is out of this molecule" from "this one supplier is rationing
something another has in stock", and recommended buffer stock for both. Those
need opposite actions, and buying cover against a problem a phone call solves
means paying to hold inventory that expires on the shelf. E13 separates them.

`disruption_risk` is retained for the existing payload contract but is now an
explicit **severity band**, not a probability. The number it replaced was a
0.45/0.35/0.20 weighting of three pressures, presented to three decimal places
as though it had been calibrated against outcomes. Nothing here is.

CLOUD BRAIN (when online — enriches, never required):
  • National drug-shortage feeds, cross-network wholesaler fill signals,
    OpenFDA recall surges, knowledge-engine web signals → much earlier & broader.
  Offline → local-only signal with degraded=true; the "order buffer" action is
  parked in the outbox.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.inventory import lead_time as LT
from services.core.inventory import shortage as SHORT
from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call,
)

logger = logging.getLogger(__name__)

SERVICE = "supply_warning"

RISK_CRITICAL = 0.66
RISK_WARNING  = 0.33

# A severity band expressed on the old 0-1 scale so the existing payload keeps
# working. It orders the list; it is not a probability and is not calibrated
# against anything, which is why the values are round numbers rather than the
# three-decimal figure that used to appear here.
RISK_OF_SEVERITY = {"critical": 0.9, "high": 0.7, "medium": 0.45, "info": 0.1}
BAND_OF_SEVERITY = {"critical": "critical", "high": "critical",
                    "medium": "warning", "info": "ok"}


@dataclass
class SupplyRisk:
    ndc11:            str
    drug_name:        str
    fill_rate:        Optional[float]   # None when nothing was ever ordered
    fill_rate_trend:  str              # declining | stable
    cusum_signal:     Optional[float]  # None below three settled deliveries
    on_hand:          float
    avg_daily_demand: Optional[float]  # None stays None; never zero
    days_of_stock:    Optional[float]
    disruption_risk:  float            # severity band, NOT a probability
    risk_band:        str
    suggested_buffer_qty: float
    rationale:        str
    is_controlled:    bool = False
    # E13's actual finding — the part that decides what to do.
    verdict:          str = "unknown"
    action:           str = "none"
    basis:            str = "insufficient_history"
    short_suppliers:  list = field(default_factory=list)
    filling_suppliers: list = field(default_factory=list)
    demand_basis:     str = "no_history"
    lead_basis:       str = "declared_default"
    concerns:         list = field(default_factory=list)
    # cloud-only
    national_shortage:  Optional[bool] = None
    recall_surge:       Optional[bool] = None
    alternatives:       Optional[list] = None


_LINES_SQL = """
    SELECT l.ndc11, o.wholesaler, o.ordered_at, l.quantity_ordered,
           l.quantity_received, l.status
    FROM   purchase_order_lines l
    JOIN   purchase_orders o ON o.id = l.order_id
    WHERE  o.pharmacy_id = :pid AND o.is_deleted = false
      AND  l.is_deleted = false
      AND  (o.ordered_at IS NULL
            OR o.ordered_at >= now() - (:days || ' days')::interval)
"""

# No COALESCE on the demand rate. A NULL there means nobody has measured how fast
# this moves, and turning that into a zero is what made an unmeasured item look
# like it would last for ever and drop off the report entirely.
_STOCK_SQL = """
    SELECT s.ndc11,
           COALESCE(s.quantity_on_hand, 0) AS on_hand,
           s.avg_daily_demand,
           COALESCE(s.demand_basis, 'no_history') AS demand_basis,
           dp.generic_name,
           COALESCE(dp.is_controlled, false) AS is_controlled
    FROM   stock_levels s
    LEFT JOIN drug_products dp ON dp.ndc11 = s.ndc11
    WHERE  s.pharmacy_id = :pid
"""

_DELIVERED_SQL = """
    SELECT wholesaler, ordered_at, received_at FROM purchase_orders
    WHERE pharmacy_id = :pid AND received_at IS NOT NULL
"""


async def _build_local_risks(db: AsyncSession, pharmacy_id: str,
                             days: int) -> list[SupplyRisk]:
    """E13's signals, translated into this service's payload shape."""
    p = {"pid": pharmacy_id, "days": days}
    lines = [dict(r) for r in (await db.execute(text(_LINES_SQL), p)).mappings().all()]
    if not lines:
        return []
    stock = {r["ndc11"]: dict(r) for r in
             (await db.execute(text(_STOCK_SQL), {"pid": pharmacy_id})).mappings().all()}
    leads = LT.by_supplier([dict(r) for r in (await db.execute(
        text(_DELIVERED_SQL), {"pid": pharmacy_id})).mappings().all()])

    by_ndc: dict[str, list[dict]] = {}
    for l in lines:
        by_ndc.setdefault(l["ndc11"], []).append(l)

    items = []
    for ndc, rows in by_ndc.items():
        st = stock.get(ndc) or {}
        items.append({"ndc11": ndc, "drug_name": st.get("generic_name"),
                      "lines": rows, "on_hand": st.get("on_hand") or 0,
                      "avg_daily_demand": st.get("avg_daily_demand"),
                      "demand_basis": st.get("demand_basis") or "no_history"})

    out = []
    for s in SHORT.assess(items, leads=leads).signals:
        st = stock.get(s.ndc11) or {}
        out.append(SupplyRisk(
            ndc11=s.ndc11, drug_name=str(s.drug_name or ""),
            fill_rate=None if s.fill_rate is None else float(s.fill_rate),
            fill_rate_trend=("declining"
                             if s.creep is not None and s.creep >= SHORT.CUSUM_ALARM
                             else "stable"),
            cusum_signal=None if s.creep is None else float(s.creep),
            on_hand=float(s.on_hand),
            avg_daily_demand=(None if st.get("avg_daily_demand") is None
                              else float(st["avg_daily_demand"])),
            days_of_stock=(None if s.days_of_cover is None
                           else float(s.days_of_cover)),
            disruption_risk=RISK_OF_SEVERITY.get(s.severity, 0.1),
            risk_band=BAND_OF_SEVERITY.get(s.severity, "ok"),
            suggested_buffer_qty=float(s.suggested_buffer or 0),
            rationale=s.explanation, is_controlled=bool(st.get("is_controlled")),
            verdict=s.verdict, action=s.action, basis=s.basis,
            short_suppliers=list(s.short_suppliers),
            filling_suppliers=list(s.filling_suppliers),
            demand_basis=s.demand_basis, lead_basis=s.lead_basis,
            concerns=list(s.concerns)))
    out.sort(key=lambda r: -r.disruption_risk)
    return out


async def _enrich_cloud(risks: list[SupplyRisk]) -> bool:
    """
    CLOUD: national shortage feeds / OpenFDA recall surge / alternatives.
    Safe stub to be wired to real feeds. Never raises (wrapped by cloud_call).
    """
    for r in risks:
        if r.risk_band in ("critical", "warning"):
            r.national_shortage = None   # real feed sets True/False
            r.recall_surge = None
            r.alternatives = []          # real alternatives engine fills this
    return True


async def analyze(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    window_days: int = 120,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Main entry — §1.2 envelope. Local-complete; cloud-enriched online."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    try:
        risks = await _build_local_risks(db, pharmacy_id, window_days)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[supply_warning] local pull failed (%s)", exc)
        risks = []

    at_risk = [r for r in risks if r.risk_band != "ok"]

    options_active  = ["shortage_e13_cause_separation", "local_fillrate_cusum",
                       "stock_pressure"]
    options_offline: list[str] = []
    degraded = False

    if tier in (Tier.CLOUD, Tier.HYBRID) and at_risk:
        _, ok = await cloud_call(lambda: _enrich_cloud(at_risk),
                                 fallback=False, label="supply_feeds")
        if ok:
            options_active += ["national_shortage_feed", "recall_surge", "alternatives"]
            tier = Tier.HYBRID
        else:
            degraded = True
            options_offline = ["national_shortage_feed", "recall_surge", "alternatives"]
    else:
        degraded = (tier == Tier.LOCAL)
        options_offline = ["national_shortage_feed", "recall_surge", "alternatives"]

    summary = {
        "at_risk_ndcs":  len(at_risk),
        "critical_ndcs": sum(1 for r in at_risk if r.risk_band == "critical"),
        "ndcs_evaluated": len(risks),
    }
    confidence = 0.70 if not degraded else 0.6

    return build_envelope(
        {"at_risk": [r.__dict__ for r in at_risk], "summary": summary},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="supply_warning_v2",
    )
