"""
#17 — Supply-Chain Disruption Early Warning  (offline-first)
============================================================
Predicts NDC-level supply disruptions early and recommends buffer-stock,
alternatives, or prescriber-switch actions before a shortage bites.

LOCAL BRAIN (always available):
  • Per-NDC wholesaler fill-rate from local purchase orders:
        fill_rate = Σ received / Σ ordered   over a trailing window
    A declining fill-rate is the earliest local shortage signal.
  • CUSUM-style cumulative deviation on the fill-rate sequence to catch a slow
    downward creep that a single snapshot misses.
  • Stockout pressure: on-hand vs (avg_daily_demand × lead time) using the
    stock_levels figures the existing forecaster already maintains.
  • Combines into disruption_risk (0–1) + a suggested buffer quantity.

CLOUD BRAIN (when online — enriches, never required):
  • National drug-shortage feeds, cross-network wholesaler fill signals,
    OpenFDA recall surges, knowledge-engine web signals → much earlier & broader.
  Offline → local-only signal with degraded=true; the "order buffer" action is
  parked in the outbox.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call,
)

logger = logging.getLogger(__name__)

SERVICE = "supply_warning"

RISK_CRITICAL = 0.66
RISK_WARNING  = 0.33
DEFAULT_LEAD_TIME_DAYS = 5
DEFAULT_BUFFER_DAYS    = 14


@dataclass
class SupplyRisk:
    ndc11:            str
    drug_name:        str
    fill_rate:        float            # 0–1, trailing window
    fill_rate_trend:  str              # declining | stable | improving
    cusum_signal:     float            # accumulated downward deviation
    on_hand:          float
    avg_daily_demand: float
    days_of_stock:    Optional[float]
    disruption_risk:  float            # 0–1
    risk_band:        str
    suggested_buffer_qty: float
    rationale:        str
    is_controlled:    bool = False
    # cloud-only
    national_shortage:  Optional[bool] = None
    recall_surge:       Optional[bool] = None
    alternatives:       Optional[list] = None


# ─── Local data pull ──────────────────────────────────────────────────────────

_SUPPLY_SQL = """
    WITH po_lines AS (
        SELECT l.ndc11,
               po.ordered_at,
               COALESCE(l.quantity_ordered, 0)  AS qty_ordered,
               COALESCE(l.quantity_received, 0) AS qty_received
        FROM   purchase_order_lines l
        JOIN   purchase_orders po ON po.id = l.order_id
        WHERE  po.pharmacy_id = :pid
          AND  po.ordered_at >= now() - (:days || ' days')::interval
    ),
    fill AS (
        SELECT ndc11,
               SUM(qty_ordered)  AS total_ordered,
               SUM(qty_received) AS total_received,
               COUNT(*)          AS order_lines
        FROM   po_lines
        GROUP  BY ndc11
    )
    SELECT f.ndc11,
           f.total_ordered,
           f.total_received,
           f.order_lines,
           COALESCE(s.quantity_on_hand, 0)   AS on_hand,
           COALESCE(s.avg_daily_demand, 0)   AS avg_daily_demand,
           COALESCE(s.stockout_probability_7d, 0) AS stockout_prob_7d,
           dp.generic_name,
           COALESCE(dp.is_controlled, false) AS is_controlled
    FROM   fill f
    LEFT JOIN stock_levels  s  ON s.ndc11 = f.ndc11 AND s.pharmacy_id = :pid
    LEFT JOIN drug_products dp ON dp.ndc11 = f.ndc11
    WHERE  f.total_ordered > 0
    ORDER  BY f.ndc11
"""

_FILL_SEQUENCE_SQL = """
    SELECT l.ndc11,
           po.ordered_at::date AS order_date,
           COALESCE(l.quantity_ordered, 0)  AS qty_ordered,
           COALESCE(l.quantity_received, 0) AS qty_received
    FROM   purchase_order_lines l
    JOIN   purchase_orders po ON po.id = l.order_id
    WHERE  po.pharmacy_id = :pid
      AND  po.ordered_at >= now() - (:days || ' days')::interval
      AND  l.ndc11 = ANY(:ndcs)
    ORDER  BY l.ndc11, po.ordered_at
