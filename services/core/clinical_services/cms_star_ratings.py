"""
CMS Star Ratings Calculator
=============================
Calculates the pharmacy-level Part D Star Ratings metrics that directly
affect PBM reimbursement bonuses and Quality Bonus Payments (QBPs).

Key metrics:
  PDC (Proportion of Days Covered) — for diabetes, hypertension, cholesterol
  CMR (Comprehensive Medication Review) completion rate
  Statin Use in Persons with Diabetes (SUPD)

PDC Formula (HEDIS-compliant):
  PDC = (days covered in measurement period) / (measurement period days)
  Target thresholds: 3-star=0.75, 4-star=0.83, 5-star=0.89
"""
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

MEASUREMENT_PERIOD_DAYS = 365

# PDC drug classes and representative drug names
PDC_DRUG_CLASSES = {
    "diabetes_all":       ["metformin","glipizide","glimepiride","sitagliptin","empagliflozin","canagliflozin","dapagliflozin","liraglutide","semaglutide","insulin"],
    "diabetes_rasa":      ["lisinopril","enalapril","ramipril","losartan","valsartan","irbesartan","olmesartan","amlodipine"],
    "statin_adherence":   ["atorvastatin","rosuvastatin","simvastatin","pravastatin","lovastatin","fluvastatin","pitavastatin"],
    "rasa_adherence":     ["lisinopril","enalapril","ramipril","benazepril","losartan","valsartan","irbesartan","olmesartan"],
    "copd":               ["tiotropium","umeclidinium","aclidinium","ipratropium","salmeterol","formoterol","indacaterol","vilanterol"],
    "heart_failure_rasa": ["lisinopril","enalapril","carvedilol","metoprolol succinate","bisoprolol","sacubitril"],
}

# Star Rating thresholds per metric
PDC_STAR_THRESHOLDS = {
    3: 0.75,
    4: 0.83,
    5: 0.89,
}


@dataclass
class PDCResult:
    patient_id: UUID
    drug_class: str
    measurement_start: date
    measurement_end: date
    days_covered: int
    measurement_days: int
    pdc_score: float
    star_level: int
    is_adherent: bool  # PDC >= 0.80 = adherent per CMS definition
    gaps: list[dict] = field(default_factory=list)  # [{start, end, days}]


@dataclass
class PharmacyStarRatings:
    pharmacy_id: UUID
    measurement_year: int
    calculated_at: date = field(default_factory=date.today)

    # PDC metrics (0.0 – 1.0)
    pdc_diabetes: Optional[float] = None
    pdc_hypertension: Optional[float] = None
    pdc_cholesterol: Optional[float] = None
    pdc_copd: Optional[float] = None

    # Counts
    eligible_diabetes: int = 0
    eligible_hypertension: int = 0
    eligible_cholesterol: int = 0
    adherent_diabetes: int = 0
    adherent_hypertension: int = 0
    adherent_cholesterol: int = 0

    # CMR
    cmr_eligible: int = 0
    cmr_completed: int = 0
    cmr_completion_rate: Optional[float] = None

    # Statin use in persons with diabetes
    supd_eligible: int = 0
    supd_on_statin: int = 0
    supd_rate: Optional[float] = None

    # Star levels (3, 4, or 5 per metric)
    star_diabetes: int = 0
    star_hypertension: int = 0
    star_cholesterol: int = 0
    star_cmr: int = 0
    overall_star_estimate: float = 0.0

    def calculate_overall(self) -> None:
        """Estimate overall star rating from component metrics."""
        scores = []
        if self.pdc_diabetes is not None:
            scores.append(self._pdc_to_stars(self.pdc_diabetes))
        if self.pdc_hypertension is not None:
            scores.append(self._pdc_to_stars(self.pdc_hypertension))
        if self.pdc_cholesterol is not None:
            scores.append(self._pdc_to_stars(self.pdc_cholesterol))
        if self.cmr_completion_rate is not None:
            # CMR threshold: 5-star ≥ 72%, 4-star ≥ 52%
            cmr_stars = 5 if self.cmr_completion_rate >= 0.72 else (4 if self.cmr_completion_rate >= 0.52 else 3)
            scores.append(cmr_stars)
        self.overall_star_estimate = round(sum(scores) / len(scores), 1) if scores else 0.0

    @staticmethod
    def _pdc_to_stars(pdc: float) -> int:
        if pdc >= PDC_STAR_THRESHOLDS[5]: return 5
        if pdc >= PDC_STAR_THRESHOLDS[4]: return 4
        return 3


