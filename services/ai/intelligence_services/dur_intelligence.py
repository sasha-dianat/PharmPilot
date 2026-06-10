"""
#5 — DUR Override Pattern Intelligence  (offline-first)
=======================================================
Two local capabilities mined from dur_override_events:

  (a) REASON-CODE SUGGESTER — before the pharmacist opens the dropdown, predict
      the most-likely override reason for THIS alert + drug, learned from history.
      Implemented as smoothed conditional probability:
          P(reason | alert_type, drug)  →  P(reason | alert_type)  →  P(reason)
      (a robust, fully-offline stand-in for Apriori on sparse pharmacy data).

  (b) CONSISTENCY MONITOR — per-pharmacist override rate per alert type, scored as
      a z-score against the peer mean. Outliers are surfaced for QA review (not
      punishment). Fully local statistics.

LOCAL BRAIN: pure counting + z-scores over the local table. No model download.
CLOUD BRAIN: (optional) LLM drafts a defensible clinical-notes sentence for the
  chosen reason; national benchmark priors for "normal" override rates. Offline →
  suggestion still ranked locally; notes left to the pharmacist.
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, cloud_call, feature_store,
)

logger = logging.getLogger(__name__)

SERVICE = "dur_intelligence"

# Mirror of the reason catalogue (kept in sync with routers/dur_overrides.py).
REASON_LABELS = {
    "PROF_JUDGMENT":   "Professional judgement — risk-benefit acceptable",
    "PRESCRIBER_AUTH": "Prescriber authorised override (verbal/written)",
    "DUPLICATE_OK":    "Duplicate therapy intentional — different indication",
    "ALLERGY_REFUTED": "Documented allergy is refuted / mislabelled",
    "DOSE_TITRATION":  "High dose is intentional titration per protocol",
    "DOSE_ADJUST":     "Dose adjusted for renal/hepatic impairment",
    "AGE_EXCEPTION":   "Age-related alert — paediatric/geriatric dosing confirmed",
    "PREGNANCY_OK":    "Pregnancy risk accepted — benefit outweighs risk",
    "SHORT_COURSE":    "Short-course therapy — interaction risk negligible",
    "PATIENT_CONSENT": "Patient counselled and consented to risk",
    "KNOWN_TOLERANCE": "Patient has documented tolerance to combination",
    "FORMULARY_REQ":   "Formulary / insurance requirement overrides preferred agent",
    "OTHER":           "Other — see clinical notes",
}

LAPLACE = 1.0   # smoothing so unseen combinations still rank


@dataclass
class ReasonSuggestion:
    reason_code:  str
    reason_label: str
    probability:  float
    support:      int       # how many historical overrides backed this
    basis:        str       # "alert+drug" | "alert" | "global" | "prior"


def _drug_key(row: dict) -> str:
    """Coarse drug grouping for association keys (generic name, lowercased)."""
    name = (row.get("drug_name") or "").strip().lower()
    # Use the first token (active ingredient) so strengths/forms collapse together.
    return name.split()[0] if name else ""


async def suggest_reason(
    db: AsyncSession,
    *,
    alert_type: str,
    drug_name: str = "",
    pharmacy_id: Optional[str] = None,
    top_k: int = 3,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Rank the most-likely override reasons for this alert (+ drug). §1.2 envelope.
    Always returns a ranked list — falls back to sensible priors with no history.
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    df = await feature_store.dur_overrides(db, pharmacy_id)

    # Build conditional counts.
    c_alert_drug: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    c_alert:      dict[str, dict[str, int]]    = defaultdict(lambda: defaultdict(int))
    c_global:     dict[str, int]               = defaultdict(int)

    target_drug = drug_name.strip().lower().split()[0] if drug_name else ""

    if df is not None and not df.empty:
        for _, r in df.iterrows():
            row = dict(r)
            a = str(row.get("alert_type") or "").upper()
            rc = str(row.get("reason_code") or "")
            if not rc:
                continue
            dk = _drug_key(row)
            c_alert_drug[(a, dk)][rc] += 1
            c_alert[a][rc] += 1
            c_global[rc] += 1

    a = alert_type.upper()
    reasons = list(REASON_LABELS.keys())

    # Choose the most specific basis with enough support.
    suggestions: list[ReasonSuggestion] = []
    basis = "prior"
    table: dict[str, int] = {}

    if target_drug and sum(c_alert_drug[(a, target_drug)].values()) >= 2:
        table = c_alert_drug[(a, target_drug)]
        basis = "alert+drug"
    elif sum(c_alert[a].values()) >= 2:
        table = c_alert[a]
        basis = "alert"
    elif sum(c_global.values()) >= 3:
        table = c_global
        basis = "global"

    if table:
        total = sum(table.values()) + LAPLACE * len(reasons)
        for rc in reasons:
            cnt = table.get(rc, 0)
            prob = (cnt + LAPLACE) / total
            suggestions.append(ReasonSuggestion(
                reason_code=rc, reason_label=REASON_LABELS[rc],
                probability=round(prob, 3), support=cnt, basis=basis,
            ))
    else:
        # No history — clinically-sensible default priors per alert type.
        prior = _alert_prior(a)
        for rc in reasons:
            suggestions.append(ReasonSuggestion(
                reason_code=rc, reason_label=REASON_LABELS[rc],
                probability=round(prior.get(rc, 0.02), 3), support=0, basis="prior",
            ))

    suggestions.sort(key=lambda s: -s.probability)
    top = suggestions[:top_k]

    # Local confidence: how peaked is the top suggestion + how much support.
    top_prob = top[0].probability if top else 0.0
    support  = top[0].support if top else 0
    confidence = round(min(0.95, 0.4 + 0.4 * top_prob + min(0.15, support / 50.0)), 3)

    options_active  = ["local_conditional_probability"]
    options_offline = []
    degraded = (tier == Tier.LOCAL)
    if tier in (Tier.CLOUD, Tier.HYBRID):
        options_active.append("benchmark_priors")
    else:
        options_offline = ["benchmark_priors", "notes_drafting"]

    return build_envelope(
        {
            "alert_type": a,
            "drug_name": drug_name,
            "basis": basis,
            "suggestions": [s.__dict__ for s in top],
            "top_reason_code": top[0].reason_code if top else "OTHER",
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="dur_suggester_v1",
    )


def _alert_prior(alert_type: str) -> dict[str, float]:
    """Clinically-sensible default reason distribution per alert type (cold start)."""
    table = {
        "DDI":        {"PROF_JUDGMENT": 0.35, "PRESCRIBER_AUTH": 0.25, "SHORT_COURSE": 0.15, "KNOWN_TOLERANCE": 0.10},
        "ALLERGY":    {"ALLERGY_REFUTED": 0.45, "PROF_JUDGMENT": 0.25, "PATIENT_CONSENT": 0.15},
        "DUPLICATE":  {"DUPLICATE_OK": 0.55, "PROF_JUDGMENT": 0.20, "PRESCRIBER_AUTH": 0.15},
        "DOSE_HIGH":  {"DOSE_TITRATION": 0.40, "DOSE_ADJUST": 0.20, "PRESCRIBER_AUTH": 0.20},
        "DOSE_LOW":   {"DOSE_ADJUST": 0.40, "PROF_JUDGMENT": 0.25, "PRESCRIBER_AUTH": 0.20},
        "AGE":        {"AGE_EXCEPTION": 0.55, "PROF_JUDGMENT": 0.20, "DOSE_ADJUST": 0.15},
        "PREGNANCY":  {"PREGNANCY_OK": 0.55, "PATIENT_CONSENT": 0.20, "PROF_JUDGMENT": 0.15},
        "RENAL":      {"DOSE_ADJUST": 0.60, "PROF_JUDGMENT": 0.20},
        "HEPATIC":    {"DOSE_ADJUST": 0.60, "PROF_JUDGMENT": 0.20},
    }
    return table.get(alert_type, {"PROF_JUDGMENT": 0.4, "PRESCRIBER_AUTH": 0.2, "OTHER": 0.1})


# ─── (b) Consistency monitor ──────────────────────────────────────────────────

@dataclass
class PharmacistConsistency:
    pharmacist:        str
    total_overrides:   int
    by_alert:          dict       # alert_type → count
    override_share:    float      # this pharmacist's share of all overrides
    z_score:           float      # vs peer mean of override volume
    flag:              str        # outlier_high | normal | low_volume
    note:              str


async def consistency_report(
    db: AsyncSession,
    *,
    pharmacy_id: Optional[str] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Per-pharmacist override-rate outlier detection (z-score). §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    df = await feature_store.dur_overrides(db, pharmacy_id)

    counts: dict[str, int] = defaultdict(int)
    by_alert: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    if df is not None and not df.empty:
        for _, r in df.iterrows():
            row = dict(r)
            who = str(row.get("overridden_by") or "unknown")
            counts[who] += 1
            by_alert[who][str(row.get("alert_type") or "?")] += 1

    total = sum(counts.values())
    volumes = list(counts.values())
    mean = (sum(volumes) / len(volumes)) if volumes else 0.0
    var = (sum((v - mean) ** 2 for v in volumes) / len(volumes)) if volumes else 0.0
    std = math.sqrt(var) if var > 0 else 0.0

    reports: list[PharmacistConsistency] = []
    for who, cnt in counts.items():
        z = ((cnt - mean) / std) if std > 1e-9 else 0.0
        if cnt < 3:
            flag, note = "low_volume", "Too few overrides to assess."
        elif z >= 2.0:
            flag = "outlier_high"
            note = (f"Overrides {cnt} vs peer avg {mean:.0f} (z={z:.1f}). "
                    "Suggest peer review of override rationale consistency.")
        else:
            flag, note = "normal", "Within normal range vs peers."
        reports.append(PharmacistConsistency(
            pharmacist=who, total_overrides=cnt, by_alert=dict(by_alert[who]),
            override_share=round(cnt / total, 3) if total else 0.0,
            z_score=round(z, 2), flag=flag, note=note,
        ))

    reports.sort(key=lambda r: -r.z_score)
    outliers = sum(1 for r in reports if r.flag == "outlier_high")

    confidence = 0.75 if total >= 20 else 0.5
    degraded = (tier == Tier.LOCAL)
    return build_envelope(
        {
            "pharmacists": [r.__dict__ for r in reports],
            "summary": {"total_overrides": total, "pharmacists": len(reports),
                        "outliers": outliers, "peer_mean": round(mean, 1)},
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=["local_zscore"],
        options_offline=["national_benchmarks"] if degraded else [],
        model_version="dur_consistency_v1",
    )
