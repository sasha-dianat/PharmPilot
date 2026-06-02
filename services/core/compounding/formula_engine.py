"""
Compounding Formula Engine — USP <795>/<797>/<800>
====================================================
Manages the pharmacy's compounding formula library and batch record generation.

USP chapters covered:
  <795> Non-sterile compounding (oral liquids, topicals, capsules)
  <797> Sterile compounding (IV admixtures, ophthalmics, injectables)
  <800> Hazardous drugs (antineoplastics, hormones, antiviral agents)

BUD (Beyond-Use Date) is calculated per USP tables — NOT the same as expiration date.
The BUD is the latest date by which a compounded preparation may be used.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)


class CompoundingCategory(str, Enum):
    NON_STERILE         = "non_sterile"      # USP <795>
    STERILE_LOW_RISK    = "sterile_low_risk"  # USP <797> Category 1
    STERILE_HIGH_RISK   = "sterile_high_risk" # USP <797> Category 2
    HAZARDOUS_NON_STERILE = "hazardous_ns"   # USP <800> non-sterile
    HAZARDOUS_STERILE   = "hazardous_sterile"# USP <800> sterile


# USP BUD tables (days) — simplified; full tables in USP 2023
# Key: (category, storage_condition) → max_bud_days
USP_BUD_TABLE = {
    # Non-sterile <795>
    ("non_sterile", "room_temp"):       180,
    ("non_sterile", "refrigerated"):    180,
    ("non_sterile", "frozen"):          180,

    # Sterile <797> Category 1
    ("sterile_low_risk", "room_temp"):   12,   # hours
    ("sterile_low_risk", "refrigerated"): 1,   # day
    ("sterile_low_risk", "frozen"):      45,   # days

    # Sterile <797> Category 2
    ("sterile_high_risk", "room_temp"):  30,
    ("sterile_high_risk", "refrigerated"): 45,
    ("sterile_high_risk", "frozen"):     60,

    # <800> hazardous — same BUD as non-hazardous equivalent category
}

BUD_UNIT = {
    "sterile_low_risk": "hours",
    # all others: "days"
}


@dataclass
class FormulaIngredient:
    """A single ingredient in a compounding formula."""
    ingredient_name: str
    quantity: float
    unit: str             # mg, mL, g, %
    ndc: Optional[str] = None
    lot_number: Optional[str] = None
    expiry_date: Optional[date] = None
    is_hazardous: bool = False
    cas_number: Optional[str] = None
    usp_grade: bool = True


@dataclass
class CompoundingFormula:
    """
    The master formula record — the authoritative definition of a preparation.
    All batch records derive from this master; changes require pharmacist sign-off.
    """
    formula_id: UUID = field(default_factory=uuid4)
    pharmacy_id: UUID = field(default_factory=uuid4)
    formula_name: str = ""
    version: str = "1.0"

    # Classification
    category: CompoundingCategory = CompoundingCategory.NON_STERILE
    dosage_form: str = ""         # capsule, oral_liquid, cream, injection, ophthalmic
    route: str = ""               # oral, topical, ophthalmic, IV, IM
    strength: str = ""

    # Ingredients
    ingredients: list[FormulaIngredient] = field(default_factory=list)
    batch_size: float = 1.0
    batch_size_unit: str = "g"    # g, mL, units

    # BUD
    storage_condition: str = "room_temp"  # room_temp | refrigerated | frozen
    calculated_bud_days: Optional[int] = None
    bud_supported_by_stability_data: bool = False  # If True, can exceed USP default

    # Compounding instructions
    instructions: str = ""
    equipment_required: list[str] = field(default_factory=list)
    quality_control_checks: list[str] = field(default_factory=list)
    packaging: str = ""
    labeling_requirements: list[str] = field(default_factory=list)

    # Compliance
    usp_chapter: str = ""
    is_hazardous: bool = False    # NIOSH Table 1, 2, or 3
    requires_cstd: bool = False   # Closed-System Transfer Device required
    ppe_requirements: list[str] = field(default_factory=list)

    # Sign-off
    created_by_pharmacist_id: Optional[UUID] = None
    approved_by_pharmacist_id: Optional[UUID] = None
    approved_at: Optional[datetime] = None
    is_active: bool = True
    superseded_by: Optional[UUID] = None

    def calculate_bud(self) -> tuple[int, str]:
        """
        Calculate the BUD per USP table.
        Returns (value, unit) — e.g., (12, 'hours') or (30, 'days').
        If stability data provided, pharmacist can set a longer BUD.
        """
        key = (self.category.value, self.storage_condition)
        bud_value = USP_BUD_TABLE.get(key, 14)
        unit = BUD_UNIT.get(self.category.value, "days")
        self.calculated_bud_days = bud_value if unit == "days" else bud_value // 24
        return bud_value, unit

    def validate(self) -> list[str]:
        """Return list of validation errors before the formula can be approved."""
        errors = []
        if not self.formula_name:
            errors.append("Formula name is required")
        if not self.ingredients:
            errors.append("At least one ingredient is required")
        if not self.instructions:
            errors.append("Compounding instructions are required")
        if not self.approved_by_pharmacist_id:
            errors.append("Pharmacist approval required before formula can be used")
        if self.is_hazardous and not self.ppe_requirements:
            errors.append("PPE requirements must be documented for hazardous drug formulas")
        if self.category in (CompoundingCategory.STERILE_LOW_RISK, CompoundingCategory.STERILE_HIGH_RISK):
            if "HEPA" not in " ".join(self.equipment_required):
                errors.append("Sterile compounding requires HEPA-filtered cleanroom (ISO Class 5 or better)")
        return errors


@dataclass
class BatchRecord:
    """
    The actual batch record for a specific compounding run.
    Generated from the master formula; every batch is uniquely identified.
    Required for PCAB accreditation and state board inspection.
    """
    batch_id: UUID = field(default_factory=uuid4)
    formula_id: UUID = field(default_factory=uuid4)
    formula_name: str = ""
    formula_version: str = ""
    pharmacy_id: UUID = field(default_factory=uuid4)

    # Batch specifics
    batch_number: str = ""         # Unique batch identifier
    preparation_date: date = field(default_factory=date.today)
    expiry_date: Optional[date] = None
    quantity_prepared: float = 0.0
    quantity_unit: str = ""

    # Ingredient actual lots used
    ingredient_lots: list[dict] = field(default_factory=list)
    # [{ingredient_name, lot_number, expiry_date, actual_quantity, unit, ndc}]

    # Measurements and quality control
    yield_actual: Optional[float] = None
    yield_expected: float = 0.0
    yield_variance_percent: Optional[float] = None
    pH_measured: Optional[float] = None
    osmolarity_measured: Optional[float] = None
    appearance: str = ""
    qc_pass: bool = False

    # Personnel
    compounded_by_id: Optional[UUID] = None
    checked_by_pharmacist_id: Optional[UUID] = None
    checked_at: Optional[datetime] = None

    # Deviations
    deviations: list[str] = field(default_factory=list)

    def calculate_yield_variance(self) -> Optional[float]:
        if self.yield_expected and self.yield_actual:
            variance = abs(self.yield_actual - self.yield_expected) / self.yield_expected * 100
            self.yield_variance_percent = round(variance, 1)
            return self.yield_variance_percent
        return None

    def is_within_acceptable_yield(self, tolerance_percent: float = 10.0) -> bool:
        var = self.calculate_yield_variance()
        return var is not None and var <= tolerance_percent


class BUDCalculator:
    """
    Calculate Beyond-Use Dates per USP 2023 guidelines.
    The BUD is ALWAYS the lesser of:
      1. The USP default BUD for the category
      2. Stability data-supported BUD (if available)
      3. Earliest expiry date of any ingredient used
    """

    def calculate(
        self,
        formula: CompoundingFormula,
        ingredient_expiry_dates: list[date],
        preparation_date: Optional[date] = None,
    ) -> tuple[date, str]:
        """
        Returns (bud_date, rationale_string).
        """
        prep_date = preparation_date or date.today()
        bud_value, bud_unit = formula.calculate_bud()

        if bud_unit == "hours":
            usp_bud = prep_date  # Same day for <12 hour BUD
            rationale = f"USP <797> Category 1: {bud_value}h BUD → expires today"
        else:
            usp_bud = prep_date + timedelta(days=bud_value)
            rationale = f"USP <{self._chapter(formula)}>: {bud_value}-day BUD"

        # Apply stability-extended BUD if supported
        if formula.bud_supported_by_stability_data and formula.calculated_bud_days:
            stability_bud = prep_date + timedelta(days=formula.calculated_bud_days)
            if stability_bud > usp_bud:
                usp_bud = stability_bud
                rationale = f"Stability data-supported BUD: {formula.calculated_bud_days} days"

        # Constrain by earliest ingredient expiry
        if ingredient_expiry_dates:
            earliest_expiry = min(ingredient_expiry_dates)
            if earliest_expiry < usp_bud:
                usp_bud = earliest_expiry
                rationale += f" → constrained by ingredient expiry {earliest_expiry}"

        return usp_bud, rationale

    def _chapter(self, formula: CompoundingFormula) -> str:
        if formula.is_hazardous:        return "800"
        if "sterile" in formula.category.value: return "797"
        return "795"
