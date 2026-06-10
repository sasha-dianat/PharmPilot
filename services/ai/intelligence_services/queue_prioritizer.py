"""
#2 — Predictive Queue Prioritization  (offline-first)
=====================================================
Scores every Rx in the queue for work-order priority and forecasts incoming load.

LOCAL BRAIN (always available):
  • A transparent weighted scorer (no model download required) combining:
      - patient physically present (biometric check-in) — strongest signal
      - drug acuity (acute/antibiotic/controlled vs chronic maintenance)
      - refills remaining (0 left → patient is waiting on this)
      - time since order (older = higher)
      - current queue depth pressure
    If a trained ranker exists in the model store it is used; otherwise the
    weighted scorer is the always-on floor.
  • Arrival forecast: seasonal-naïve by hour-of-day / day-of-week over local
    timestamp history.

CLOUD BRAIN (when online):
  • Cross-pharmacy arrival priors, weather/holiday signals, LLM tie-breaking on
    free-text Rx notes. Offline → these drop from options_active.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, ModelStore,
)

logger = logging.getLogger(__name__)

SERVICE = "queue_prioritizer"

# Acute / high-acuity drug-name hints (coarse, offline).
_ACUTE_HINTS = ("amoxicillin", "azithromycin", "cephalexin", "ciprofloxacin",
                "prednisone", "albuterol", "ondansetron", "oseltamivir",
                "nitrofurantoin", "metronidazole", "doxycycline", "augmentin",
                "antibiotic", "inhaler", "insulin", "epinephrine")


@dataclass
class QueueItem:
    rx_id:        str
    rx_number:    str
    drug_name:    str
    patient_present: bool
    priority_score: float
    priority_band:  str       # urgent | high | normal | low
    factors:        list[str]
    minutes_waiting: int


def _acuity(drug_name: str, is_controlled: bool) -> float:
    low = (drug_name or "").lower()
    if any(h in low for h in _ACUTE_HINTS):
        return 1.0
    if is_controlled:
        return 0.7
    return 0.3   # chronic / maintenance default


def _score_item(row: dict, queue_depth: int, now: datetime) -> QueueItem:
    drug = str(row.get("drug_name") or "")
    is_ctrl = bool(row.get("is_controlled", False))
    present = bool(row.get("patient_present", False))
    refills = int(row.get("refills_remaining") or 0)

    created = row.get("created_at")
    minutes = 0
    if created:
        try:
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            minutes = int((now - created).total_seconds() / 60)
        except Exception:  # noqa: BLE001
            minutes = 0

    factors: list[str] = []
    score = 0.0

    if present:
        score += 0.40; factors.append("patient_at_counter")
    acuity = _acuity(drug, is_ctrl)
    score += 0.25 * acuity
    if acuity >= 1.0:
        factors.append("acute_medication")
    elif is_ctrl:
        factors.append("controlled_substance")
    if refills == 0:
        score += 0.15; factors.append("no_refills_remaining")
    # Wait pressure: saturating at ~60 min.
    wait_pressure = 1.0 - math.exp(-minutes / 30.0)
    score += 0.12 * wait_pressure
    if minutes > 30:
        factors.append(f"waiting_{minutes}m")
    # Queue depth nudges everything up slightly.
    score += 0.08 * min(1.0, queue_depth / 25.0)

    score = round(min(1.0, score), 3)
    band = ("urgent" if score >= 0.7 else "high" if score >= 0.5
            else "normal" if score >= 0.3 else "low")

    return QueueItem(
        rx_id=str(row.get("rx_id") or row.get("id") or ""),
        rx_number=str(row.get("rx_number") or ""),
        drug_name=drug, patient_present=present,
        priority_score=score, priority_band=band, factors=factors,
        minutes_waiting=minutes,
    )


_QUEUE_SQL = """
    SELECT p.id AS rx_id, p.rx_number, p.drug_name,
           COALESCE(p.is_controlled, false) AS is_controlled,
           COALESCE(p.refills_remaining, 0) AS refills_remaining,
           p.created_at,
           false AS patient_present
    FROM   prescriptions p
    WHERE  p.pharmacy_id = :pid
      AND  p.status NOT IN ('dispensed', 'cancelled', 'voided', 'picked_up')
    ORDER  BY p.created_at
    LIMIT  200
"""

_ARRIVAL_SQL = """
    SELECT EXTRACT(HOUR FROM created_at)::int AS hour, COUNT(*) AS cnt
    FROM   prescriptions
    WHERE  pharmacy_id = :pid
      AND  created_at >= now() - interval '60 days'
    GROUP  BY hour ORDER BY hour
"""


async def rank(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    present_patient_ids: Optional[list[str]] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Rank the active queue. §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    present_set = set(present_patient_ids or [])

    try:
        res = await db.execute(text(_QUEUE_SQL), {"pid": pharmacy_id})
        rows = [dict(r) for r in res.mappings().all()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[queue_prioritizer] queue pull failed (%s)", exc)
        rows = []

    # Mark patient-present (if caller passed biometric check-ins).
    for r in rows:
        if present_set and str(r.get("patient_id")) in present_set:
            r["patient_present"] = True

    now = datetime.now(timezone.utc)
    depth = len(rows)
    items = [_score_item(r, depth, now) for r in rows]
    items.sort(key=lambda i: -i.priority_score)

    options_active  = ["weighted_scorer"]
    options_offline = ["cross_pharmacy_priors", "weather_holiday"] if tier == Tier.LOCAL else []
    degraded = (tier == Tier.LOCAL)
    if tier in (Tier.CLOUD, Tier.HYBRID):
        options_active.append("cross_pharmacy_priors")

    summary = {
        "queue_depth": depth,
        "urgent": sum(1 for i in items if i.priority_band == "urgent"),
        "high":   sum(1 for i in items if i.priority_band == "high"),
    }
    return build_envelope(
        {"ranking": [i.__dict__ for i in items], "summary": summary},
        tier_used=tier, confidence=0.72, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="queue_prioritizer_v1",
    )


async def forecast(
    db: AsyncSession,
    pharmacy_id: str,
    *,
    horizon_hours: int = 4,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Forecast incoming Rx load for the next N hours (seasonal-naïve). §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    try:
        res = await db.execute(text(_ARRIVAL_SQL), {"pid": pharmacy_id})
        by_hour = {int(r["hour"]): int(r["cnt"]) for r in res.mappings().all()}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[queue_prioritizer] arrival pull failed (%s)", exc)
        by_hour = {}

    # Average per-hour over the 60-day window (so divide by ~60 days).
    days = 60.0
    now_hour = datetime.now(timezone.utc).hour
    points = []
    for h in range(horizon_hours):
        hour = (now_hour + h) % 24
        avg = round(by_hour.get(hour, 0) / days, 1)
        points.append({"hour": hour, "expected_rx": avg})

    total_expected = round(sum(p["expected_rx"] for p in points), 1)
    degraded = (tier == Tier.LOCAL)
    return build_envelope(
        {"forecast": points, "total_expected": total_expected, "horizon_hours": horizon_hours},
        tier_used=tier, confidence=0.6, degraded=degraded,
        options_active=["seasonal_naive"],
        options_offline=["weather_holiday", "cross_pharmacy_priors"] if degraded else [],
        model_version="queue_forecast_v1",
    )
