"""
DUR (Drug Utilization Review) Engine.
Coordinates drug database checks with ACB clinical AI to produce
severity-tiered alerts displayed in the pharmacist workflow.
"""
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class DURAlertData:
    alert_type: str
    severity: str              # critical, high, moderate, informational
    source: str                # fdb, acb, beers, stopp_start, pain_mgmt, renal, pdmp
    description: str
    is_hard_stop: bool = False
    interacting_drug_ndc: Optional[str] = None
    interacting_drug_name: Optional[str] = None
    evidence_grade: Optional[str] = None
    citations: Optional[list] = None
    pharmacist_action_required: bool = False


# Severity → is_hard_stop mapping
SEVERITY_HARD_STOP = {"critical"}
SEVERITY_PHARMACIST_ACTION = {"critical", "high"}


class DUREngine:
    """
    Runs all DUR checks for a new prescription and returns prioritized alerts.
    Integrates with FDB/Medi-Span drug database and ACB clinical brain.
    """

    def __init__(self, drug_db_client=None, acb_orchestrator=None):
        self._drug_db = drug_db_client
        self._acb = acb_orchestrator

        # Load specialty modules
        from services.ai.clinical_brain.specialties.drug_safety import DrugSafetyModule
        from services.ai.clinical_brain.specialties.renal import RenalDosingModule
        from services.ai.clinical_brain.specialties.geriatrics import GeriatricsModule
        from services.ai.clinical_brain.specialties.pain import PainManagementModule
        self._drug_safety = DrugSafetyModule()
        self._renal = RenalDosingModule()
        self._geriatrics = GeriatricsModule()
        self._pain = PainManagementModule()

    async def run_full_review(
        self,
        prescription: dict,
        patient_profile: dict,
    ) -> list[DURAlertData]:
        """
        Run all DUR checks for a new prescription.
        Returns alerts sorted by severity (critical first).
        """
        alerts: list[DURAlertData] = []
        active_meds = patient_profile.get("active_medications", [])
        allergies = patient_profile.get("allergies", [])
        age = patient_profile.get("age", 0)
        egfr = patient_profile.get("egfr")
        diagnoses = patient_profile.get("diagnoses", [])

        # ── Layer 1: Allergy check ────────────────────────────────
        alerts.extend(self._check_allergies(prescription, allergies))

        # ── Layer 2: Drug-drug interactions ───────────────────────
        interactions = self._drug_safety.check_interactions(
            new_drug=prescription,
            active_medications=active_meds,
            allergies=allergies,
        )
        for finding in interactions:
            severity = finding.get("severity", "moderate")
            alerts.append(DURAlertData(
                alert_type=finding.get("type", "interaction"),
                severity=severity,
                source="drug_safety",
                description=finding.get("description", ""),
                is_hard_stop=(severity == "critical"),
                interacting_drug_name=finding.get("interacting_drug"),
                evidence_grade=finding.get("evidence_grade", "B"),
                pharmacist_action_required=(severity in SEVERITY_PHARMACIST_ACTION),
            ))

        # ── Layer 3: Renal dosing ─────────────────────────────────
        if egfr is not None and egfr < 60:
            renal_finding = self._renal.evaluate(prescription, egfr)
            if renal_finding:
                is_critical = "CONTRAINDICATED" in renal_finding
                alerts.append(DURAlertData(
                    alert_type="renal_dosing",
                    severity="critical" if is_critical else "high",
                    source="renal",
                    description=renal_finding,
                    is_hard_stop=is_critical,
                    evidence_grade="A",
                    pharmacist_action_required=True,
                ))

        # ── Layer 4: Geriatric / Beers Criteria ───────────────────
        if age >= 65:
            beers_finding = self._geriatrics.beers_check(prescription, diagnoses)
            if beers_finding:
                alerts.append(DURAlertData(
                    alert_type="beers_criteria",
                    severity="high",
                    source="beers_criteria",
                    description=beers_finding,
                    is_hard_stop=False,
                    evidence_grade="A",
                    pharmacist_action_required=True,
                ))

        # ── Layer 5: Controlled substance / opioid ────────────────
        dea_schedule = prescription.get("dea_schedule", "")
        if dea_schedule in ("CI", "CII", "CIII", "CIV", "CV"):
            pain_finding = self._pain.evaluate(
                new_prescription=prescription,
                active_medications=active_meds,
                patient_age=age,
            )
            if pain_finding:
                alerts.append(DURAlertData(
                    alert_type="high_dose_opioid",
                    severity="high",
                    source="pain_management",
                    description=pain_finding,
                    is_hard_stop=False,
                    evidence_grade="A",
                    pharmacist_action_required=True,
                ))

        # ── Layer 6: Pregnancy check ──────────────────────────────
        pregnancy_status = patient_profile.get("pregnancy_status")
        if pregnancy_status in ("pregnant", "trying_to_conceive", "breastfeeding"):
            preg_alert = self._check_pregnancy(prescription, pregnancy_status)
            if preg_alert:
                alerts.append(preg_alert)

        # ── Layer 7: Duplicate therapy ────────────────────────────
        dup_alerts = self._check_duplicate_therapy(prescription, active_meds)
        alerts.extend(dup_alerts)

        # Deduplicate and sort
        alerts = self._deduplicate(alerts)
        alerts = sorted(
            alerts,
            key=lambda a: ["critical", "high", "moderate", "informational"].index(a.severity)
        )

        logger.info(
            "DUR complete for Rx %s: %d alerts (%d critical, %d high)",
            prescription.get("rx_number", "?"),
            len(alerts),
            sum(1 for a in alerts if a.severity == "critical"),
            sum(1 for a in alerts if a.severity == "high"),
        )

        return alerts

    def _check_allergies(self, prescription: dict, allergies: list) -> list[DURAlertData]:
        alerts = []
        drug_name = prescription.get("drug_name", "").lower()
        ndc = prescription.get("ndc", "")

        for allergy in allergies:
            allergen = allergy.get("allergen_name", "").lower()
            allergen_ndc = allergy.get("allergen_ndc", "")

            is_match = False
            if allergen and allergen in drug_name:
                is_match = True
            elif allergen_ndc and allergen_ndc[:9] == ndc[:9]:  # Same product
                is_match = True

            if is_match:
                alerts.append(DURAlertData(
                    alert_type="allergy",
                    severity="critical",
                    source="allergy_check",
                    description=(
                        f"⚠️ ALLERGY ALERT: Patient documented allergy to {allergy['allergen_name']}. "
                        f"Reaction: {allergy.get('reaction', 'documented')}. "
                        f"Severity: {allergy.get('severity', 'unknown')}."
                    ),
                    is_hard_stop=True,
                    evidence_grade="A",
                    pharmacist_action_required=True,
                ))
        return alerts

    def _check_pregnancy(self, prescription: dict, pregnancy_status: str) -> Optional[DURAlertData]:
        drug_name = prescription.get("drug_name", "").lower()

        # High-risk drugs in pregnancy (simplified — production uses FDB/LactMed API)
        PREGNANCY_CONTRAINDICATED = [
            "warfarin", "methotrexate", "thalidomide", "isotretinoin",
            "valproic acid", "valproate", "lithium", "misoprostol",
            "finasteride", "dutasteride",
        ]
        BREASTFEEDING_CAUTION = [
            "codeine", "opioid", "benzodiazepine", "alprazolam",
            "clonazepam", "amiodarone", "radioactive",
        ]

        if pregnancy_status == "pregnant":
            for drug in PREGNANCY_CONTRAINDICATED:
                if drug in drug_name:
                    return DURAlertData(
                        alert_type="pregnancy_safety",
                        severity="critical",
                        source="pregnancy_safety",
                        description=(
                            f"PREGNANCY ALERT: {drug_name} is contraindicated or requires "
                            f"extreme caution in pregnancy. Verify with prescriber."
                        ),
                        is_hard_stop=True,
                        evidence_grade="A",
                        pharmacist_action_required=True,
                    )

        elif pregnancy_status == "breastfeeding":
            for drug in BREASTFEEDING_CAUTION:
                if drug in drug_name:
                    return DURAlertData(
                        alert_type="breastfeeding_safety",
                        severity="high",
                        source="pregnancy_safety",
                        description=(
                            f"BREASTFEEDING CAUTION: {drug_name} may be present in breast milk. "
                            f"Review LactMed database and counsel patient."
                        ),
                        is_hard_stop=False,
                        evidence_grade="B",
                        pharmacist_action_required=True,
                    )
        return None

    def _check_duplicate_therapy(
        self,
        prescription: dict,
        active_meds: list,
    ) -> list[DURAlertData]:
        """Detect same-class therapeutic duplicates."""
        alerts = []
        new_drug = prescription.get("drug_name", "").lower()
        new_gpi = prescription.get("gpi", "")

        # GPI-based duplicate detection (first 4 chars = therapeutic class)
        if new_gpi:
            new_class = new_gpi[:4]
            for med in active_meds:
                med_gpi = med.get("gpi", "")
                if med_gpi and med_gpi[:4] == new_class and med.get("drug_name", "").lower() != new_drug:
                    alerts.append(DURAlertData(
                        alert_type="duplicate_therapy",
                        severity="moderate",
                        source="duplicate_therapy",
                        description=(
                            f"DUPLICATE THERAPY: {prescription.get('drug_name')} is in the same "
                            f"therapeutic class as {med.get('drug_name')}. "
                            f"Verify clinical intent with prescriber."
                        ),
                        interacting_drug_name=med.get("drug_name"),
                        is_hard_stop=False,
                        evidence_grade="B",
                        pharmacist_action_required=False,
                    ))

        return alerts

    def _deduplicate(self, alerts: list[DURAlertData]) -> list[DURAlertData]:
        """Remove duplicate alerts from multiple sources about the same issue."""
        seen = set()
        unique = []
        for alert in alerts:
            key = (alert.alert_type, alert.interacting_drug_name or "", alert.severity)
            if key not in seen:
                seen.add(key)
                unique.append(alert)
        return unique


async def create_dur_alerts_for_rx(
    prescription_id: UUID,
    prescription_dict: dict,
    patient_profile: dict,
    db,
) -> list:
    """
    Run DUR and persist alerts to the database.
    Called after Rx intake, returns list of alert dicts for the UI.
    """
    from shared.models.prescription import DURAlert

    engine = DUREngine()
    alert_data = await engine.run_full_review(prescription_dict, patient_profile)

    db_alerts = []
    for alert in alert_data:
        dur_alert = DURAlert(
            prescription_id=prescription_id,
            alert_type=alert.alert_type,
            severity=alert.severity,
            source=alert.source,
            description=alert.description,
            interacting_drug_ndc=alert.interacting_drug_ndc,
            interacting_drug_name=alert.interacting_drug_name,
            is_hard_stop=alert.is_hard_stop,
            was_shown=True,
            evidence_grade=alert.evidence_grade,
            citations=alert.citations,
        )
        db.add(dur_alert)
        db_alerts.append(dur_alert)

    return db_alerts
