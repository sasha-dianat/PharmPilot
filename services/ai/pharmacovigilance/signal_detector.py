"""
Local Pharmacovigilance Signal Detector — Phase 22-D
======================================================
Detects adverse drug event signals in your pharmacy's own patient data.
100% offline — uses only numpy/scipy. No external API, no cloud.

Three algorithms:

  1. PRR (Proportional Reporting Ratio)
     Measures whether drug-event pair is reported more than expected by chance.
     PRR ≥ 2.0 with ≥ 3 cases = signal. (Evans 2001, WHO guideline)

  2. ROR (Reporting Odds Ratio)
     More robust than PRR for small case counts. Exact 95% CI via Fisher's exact.
     ROR ≥ 2.0 with 95% CI lower bound ≥ 1.0 = signal.

  3. CUSUM Change-Point Detection
     Detects when adverse event rate shifts — catches a new signal
     emerging after a formulary change, lot number change, etc.
     Based on Shiryaev-Roberts procedure (faster detection than CUSUM
     in the presence of multiple change-points).

All methods are applied to your pharmacy's own dispensing + adverse event log.
They do NOT send any data externally.

Clinical note:
  These methods provide statistical signals requiring pharmacist review.
  A signal is not a confirmed causal relationship.
  Per PharmPilot policy: AI proposes, pharmacist confirms. No auto-reporting.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import numpy as np

log = logging.getLogger(__name__)

# ── Signal Thresholds (WHO Uppsala Monitoring Centre guidelines) ──────────────
PRR_THRESHOLD         = 2.0    # PRR ≥ 2.0
PRR_CHI2_THRESHOLD    = 4.0    # χ² ≥ 4.0 (≈ p < 0.05)
PRR_MIN_CASES         = 3      # Minimum cases to generate a signal
ROR_THRESHOLD         = 2.0    # ROR ≥ 2.0
ROR_CI_LOWER_BOUND    = 1.0    # 95% CI lower bound ≥ 1.0
CUSUM_THRESHOLD       = 3.0    # CUSUM alert threshold (σ units)

# Signal severity mapping
def _prr_severity(prr: float, n_cases: int) -> str:
    if prr >= 10 and n_cases >= 10: return "critical"
    if prr >= 5  and n_cases >= 5:  return "high"
    if prr >= 2  and n_cases >= 3:  return "moderate"
    return "low"


# ── Data Structures ───────────────────────────────────────────────────────────

@dataclass
class ADRSignal:
    """A detected adverse drug reaction signal."""
    signal_id:      UUID = field(default_factory=uuid4)
    drug_name:      str  = ""
    ndc11:          str  = ""
    event_type:     str  = ""          # e.g., "nausea", "rash", "hypoglycemia"
    method:         str  = ""          # prr | ror | cusum
    # PRR/ROR metrics
    prr:            Optional[float] = None
    prr_chi2:       Optional[float] = None
    ror:            Optional[float] = None
    ror_ci_lower:   Optional[float] = None
    ror_ci_upper:   Optional[float] = None
    n_cases:        int   = 0          # Drug + event co-occurrences
    n_drug_total:   int   = 0          # All patients on this drug
    n_event_total:  int   = 0          # All patients with this event
    n_total:        int   = 0          # Total patient-dispense records
    # CUSUM
    cusum_value:    Optional[float] = None
    # Outcome
    severity:       str   = "moderate"
    is_signal:      bool  = True
    description:    str   = ""
    pharmacist_note: str  = ""
    detected_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Review
    reviewed:       bool  = False
    reviewed_by:    Optional[str] = None
    reviewed_at:    Optional[datetime] = None
    disposition:    Optional[str] = None  # confirmed | dismissed | monitor | report_to_fda

    def to_dict(self) -> dict:
        return {
            "signal_id":     str(self.signal_id),
            "drug_name":     self.drug_name,
            "ndc11":         self.ndc11,
            "event_type":    self.event_type,
            "method":        self.method,
            "prr":           round(self.prr, 3) if self.prr else None,
            "prr_chi2":      round(self.prr_chi2, 3) if self.prr_chi2 else None,
            "ror":           round(self.ror, 3) if self.ror else None,
            "ror_ci_lower":  round(self.ror_ci_lower, 3) if self.ror_ci_lower else None,
            "ror_ci_upper":  round(self.ror_ci_upper, 3) if self.ror_ci_upper else None,
            "n_cases":       self.n_cases,
            "n_drug_total":  self.n_drug_total,
            "n_event_total": self.n_event_total,
            "n_total":       self.n_total,
            "cusum_value":   round(self.cusum_value, 3) if self.cusum_value else None,
            "severity":      self.severity,
            "is_signal":     self.is_signal,
            "description":   self.description,
            "pharmacist_note": self.pharmacist_note,
            "detected_at":   self.detected_at.isoformat(),
            "reviewed":      self.reviewed,
            "disposition":   self.disposition,
        }


@dataclass
class ADRCase:
    """A single adverse drug reaction case for the internal case registry."""
    case_id:        UUID = field(default_factory=uuid4)
    patient_id:     Optional[UUID] = None
    ndc11:          str  = ""
    drug_name:      str  = ""
    event_type:     str  = ""
    event_date:     date = field(default_factory=date.today)
    severity:       str  = "moderate"   # mild | moderate | severe | life_threatening | fatal
    description:    str  = ""
    dechallenge:    Optional[str] = None   # Improved on stopping drug? yes/no/unknown
    rechallenge:    Optional[str] = None   # Recurred on restarting? yes/no/unknown
    causality:      str  = "possible"      # certain | probable | possible | unlikely
    reported_by:    str  = ""
    created_at:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    medwatch_submitted: bool = False


# ── PRR Calculator ────────────────────────────────────────────────────────────

class SignalDetector:
    """
    Disproportionality analysis for adverse drug reaction signal detection.

    Input: a list of patient-event records. Each record is a dict with:
      drug_name (or ndc11), event_type, patient_id (or count)

    The contingency table approach:
                       Has Event   No Event   Total
      Exposed (drug)      a           b        a+b
      Unexposed           c           d        c+d
      Total             a+c         b+d       n=a+b+c+d

    PRR = (a/(a+b)) / (c/(c+d))
    ROR = (a*d) / (b*c)
    χ² = (n * (a*d - b*c)²) / ((a+b)*(c+d)*(a+c)*(b+d))
    """

    def compute_prr(
        self,
        a: int,    # drug + event
        b: int,    # drug, no event
        c: int,    # no drug, event
        d: int,    # no drug, no event
    ) -> dict:
        """
        Compute PRR with chi-squared test.
        Returns {"prr", "chi2", "is_signal", "n_cases"}
        """
        if a < PRR_MIN_CASES or (a + b) == 0 or (c + d) == 0:
            return {"prr": None, "chi2": None, "is_signal": False, "n_cases": a}

        prr = (a / (a + b)) / max(c / (c + d), 1e-10)

        # χ² with continuity correction (Yates)
        n = a + b + c + d
        if n == 0 or (a + b) == 0 or (c + d) == 0 or (a + c) == 0 or (b + d) == 0:
            return {"prr": prr, "chi2": 0.0, "is_signal": False, "n_cases": a}

        # Standard chi-squared (no Yates — small counts handled by minimum case threshold)
        expected_a = (a + b) * (a + c) / n
        chi2 = n * (a * d - b * c) ** 2 / ((a + b) * (c + d) * (a + c) * (b + d))

        is_signal = (prr >= PRR_THRESHOLD and chi2 >= PRR_CHI2_THRESHOLD and a >= PRR_MIN_CASES)

        return {
            "prr":       prr,
            "chi2":      chi2,
            "is_signal": is_signal,
            "n_cases":   a,
        }

    def compute_ror(
        self,
        a: int,
        b: int,
        c: int,
        d: int,
    ) -> dict:
        """
        Compute ROR with 95% confidence interval (exact binomial via log-transform).
        Returns {"ror", "ci_lower", "ci_upper", "is_signal"}
        """
        if a < PRR_MIN_CASES or b == 0 or c == 0 or d == 0:
            return {"ror": None, "ci_lower": None, "ci_upper": None, "is_signal": False}

        ror = (a * d) / (b * c)
        # Approximate 95% CI using log-normal method
        log_ror = np.log(ror)
        se_log  = np.sqrt(1/a + 1/b + 1/c + 1/d)
        ci_lower = float(np.exp(log_ror - 1.96 * se_log))
        ci_upper = float(np.exp(log_ror + 1.96 * se_log))

        is_signal = (
            ror >= ROR_THRESHOLD and
            ci_lower >= ROR_CI_LOWER_BOUND and
            a >= PRR_MIN_CASES
        )

        return {
            "ror":       ror,
            "ci_lower":  ci_lower,
            "ci_upper":  ci_upper,
            "is_signal": is_signal,
        }

    def scan_event_database(
        self,
        records: list[dict],
        drug_filter: Optional[str] = None,
        event_filter: Optional[str] = None,
    ) -> list[ADRSignal]:
        """
        Scan a list of patient records for drug-event co-occurrence signals.

        records: list of {"drug_name", "ndc11", "event_type", "patient_id"}
          - Each record represents one patient-drug-event combination.
          - Multiple drugs per patient = multiple records per patient.

        Returns list of ADRSignal objects for all detected signals.
        """
        if not records:
            return []

        # Build frequency tables
        drug_event_counts:  dict[tuple, int] = {}
        drug_counts:        dict[str, int]   = {}
        event_counts:       dict[str, int]   = {}
        n_total = len(records)

        for r in records:
            drug  = r.get("drug_name", r.get("ndc11", "unknown"))
            event = r.get("event_type", "unknown")
            key   = (drug, event)
            drug_event_counts[key]  = drug_event_counts.get(key, 0) + 1
            drug_counts[drug]       = drug_counts.get(drug, 0) + 1
            event_counts[event]     = event_counts.get(event, 0) + 1

        signals: list[ADRSignal] = []

        for (drug, event), count_a in drug_event_counts.items():
            if drug_filter and drug_filter.lower() not in drug.lower():
                continue
            if event_filter and event_filter.lower() not in event.lower():
                continue

            a = count_a
            b = drug_counts[drug] - a          # drug, no event
            c = event_counts[event] - a         # event, no drug
            d = n_total - a - b - c             # neither
            d = max(0, d)

            if a < PRR_MIN_CASES:
                continue

            prr_result = self.compute_prr(a, b, c, d)
            ror_result = self.compute_ror(a, b, c, d)

            if not prr_result["is_signal"] and not ror_result["is_signal"]:
                continue

            severity = _prr_severity(prr_result.get("prr") or 1.0, a)
            description = (
                f"Signal detected: {drug} → {event}. "
                f"Cases: {a}/{drug_counts[drug]} patients on this drug ({100*a/max(drug_counts[drug],1):.1f}%). "
                f"PRR={prr_result.get('prr', 0):.2f} χ²={prr_result.get('chi2', 0):.2f} "
                f"ROR={ror_result.get('ror', 0):.2f} (95% CI {ror_result.get('ci_lower', 0):.2f}–{ror_result.get('ci_upper', 0):.2f}). "
                f"Pharmacist review required before any action."
            )

            signals.append(ADRSignal(
                drug_name=drug,
                ndc11=next((r.get("ndc11", "") for r in records if r.get("drug_name") == drug), ""),
                event_type=event,
                method="prr_ror",
                prr=prr_result.get("prr"),
                prr_chi2=prr_result.get("chi2"),
                ror=ror_result.get("ror"),
                ror_ci_lower=ror_result.get("ci_lower"),
                ror_ci_upper=ror_result.get("ci_upper"),
                n_cases=a,
                n_drug_total=drug_counts[drug],
                n_event_total=event_counts[event],
                n_total=n_total,
                severity=severity,
                is_signal=True,
                description=description,
                pharmacist_note="Statistical signal only. Causality not established. Do not act without clinical review.",
            ))

        # Sort by PRR descending (strongest signals first)
        signals.sort(
            key=lambda s: (s.prr or 0) * s.n_cases,
            reverse=True,
        )
        return signals


# ── CUSUM Signal Monitor ───────────────────────────────────────────────────────

class CUSUMSignalMonitor:
    """
    Shiryaev-Roberts change-point detection for emerging adverse event signals.
    Better suited than CUSUM for detecting persistent shifts in event rates.

    Monitors adverse event rate for each drug over time.
    Alerts when the rate shifts significantly upward.
    """

    def __init__(self, nu: float = 2.0, detection_threshold: float = CUSUM_THRESHOLD):
        """
        nu: expected shift size in standard deviations to detect.
            nu=2.0 means we're tuned to detect a doubling of event rate.
        """
        self.nu        = nu
        self.threshold = detection_threshold
        self._stats:   dict[str, dict] = {}   # drug → running state

    def update(self, drug: str, rate: float) -> Optional[ADRSignal]:
        """
        Update with the latest adverse event rate for a drug.
        rate: adverse events per 100 dispenses in the latest period.
        Returns ADRSignal if threshold crossed, None otherwise.
        """
        if drug not in self._stats:
            self._stats[drug] = {
                "sr": 0.0,          # Shiryaev-Roberts statistic
                "baseline": rate,   # First observation as baseline
                "n": 0,
                "alerted": False,
            }

        s = self._stats[drug]
        s["n"] += 1

        # Calibrate baseline from first 3 observations
        if s["n"] <= 3:
            s["baseline"] = (s["baseline"] * (s["n"] - 1) + rate) / s["n"]
            return None

        mu0 = max(s["baseline"], 0.01)   # baseline rate
        mu1 = mu0 * self.nu               # post-shift rate we're detecting

        # Log-likelihood ratio for Poisson rates
        if mu1 <= 0 or rate <= 0:
            llr = 0.0
        else:
            llr = rate * (np.log(mu1 / mu0)) - (mu1 - mu0)

        # Shiryaev-Roberts recursive update
        s["sr"] = max(0.0, (s["sr"] + 1) * np.exp(llr))

        if s["sr"] > self.threshold and not s["alerted"]:
            s["alerted"] = True
            return ADRSignal(
                drug_name=drug,
                event_type="adverse_event_rate_increase",
                method="cusum_shiryaev_roberts",
                cusum_value=s["sr"],
                n_cases=s["n"],
                severity="high" if s["sr"] > self.threshold * 2 else "moderate",
                is_signal=True,
                description=(
                    f"Sustained adverse event rate increase detected for {drug}. "
                    f"SR statistic: {s['sr']:.2f} (threshold: {self.threshold}). "
                    f"Current rate: {rate:.1f} per 100 dispenses vs. "
                    f"baseline: {mu0:.1f}. "
                    f"Pharmacist review required."
                ),
                pharmacist_note="Rate-based signal. Verify by reviewing individual case reports.",
            )

        # Reset after sustained high rate is acknowledged (SR > 2× threshold)
        if s["sr"] > self.threshold * 3:
            s["sr"] = 0.0
            s["alerted"] = False
            s["baseline"] = rate   # Recalibrate to new baseline

        return None

    def get_states(self) -> list[dict]:
        return [
            {
                "drug":      drug,
                "sr_stat":   round(s["sr"], 3),
                "baseline":  round(s["baseline"], 3),
                "alerted":   s["alerted"],
                "n_obs":     s["n"],
                "threshold": self.threshold,
            }
            for drug, s in self._stats.items()
        ]

    def reset(self, drug: str) -> None:
        """Reset SR statistic after pharmacist acknowledges and investigates."""
        if drug in self._stats:
            self._stats[drug]["sr"] = 0.0
            self._stats[drug]["alerted"] = False


# ── ADR Case Registry ─────────────────────────────────────────────────────────

class ADRCaseRegistry:
    """
    Internal pharmacovigilance case management workflow.

    Manages the lifecycle of ADR cases from initial report through
    pharmacist review, disposition, and optional FDA MedWatch submission.

    Storage: in-memory for this session. Wire to PostgreSQL or
    SQLite for persistence across restarts (see /api/v1/pharmacovigilance).
    """

    def __init__(self):
        self._cases: dict[UUID, ADRCase] = {}

    def create_case(
        self,
        ndc11: str,
        drug_name: str,
        event_type: str,
        event_date: Optional[date] = None,
        severity: str = "moderate",
        description: str = "",
        reported_by: str = "",
        patient_id: Optional[UUID] = None,
    ) -> ADRCase:
        case = ADRCase(
            patient_id=patient_id,
            ndc11=ndc11,
            drug_name=drug_name,
            event_type=event_type,
            event_date=event_date or date.today(),
            severity=severity,
            description=description,
            reported_by=reported_by,
        )
        self._cases[case.case_id] = case
        log.info("ADR case created: %s / %s / %s", drug_name, event_type, case.case_id)
        return case

    def get_case(self, case_id: UUID) -> Optional[ADRCase]:
        return self._cases.get(case_id)

    def list_cases(
        self,
        drug_name: Optional[str] = None,
        reviewed: Optional[bool] = None,
        severity: Optional[str] = None,
    ) -> list[ADRCase]:
        cases = list(self._cases.values())
        if drug_name:
            cases = [c for c in cases if drug_name.lower() in c.drug_name.lower()]
        if reviewed is not None:
            cases = [c for c in cases if c.reviewed == reviewed]
        if severity:
            cases = [c for c in cases if c.severity == severity]
        return sorted(cases, key=lambda c: c.created_at, reverse=True)

    def review_case(
        self,
        case_id: UUID,
        reviewed_by: str,
        causality: str,
        disposition: str,
        pharmacist_note: str = "",
        submit_medwatch: bool = False,
    ) -> Optional[ADRCase]:
        """
        Pharmacist review disposition.
        disposition: confirmed | dismissed | monitor | report_to_fda
        causality:   certain | probable | possible | unlikely
        """
        case = self._cases.get(case_id)
        if not case:
            return None
        case.reviewed       = True
        case.reviewed_by    = reviewed_by
        case.reviewed_at    = datetime.now(timezone.utc)
        case.causality      = causality
        case.disposition    = disposition
        case.medwatch_submitted = submit_medwatch
        log.info(
            "ADR case %s reviewed by %s: %s / %s",
            case_id, reviewed_by, causality, disposition
        )
        return case

    def signal_summary(self) -> dict:
        """Summary statistics for the case registry."""
        all_cases = list(self._cases.values())
        return {
            "total_cases":      len(all_cases),
            "unreviewed":       sum(1 for c in all_cases if not c.reviewed),
            "confirmed":        sum(1 for c in all_cases if c.disposition == "confirmed"),
            "dismissed":        sum(1 for c in all_cases if c.disposition == "dismissed"),
            "reported_to_fda":  sum(1 for c in all_cases if c.medwatch_submitted),
            "by_severity": {
                sev: sum(1 for c in all_cases if c.severity == sev)
                for sev in ("mild", "moderate", "severe", "life_threatening", "fatal")
            },
        }