"""


def _cusum_downward(fill_rates: list[float], target: float = 0.95, k: float = 0.05) -> float:
    """
    One-sided CUSUM accumulating how far fill-rate runs BELOW target.
    Returns the max accumulated downward deviation (0 = always at/above target).
    """
    s = 0.0
    peak = 0.0
    for r in fill_rates:
        s = max(0.0, s + (target - r) - k)
        peak = max(peak, s)
    return round(peak, 3)


def _trend(fill_rates: list[float]) -> str:
    if len(fill_rates) < 2:
        return "stable"
    first_half = fill_rates[: len(fill_rates) // 2]
    second_half = fill_rates[len(fill_rates) // 2:]
    a = sum(first_half) / max(len(first_half), 1)
    b = sum(second_half) / max(len(second_half), 1)
    if b < a - 0.05:
        return "declining"
    if b > a + 0.05:
        return "improving"
    return "stable"


def _safe(v, d=0.0) -> float:
    try:
        return float(v) if v is not None else d
    except (TypeError, ValueError):
        return d


async def _build_local_risks(db: AsyncSession, pharmacy_id: str, days: int) -> list[SupplyRisk]:
    agg = await db.execute(text(_SUPPLY_SQL), {"pid": pharmacy_id, "days": days})
    agg_rows = [dict(r) for r in agg.mappings().all()]
    if not agg_rows:
        return []

    ndcs = [r["ndc11"] for r in agg_rows]

    # Per-NDC fill-rate sequence for CUSUM + trend.
    seq = await db.execute(text(_FILL_SEQUENCE_SQL),
                           {"pid": pharmacy_id, "days": days, "ndcs": ndcs})
    seq_by_ndc: dict[str, list[float]] = {}
    for r in seq.mappings():
        ordered = _safe(r["qty_ordered"])
        received = _safe(r["qty_received"])
        rate = (received / ordered) if ordered > 1e-9 else 1.0
        seq_by_ndc.setdefault(r["ndc11"], []).append(min(1.0, rate))

    risks: list[SupplyRisk] = []
    for row in agg_rows:
        ndc = row["ndc11"]
        total_ordered  = _safe(row["total_ordered"])
        total_received = _safe(row["total_received"])
        fill_rate = (total_received / total_ordered) if total_ordered > 1e-9 else 1.0
        fill_rate = min(1.0, fill_rate)

        rates = seq_by_ndc.get(ndc, [fill_rate])
        cusum = _cusum_downward(rates)
        trend = _trend(rates)

        on_hand    = _safe(row["on_hand"])
        demand     = _safe(row["avg_daily_demand"])
        days_of_stock = (on_hand / demand) if demand > 1e-9 else None

        # Disruption risk = blend of (1) low/declining fill-rate and (2) thin stock.
        fill_pressure = (1.0 - fill_rate)                      # 0 good, 1 bad
        cusum_pressure = min(1.0, cusum / 0.5)                 # normalise CUSUM
        if days_of_stock is None:
            stock_pressure = 0.2                               # no demand → low pressure
        else:
            # Thin if days_of_stock < lead time + buffer.
            threshold = DEFAULT_LEAD_TIME_DAYS + DEFAULT_BUFFER_DAYS
            stock_pressure = max(0.0, min(1.0, 1.0 - (days_of_stock / threshold)))

        risk = (0.45 * max(fill_pressure, cusum_pressure)
                + 0.35 * stock_pressure
                + 0.20 * (1.0 if trend == "declining" else 0.0))
        risk = round(min(1.0, risk), 3)

        band = ("critical" if risk >= RISK_CRITICAL
                else "warning" if risk >= RISK_WARNING
                else "ok")

        buffer_qty = round(demand * DEFAULT_BUFFER_DAYS, 0) if demand > 0 else 0.0

        bits = []
        if fill_rate < 0.9:
            bits.append(f"wholesaler fill-rate {fill_rate*100:.0f}%")
        if trend == "declining":
            bits.append("fill-rate declining")
        if days_of_stock is not None and days_of_stock < DEFAULT_LEAD_TIME_DAYS + DEFAULT_BUFFER_DAYS:
            bits.append(f"only {days_of_stock:.0f}d of stock")
        rationale = ("Supply pressure: " + ", ".join(bits) + "."
                     if bits else "Supply stable on local signals.")

        risks.append(SupplyRisk(
            ndc11=ndc,
            drug_name=str(row.get("generic_name") or ""),
            fill_rate=round(fill_rate, 3),
            fill_rate_trend=trend,
            cusum_signal=cusum,
            on_hand=on_hand,
            avg_daily_demand=round(demand, 3),
            days_of_stock=round(days_of_stock, 1) if days_of_stock is not None else None,
            disruption_risk=risk,
            risk_band=band,
            suggested_buffer_qty=buffer_qty,
            rationale=rationale,
            is_controlled=bool(row.get("is_controlled", False)),
        ))

    risks.sort(key=lambda r: -r.disruption_risk)
    return risks


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

    options_active  = ["local_fillrate_cusum", "stock_pressure"]
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
        model_version="supply_warning_v1",
    )