class StarRatingsCalculator:
    """
    Calculates CMS PDC metrics from dispensing history.
    Implements the HEDIS-compliant PDC algorithm:
    - Only count days covered by fills within the measurement period
    - No overlap credit (if patient fills early, excess days pushed forward)
    - Gaps = periods with no active supply
    """

    def __init__(self, db=None):
        self.db = db

    async def calculate_pharmacy_ratings(
        self,
        pharmacy_id: UUID,
        measurement_year: int,
    ) -> PharmacyStarRatings:
        ratings = PharmacyStarRatings(
            pharmacy_id=pharmacy_id,
            measurement_year=measurement_year,
        )

        period_start = date(measurement_year, 1, 1)
        period_end   = date(measurement_year, 12, 31)

        if not self.db:
            return ratings

        from sqlalchemy import text

        # For each PDC drug class, calculate adherence across all eligible patients
        for drug_class, drugs in PDC_DRUG_CLASSES.items():
            if not any(d in ["atorvastatin","rosuvastatin","simvastatin","pravastatin","lovastatin"] for d in drugs):
                if drug_class not in ("diabetes_all", "rasa_adherence"):
                    continue

            drug_list = ", ".join(f"'{d.lower()}'" for d in drugs)
            result = await self.db.execute(text(f"""
                SELECT
                    pr.patient_id,
                    array_agg(json_build_object(
                        'fill_date', pf.fill_date,
                        'days_supply', pf.days_supply
                    ) ORDER BY pf.fill_date) AS fills
                FROM prescription_fills pf
                JOIN prescriptions pr ON pr.id = pf.prescription_id
                WHERE
                    pf.fill_date BETWEEN :start AND :end
                    AND lower(pr.drug_name) = ANY(ARRAY[{drug_list}])
                GROUP BY pr.patient_id
                HAVING count(*) >= 2
            """), {"start": period_start, "end": period_end})

            total = 0
            adherent = 0
            for row in result.mappings().all():
                pdc = self._calculate_pdc(row["fills"], period_start, period_end)
                total += 1
                if pdc.is_adherent:
                    adherent += 1

            if "statin" in drug_class:
                ratings.eligible_cholesterol = total
                ratings.adherent_cholesterol = adherent
                ratings.pdc_cholesterol = adherent / total if total else None
            elif "diabetes" in drug_class:
                ratings.eligible_diabetes = total
                ratings.adherent_diabetes = adherent
                ratings.pdc_diabetes = adherent / total if total else None
            elif "rasa" in drug_class:
                ratings.eligible_hypertension = total
                ratings.adherent_hypertension = adherent
                ratings.pdc_hypertension = adherent / total if total else None

        ratings.calculate_overall()
        logger.info(
            "Star ratings calculated for pharmacy %s year %d: %.1f stars estimated",
            str(pharmacy_id)[:8], measurement_year, ratings.overall_star_estimate
        )
        return ratings

    def _calculate_pdc(
        self,
        fills: list[dict],
        period_start: date,
        period_end: date,
    ) -> PDCResult:
        """
        HEDIS PDC calculation:
        1. For each fill, determine the days covered (start_date to start_date+days_supply)
        2. Cap to measurement period
        3. Merge overlapping coverage (no overlap credit — shift excess forward)
        4. PDC = total_covered_days / measurement_period_days
        """
        from datetime import date as date_type

        measurement_days = (period_end - period_start).days + 1
        covered_days_set: set[date_type] = set()
        gaps = []

        # Sort fills by date and calculate coverage
        sorted_fills = sorted(fills, key=lambda f: f["fill_date"])
        current_end = None

        for fill in sorted_fills:
            fill_date = fill["fill_date"] if isinstance(fill["fill_date"], date) else date.fromisoformat(str(fill["fill_date"]))
            days_supply = int(fill["days_supply"])

            # HEDIS: if refill before current supply exhausted, start at exhaustion date
            effective_start = max(fill_date, current_end + timedelta(days=1)) if current_end else fill_date
            effective_end   = effective_start + timedelta(days=days_supply - 1)
            current_end     = effective_end

            # Clip to measurement period
            clip_start = max(effective_start, period_start)
            clip_end   = min(effective_end, period_end)

            if clip_start <= clip_end:
                d = clip_start
                while d <= clip_end:
                    covered_days_set.add(d)
                    d += timedelta(days=1)

        days_covered = len(covered_days_set)
        pdc_score    = days_covered / measurement_days if measurement_days > 0 else 0.0

        return PDCResult(
            patient_id=uuid4(),  # Anonymized for aggregate
            drug_class="",
            measurement_start=period_start,
            measurement_end=period_end,
            days_covered=days_covered,
            measurement_days=measurement_days,
            pdc_score=round(pdc_score, 4),
            star_level=PharmacyStarRatings._pdc_to_stars(pdc_score),
            is_adherent=pdc_score >= 0.80,
        )
