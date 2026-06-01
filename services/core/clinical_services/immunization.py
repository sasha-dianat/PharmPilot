"""
Immunization Administration Module
====================================
Records vaccine administration, generates VIS (Vaccine Information Statement)
acknowledgments, tracks ACIP schedule, submits to state immunization registries
via HL7 v2.5.1 VXU (Vaccination Update) messages.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# ACIP recommended vaccines with typical age ranges
ACIP_VACCINES = {
    "FLU":      {"name": "Influenza",              "schedule": "Annual, age 6mo+",    "cpt": "90686"},
    "COVID19":  {"name": "COVID-19",               "schedule": "Annual booster",       "cpt": "91305"},
    "TDAP":     {"name": "Tdap",                   "schedule": "Every 10 years",       "cpt": "90715"},
    "PNEUMO":   {"name": "Pneumococcal (PPSV23)",  "schedule": "Age 65+, high-risk",  "cpt": "90732"},
    "SHINGRIX": {"name": "Shingrix (Zoster)",      "schedule": "Age 50+, 2-dose",     "cpt": "90750"},
    "HEPATB":   {"name": "Hepatitis B",            "schedule": "3-dose series",        "cpt": "90746"},
    "HPV":      {"name": "HPV (Gardasil 9)",       "schedule": "Age 9-45",            "cpt": "90651"},
    "MMR":      {"name": "MMR",                    "schedule": "2-dose series",        "cpt": "90707"},
    "VARICELLA":{"name": "Varicella",              "schedule": "2-dose series",        "cpt": "90716"},
    "MENING":   {"name": "Meningococcal (MenACWY)","schedule": "Age 11-18",           "cpt": "90734"},
}

# VIS (Vaccine Information Statement) current dates — must be verified against CDC
VIS_DATES = {
    "FLU":       "8/15/2021",
    "COVID19":   "9/12/2023",
    "TDAP":      "2/24/2015",
    "PNEUMO":    "10/6/2023",
    "SHINGRIX":  "10/1/2019",
    "HEPATB":    "1/12/2012",
}


@dataclass
class ImmunizationRecord:
    record_id: UUID = field(default_factory=uuid4)
    patient_id: UUID = field(default_factory=uuid4)
    administering_pharmacist_id: UUID = field(default_factory=uuid4)
    pharmacy_id: UUID = field(default_factory=uuid4)

    # Vaccine details
    vaccine_code: str = ""              # CVX code (HL7 standard)
    vaccine_name: str = ""
    manufacturer: str = ""
    lot_number: str = ""
    expiry_date: Optional[date] = None
    ndc: Optional[str] = None
    route: str = "IM"                   # IM, SC, Intranasal
    site: str = "right_deltoid"
    dose_volume_ml: float = 0.5
    dose_number_in_series: int = 1
    series_complete: bool = True

    # Administration
    administered_date: date = field(default_factory=date.today)
    administered_at: Optional[str] = None  # datetime string

    # VIS documentation (legally required)
    vis_document_version: str = ""
    vis_presented_date: Optional[date] = None
    patient_consent_obtained: bool = False

    # Outcome
    adverse_event: Optional[str] = None
    observation_period_minutes: int = 15
    patient_discharged: bool = False

    # Registry submission
    submitted_to_registry: bool = False
    registry_submission_ack: Optional[str] = None

    # Billing
    cpt_code: str = ""
    iis_code: str = ""  # State IIS vaccine code

    def validate(self) -> list[str]:
        """Returns list of validation errors before record can be finalized."""
        errors = []
        if not self.vaccine_code:
            errors.append("Vaccine CVX code required")
        if not self.lot_number:
            errors.append("Lot number required")
        if not self.expiry_date:
            errors.append("Vaccine expiry date required")
        if not self.vis_presented_date:
            errors.append("VIS presented date required (legal requirement)")
        if not self.patient_consent_obtained:
            errors.append("Patient consent not documented")
        if not self.patient_discharged:
            errors.append("15-minute observation period not confirmed")
        return errors

    def to_hl7_vxu(self) -> str:
        """Generate HL7 v2.5.1 VXU_V04 message for state registry submission."""
        ts = self.administered_at or f"{self.administered_date.strftime('%Y%m%d')}120000"
        return "\r".join([
            f"MSH|^~\\&|PHARMPILOT|{self.pharmacy_id!s:.8}|||{ts}||VXU^V04^VXU_V04|{self.record_id!s:.20}|P|2.5.1",
            f"PID|1||{self.patient_id!s:.20}||||||",
            f"ORC|RE|||||||||||{self.administering_pharmacist_id!s:.15}",
            f"RXA|0|1|{ts}|{ts}|{self.vaccine_code}^{self.vaccine_name}^CVX|{self.dose_volume_ml}|mL||00^New immunization|"
            f"|{self.lot_number}|{self.expiry_date.strftime('%Y%m%d') if self.expiry_date else ''}|"
            f"{self.manufacturer}||||CP",
            f"RXR|{self.route}^{self.route}^HL70162|{self.site}^{self.site}^HL70163",
            f"OBX|1|CE|64994-7^Vaccine funding program eligibility category^LN||V02^VFC eligible - Medicaid/Medicaid Managed Care^HL70064||||||F|||{ts}",
        ]) + "\r"


class ImmunizationScheduleChecker:
    """
    Checks patient's vaccination history and recommends overdue vaccines
    based on ACIP schedule and patient demographics.
    """

    def check_patient(
        self,
        patient_age: int,
        patient_diagnoses: list[str],
        prior_immunizations: list[dict],  # [{vaccine_code, date}]
        is_pregnant: bool = False,
    ) -> list[dict]:
        """Returns list of recommended vaccines with rationale."""
        recommendations = []
        prior_codes = {imm["vaccine_code"] for imm in prior_immunizations}

        # Annual flu
        last_flu = max(
            (imm["date"] for imm in prior_immunizations if imm["vaccine_code"] in ("FLU", "LAIV")),
            default=None
        )
        if not last_flu or (date.today() - date.fromisoformat(last_flu)).days > 365:
            recommendations.append({
                "vaccine": ACIP_VACCINES["FLU"],
                "code": "FLU",
                "urgency": "due",
                "rationale": "Annual flu vaccine recommended for age 6mo+",
                "cpt": ACIP_VACCINES["FLU"]["cpt"],
            })

        # Shingrix for 50+
        if patient_age >= 50 and "SHINGRIX" not in prior_codes:
            recommendations.append({
                "vaccine": ACIP_VACCINES["SHINGRIX"],
                "code": "SHINGRIX",
                "urgency": "overdue",
                "rationale": f"Shingrix recommended for age {patient_age} (50+ threshold)",
                "cpt": ACIP_VACCINES["SHINGRIX"]["cpt"],
            })

        # Pneumococcal for 65+
        if patient_age >= 65 and "PNEUMO" not in prior_codes:
            recommendations.append({
                "vaccine": ACIP_VACCINES["PNEUMO"],
                "code": "PNEUMO",
                "urgency": "overdue",
                "rationale": "Pneumococcal vaccine recommended for age 65+",
                "cpt": ACIP_VACCINES["PNEUMO"]["cpt"],
            })

        # High-risk conditions — pneumococcal regardless of age
        high_risk_conditions = ["diabetes", "copd", "heart failure", "chronic kidney", "immunocompromised"]
        if any(c in " ".join(patient_diagnoses).lower() for c in high_risk_conditions):
            if "PNEUMO" not in prior_codes:
                recommendations.append({
                    "vaccine": ACIP_VACCINES["PNEUMO"],
                    "code": "PNEUMO",
                    "urgency": "due",
                    "rationale": "High-risk condition — pneumococcal recommended regardless of age",
                    "cpt": ACIP_VACCINES["PNEUMO"]["cpt"],
                })

        # Pregnancy contraindications
        if is_pregnant:
            recommendations = [r for r in recommendations if r["code"] not in ("SHINGRIX", "MMR", "VARICELLA", "HPV")]

        logger.info(
            "Immunization check for age=%d: %d vaccines recommended",
            patient_age, len(recommendations)
        )
        return recommendations
