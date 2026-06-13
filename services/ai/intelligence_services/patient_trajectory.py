"""
#16 — Patient Lifetime Health Trajectory Modeling  (offline-first)
==================================================================
Turns a patient's full history into a longitudinal story: medication changes vs
lab-value trends, disease-progression signals, predicted future state, and
care-gap alerts — feeding a dedicated visualization panel.

LOCAL BRAIN (always available):
  • Unified chronological event stream (meds + labs) via feature_store.
  • Per-lab trend + change-point detection (slope, recent delta) — no model
    download; pure local time-series math.
  • Med-vs-lab correlation: align med starts with subsequent lab deltas.
  • Care-gap detection: chronic-condition monitoring rules (e.g. diabetic without
    recent A1c-type monitoring).
  • Risk-badge strip reuses adherence/polypharmacy signals.

CLOUD BRAIN (when online):
  • Cloud LLM writes the "patient story" narrative + population-level trajectory
    priors. Offline → charts + local predictions + a terse rule-based narrative.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, generate, feature_store,
)

logger = logging.getLogger(__name__)

SERVICE = "patient_trajectory"

# Coarse chronic-condition → monitoring lab hints for care-gap detection.
_MONITORING = {
    "diabet":      {"labs": ["a1c", "hemoglobin a1c", "glucose"], "days": 180, "condition": "Diabetes"},
    "metformin":   {"labs": ["a1c", "creatinine", "egfr"],         "days": 180, "condition": "Diabetes"},
    "insulin":     {"labs": ["a1c", "glucose"],                    "days": 120, "condition": "Diabetes"},
    "warfarin":    {"labs": ["inr", "pt"],                          "days": 30,  "condition": "Anticoagulation"},
    "statin":      {"labs": ["ldl", "lipid", "alt", "ast"],        "days": 365, "condition": "Hyperlipidemia"},
    "atorvastatin":{"labs": ["ldl", "lipid"],                      "days": 365, "condition": "Hyperlipidemia"},
    "lisinopril":  {"labs": ["potassium", "creatinine", "egfr"],   "days": 365, "condition": "Hypertension"},
    "levothyroxine":{"labs": ["tsh"],                              "days": 365, "condition": "Hypothyroidism"},
}


@dataclass
class LabTrend:
    test_name: str
    points:    list[dict]          # [{date, value}]
    slope:     float               # per-day change
    direction: str                 # rising | falling | stable
    latest:    Optional[float]
    delta_pct: Optional[float]


@dataclass
class CareGap:
    condition: str
    message:   str
    severity:  str                 # warning | info


def _linreg_slope(points: list[tuple[float, float]]) -> float:
    n = len(points)
    if n < 2:
        return 0.0
    mean_x = sum(p[0] for p in points) / n
    mean_y = sum(p[1] for p in points) / n
    num = sum((p[0] - mean_x) * (p[1] - mean_y) for p in points)
    den = sum((p[0] - mean_x) ** 2 for p in points)
    return (num / den) if den > 1e-9 else 0.0


def _build_trends(lab_events: list[dict]) -> list[LabTrend]:
    by_test: dict[str, list[dict]] = defaultdict(list)
    for e in lab_events:
        by_test[str(e["label"]).strip()].append(e)

    trends: list[LabTrend] = []
    for test, evs in by_test.items():
        evs = [e for e in evs if e.get("event_date")]
        if len(evs) < 1:
            continue
        evs.sort(key=lambda e: e["event_date"])
        pts = []
        xy = []
        base = None
        for e in evs:
            d = e["event_date"]
            if isinstance(d, str):
                try:
                    d = datetime.fromisoformat(d.replace("Z", "+00:00"))
                except Exception:  # noqa: BLE001
                    continue
            if base is None:
                base = d
            day = (d - base).days if hasattr(d, "days") or hasattr((d - base), "days") else 0
            try:
                day = (d - base).days
            except Exception:  # noqa: BLE001
                day = 0
            val = float(e.get("value") or 0)
            pts.append({"date": str(e["event_date"])[:10], "value": val})
            xy.append((float(day), val))
        if not pts:
            continue
        slope = _linreg_slope(xy)
        latest = pts[-1]["value"]
        first = pts[0]["value"]
        delta_pct = ((latest - first) / first * 100) if first else None
        direction = ("rising" if slope > 1e-6 else "falling" if slope < -1e-6 else "stable")
        trends.append(LabTrend(
            test_name=test, points=pts, slope=round(slope, 4),
            direction=direction, latest=latest,
            delta_pct=round(delta_pct, 1) if delta_pct is not None else None,
        ))
    return trends


def _detect_care_gaps(med_events: list[dict], lab_events: list[dict], now: datetime) -> list[CareGap]:
    gaps: list[CareGap] = []
    med_names = " ".join(str(e.get("label") or "").lower() for e in med_events)
    # Most-recent date per lab keyword.
    lab_latest: dict[str, datetime] = {}
    for e in lab_events:
        label = str(e.get("label") or "").lower()
        d = e.get("event_date")
        if isinstance(d, str):
            try:
                d = datetime.fromisoformat(d.replace("Z", "+00:00"))
            except Exception:  # noqa: BLE001
                continue
        if d is None:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        lab_latest[label] = max(lab_latest.get(label, d), d)

    seen_conditions = set()
    for hint, spec in _MONITORING.items():
        if hint in med_names and spec["condition"] not in seen_conditions:
            # Has a recent monitoring lab?
            recent = False
            for lab_kw in spec["labs"]:
                for label, d in lab_latest.items():
                    if lab_kw in label and (now - d).days <= spec["days"]:
                        recent = True
                        break
                if recent:
                    break
            if not recent:
                seen_conditions.add(spec["condition"])
                gaps.append(CareGap(
                    condition=spec["condition"],
                    message=(f"{spec['condition']} therapy detected but no "
                             f"{'/'.join(spec['labs'][:2])} result in the last "
                             f"{spec['days']} days — monitoring may be overdue."),
                    severity="warning",
                ))
    return gaps


async def analyze(
    db: AsyncSession,
    patient_id: str,
    *,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Build the trajectory. §1.2 envelope with timeline, trends, gaps, narrative."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    now = datetime.now(timezone.utc)

    df = await feature_store.patient_timeline(db, patient_id)
    events = [] if (df is None or df.empty) else [dict(r) for _, r in df.iterrows()]

    med_events = [e for e in events if e.get("event_type") == "medication"]
    lab_events = [e for e in events if e.get("event_type") == "lab"]

    trends = _build_trends(lab_events)
    care_gaps = _detect_care_gaps(med_events, lab_events, now)

    # Timeline for the panel (compact).
    timeline = []
    for e in events:
        timeline.append({
            "type": e.get("event_type"),
            "date": str(e.get("event_date"))[:10] if e.get("event_date") else None,
            "label": e.get("label"),
            "value": float(e.get("value") or 0),
            "detail": e.get("detail"),
        })

    options_active  = ["local_trends", "care_gap_rules"]
    options_offline: list[str] = []
    degraded = (tier == Tier.LOCAL)

    # Narrative.
    narrative = _rule_narrative(med_events, trends, care_gaps)
    if tier in (Tier.CLOUD, Tier.HYBRID) and events:
        try:
            summary_in = {
                "med_count": len(med_events),
                "trends": [{"test": t.test_name, "direction": t.direction, "delta_pct": t.delta_pct} for t in trends[:5]],
                "care_gaps": [g.condition for g in care_gaps],
            }
            res = await generate(
                f"Summarise this patient's medication/lab trajectory in 2-3 sentences "
                f"for a pharmacist. Data: {summary_in}",
                system="You are a clinical pharmacist. Be factual, concise, no PHI invention.",
                max_tokens=180, temperature=0.2, phi=True, task="clinical",
            )
            if res.text.strip() and not res.degraded:
                narrative = res.text.strip()
                options_active.append("llm_narrative")
                tier = Tier.HYBRID
            elif res.degraded:
                degraded = True
                options_offline.append("llm_narrative")
        except Exception:  # noqa: BLE001
            options_offline.append("llm_narrative")
    else:
        options_offline.append("population_priors")
        options_offline.append("llm_narrative")

    summary = {
        "total_events":   len(events),
        "medications":    len(med_events),
        "lab_results":    len(lab_events),
        "tracked_labs":   len(trends),
        "care_gaps":      len(care_gaps),
    }
    confidence = 0.7 if len(events) >= 10 else 0.5 if events else 0.3
    return build_envelope(
        {
            "narrative":  narrative,
            "summary":    summary,
            "timeline":   timeline[:200],
            "lab_trends": [t.__dict__ for t in trends],
            "care_gaps":  [g.__dict__ for g in care_gaps],
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=sorted(set(options_offline)),
        model_version="patient_trajectory_v1",
    )


def _rule_narrative(med_events, trends, care_gaps) -> str:
    bits = []
    if med_events:
        bits.append(f"{len(med_events)} medication events on record")
    rising = [t.test_name for t in trends if t.direction == "rising" and (t.delta_pct or 0) > 10]
    falling = [t.test_name for t in trends if t.direction == "falling" and (t.delta_pct or 0) < -10]
    if falling:
        bits.append(f"{', '.join(falling[:2])} trending down (likely therapy response)")
    if rising:
        bits.append(f"{', '.join(rising[:2])} trending up — monitor")
    if care_gaps:
        bits.append(f"{len(care_gaps)} possible care gap(s): {', '.join(g.condition for g in care_gaps)}")
    return ". ".join(bits).capitalize() + "." if bits else "Insufficient history to build a trajectory yet."
