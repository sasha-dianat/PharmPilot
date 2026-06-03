"""
Review-by-Exception Triage Engine.
==================================
Classifies each prescription into a review lane so the pharmacist's cognitive
effort is proportional to risk — WITHOUT ever weakening a safety gate.

    GREEN  → fast lane: every decision pre-filled; collapses to ONE confirm.
             Final visual product verification is STILL mandatory and logged.
    AMBER  → standard review surface (known issues to consider).
    RED    → full scrutiny; never auto-advanced.

NON-NEGOTIABLE HARD GATES (can never be GREEN, encoded as immovable rules):
  - controlled substance (any DEA schedule)
  - first-fill of a high-alert medication
  - any unresolved hard-stop / blocker DUR alert
  - any council BLOCKER finding (incl. hereditary blockers like G6PD+oxidant)
  - PDMP high/critical risk

Pure, deterministic, dependency-free → fully unit-testable. The classifier
EXPLAINS itself (reasons + which gates fired) for audit and pharmacist trust.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ReviewLane(str, Enum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"


# High-alert medications (ISMP-derived) — first fills always escape the fast lane.
HIGH_ALERT_MEDS = {
    "warfarin", "insulin", "heparin", "enoxaparin", "methotrexate", "digoxin",
    "lithium", "phenytoin", "carbamazepine", "clozapine", "amiodarone",
    "fentanyl", "morphine", "oxycodone", "hydromorphone", "methadone",
    "chemotherapy", "vincristine", "cyclophosphamide", "tacrolimus", "cyclosporine",
}


@dataclass
class TriageInput:
    drug_name: str
    is_controlled: bool = False
    dea_schedule: Optional[str] = None
    is_first_fill: bool = True
    is_refill: bool = False
    is_stable_chronic: bool = False        # refill of an unchanged chronic med
    known_patient: bool = True             # identity resolved with high confidence
    dose_in_range: bool = True
    # signals from the rest of the pipeline
    dur_alerts: list[dict] = field(default_factory=list)        # [{severity, is_hard_stop, was_overridden}]
    council_findings: list[dict] = field(default_factory=list)  # [{severity}]
    pdmp_risk: Optional[str] = None        # low | moderate | high | critical | None
    claim_status: Optional[str] = None     # approved | paid | cash_pay | rejected | None


@dataclass
class TriageResult:
    lane: ReviewLane
    reasons: list[str]
    hard_gates: list[str]                  # gates that forced escalation (audit)
    requires_visual_verification: bool = True   # ALWAYS true — never removed
    one_tap_confirm: bool = False          # only green
    suggested_priority: float = 0.0

    @property
    def is_green(self) -> bool:
        return self.lane == ReviewLane.GREEN


class ReviewTriageEngine:

    def classify(self, t: TriageInput) -> TriageResult:
        hard_gates: list[str] = []
        reasons: list[str] = []

        drug = (t.drug_name or "").lower()

        # ── HARD GATES (force escalation; can never be green) ─────────────────
        if t.is_controlled or t.dea_schedule:
            hard_gates.append("controlled_substance")
        if t.is_first_fill and any(h in drug for h in HIGH_ALERT_MEDS):
            hard_gates.append("first_fill_high_alert")

        unresolved_hardstop = any(
            a.get("is_hard_stop") and not a.get("was_overridden") for a in t.dur_alerts
        )
        if unresolved_hardstop:
            hard_gates.append("unresolved_hard_stop_dur")

        critical_dur = any(a.get("severity") == "critical" and not a.get("was_overridden")
                           for a in t.dur_alerts)
        if critical_dur:
            hard_gates.append("critical_dur_alert")

        council_blocker = any(f.get("severity") == "blocker" for f in t.council_findings)
        if council_blocker:
            hard_gates.append("council_blocker")

        if t.pdmp_risk in ("high", "critical"):
            hard_gates.append(f"pdmp_{t.pdmp_risk}_risk")

        # ── RED: any hard gate ⇒ full scrutiny ────────────────────────────────
        if hard_gates:
            reasons.append("Safety gate(s) require full review: " + ", ".join(hard_gates))
            return TriageResult(
                lane=ReviewLane.RED,
                reasons=reasons,
                hard_gates=hard_gates,
                one_tap_confirm=False,
                suggested_priority=self._priority(t, base=0.9),
            )

        # ── Collect amber-level signals ───────────────────────────────────────
        amber = []
        if any(a.get("severity") in ("high", "moderate") and not a.get("was_overridden")
               for a in t.dur_alerts):
            amber.append("dur_review")
        if any(f.get("severity") in ("caution", "clarification") for f in t.council_findings):
            amber.append("council_caution")
        if not t.known_patient:
            amber.append("identity_unconfirmed")
        if not t.dose_in_range:
            amber.append("dose_out_of_range")
        if t.claim_status == "rejected":
            amber.append("claim_rejected")
        if t.is_first_fill and not t.is_stable_chronic:
            amber.append("first_fill")

        if amber:
            reasons.append("Items to review: " + ", ".join(amber))
            return TriageResult(
                lane=ReviewLane.AMBER,
                reasons=reasons,
                hard_gates=[],
                one_tap_confirm=False,
                suggested_priority=self._priority(t, base=0.5),
            )

        # ── GREEN: all positive conditions met, zero hard gates, zero amber ───
        green_ok = (
            t.known_patient
            and t.is_refill
            and t.is_stable_chronic
            and t.dose_in_range
            and t.claim_status in ("approved", "paid", "cash_pay")
            and not t.is_controlled
        )
        if green_ok:
            reasons.append("Stable chronic refill, known patient, clean checks, claim settled.")
            return TriageResult(
                lane=ReviewLane.GREEN,
                reasons=reasons,
                hard_gates=[],
                one_tap_confirm=True,
                requires_visual_verification=True,   # still mandatory
                suggested_priority=self._priority(t, base=0.2),
            )

        # Default: not enough to be green, nothing amber-worthy flagged → amber.
        reasons.append("Insufficient positive signals for fast-lane; standard review.")
        return TriageResult(
            lane=ReviewLane.AMBER,
            reasons=reasons,
            hard_gates=[],
            suggested_priority=self._priority(t, base=0.5),
        )

    def _priority(self, t: TriageInput, base: float) -> float:
        """Risk-weighted queue priority (higher = serve sooner)."""
        score = base
        if t.pdmp_risk in ("high", "critical"):
            score += 0.2
        if any(a.get("severity") == "critical" for a in t.dur_alerts):
            score += 0.15
        return round(min(score, 1.0), 3)


def queue_order(items: list[tuple[TriageInput, float]]) -> list[int]:
    """
    Given [(triage_input, wait_minutes)], return indices ordered by
    risk-weighted priority (clinical risk + wait). Patient-present weighting can
    be layered by the caller via the wait/contextual term.
    """
    engine = ReviewTriageEngine()
    scored = []
    for idx, (t, wait_min) in enumerate(items):
        res = engine.classify(t)
        # combine clinical priority with normalized wait pressure
        wait_term = min(wait_min / 30.0, 1.0) * 0.3
        scored.append((idx, res.suggested_priority + wait_term))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [idx for idx, _ in scored]
