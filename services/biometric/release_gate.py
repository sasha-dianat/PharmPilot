"""Per-cell release gating. No averaging, ever.

A mean hides the failure that matters: two excellent demographic cells will
carry a third that is unusable, and the headline number looks fine while the
system fails the people in that third cell.

THE SUBTLE PART, and the reason this gate covers three channels rather than one:
differential performance usually enters through the DETECTOR and the QUALITY
GATE, not the matcher. If detection recall is lower on chador — dark garment,
low contrast, tight face aperture — those customers never reach the matcher.
Per-cell matcher metrics then look perfect while the system discriminates. So
the headline is end-to-end, captured -> identified, and detection is gated on
its own as well.

A cell with too little data FAILS. Treating missing evidence as a pass is how a
group with no test data gets declared safe.

The cells themselves come from a consented evaluation dataset. Nothing here
infers a demographic attribute from a face — that is the narrow, bounded
carve-out to invariant I-9 recorded in SURVEILLANCE_PLATFORM.md: demographics
are recorded for the release gate and never joined to the operational gallery.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CellMetrics:
    """One demographic or presentation cell, measured end to end."""

    cell: str
    captured: int          # encounters where a person was present
    detected: int          # of those, where a face was found
    quality_passed: int    # of those, clearing the quality gate
    identified: int        # of the ORIGINAL captures, committed to an identity

    @property
    def detection_rate(self) -> float:
        return (self.detected / self.captured) if self.captured else 0.0

    @property
    def quality_rate(self) -> float:
        return (self.quality_passed / self.detected) if self.detected else 0.0

    @property
    def identification_rate(self) -> float:
        """Captured -> identified. The headline, and deliberately NOT
        matcher-only: a matcher that never sees a face cannot fail on it."""
        return (self.identified / self.captured) if self.captured else 0.0

    def as_dict(self) -> dict:
        return {"cell": self.cell, "captured": self.captured,
                "detected": self.detected, "quality_passed": self.quality_passed,
                "identified": self.identified,
                "detection_rate": round(self.detection_rate, 4),
                "quality_rate": round(self.quality_rate, 4),
                "identification_rate": round(self.identification_rate, 4)}


@dataclass
class GateResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    worst_cell: str | None = None
    ratio: float = 0.0
    report: list[dict] = field(default_factory=list)


def evaluate_gate(cells: list[CellMetrics], *,
                  min_identification_rate: float = 0.85,
                  max_ratio: float = 2.0,
                  min_detection_rate: float = 0.90,
                  min_cell_size: int = 30) -> GateResult:
    """Pass only if EVERY cell clears every floor. Nothing is averaged."""
    report = [c.as_dict() for c in cells]

    if not cells:
        return GateResult(False, ["no cells measured — a gate with no evidence "
                                  "cannot pass"], None, 0.0, report)

    failures: list[str] = []

    for c in cells:
        if c.captured < min_cell_size:
            failures.append(
                f"{c.cell}: too few samples ({c.captured} < {min_cell_size}) to "
                f"judge — missing evidence is not a pass")
            continue
        if c.detection_rate < min_detection_rate:
            failures.append(
                f"{c.cell}: detection rate {c.detection_rate:.3f} below "
                f"{min_detection_rate} — these captures never reach the "
                f"matcher, so matcher metrics cannot exonerate it")
        if c.identification_rate < min_identification_rate:
            failures.append(
                f"{c.cell}: end-to-end identification rate "
                f"{c.identification_rate:.3f} below {min_identification_rate}")

    judged = [c for c in cells if c.captured >= min_cell_size]
    ratio = 0.0
    worst = None
    if judged:
        rates = {c.cell: c.identification_rate for c in judged}
        worst = min(rates, key=lambda k: rates[k])
        best_rate = max(rates.values())
        worst_rate = rates[worst]
        ratio = (best_rate / worst_rate) if worst_rate > 0 else float("inf")
        if ratio > max_ratio:
            failures.append(
                f"spread between best and worst cell is {ratio:.2f}x "
                f"(limit {max_ratio}) — worst is {worst}")

    return GateResult(passed=not failures, failures=failures,
                      worst_cell=worst, ratio=ratio, report=report)
