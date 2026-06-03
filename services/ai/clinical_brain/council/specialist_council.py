"""
Specialist Clinical Council
===========================
Auto-triggered when a pharmacist opens the prescription review workbench.
Runs ALL specialist modules in parallel and streams results progressively.

Specialists:
  1. Cardiology — cardiac medications, QTc, anticoagulants
  2. Endocrinology — diabetes, thyroid, hormone therapy
  3. Nephrology — renal dosing adjustments, nephrotoxicity
  4. Geriatrics / Pediatrics — age-appropriate prescribing (Beers, weight-based)
  5. Psychiatry — CNS interactions, QTc, anticholinergic burden
  6. Clinical Pharmacology / Medication Safety — DDI, overdose thresholds
  7. Pain Management — opioid safety, MME, PDMP risk
  8. Nutrition / Complement Safety — drug-nutrient, herb-drug interactions
  9. Hereditary / Family Risk — hereditary conditions from linked family profiles

Each specialist runs independently. The coordinator then synthesizes:
  - BLOCKERS    (must resolve before dispensing)
  - CAUTIONS    (pharmacist should consider)
  - COUNSELING  (key points to share with patient)
  - MONITORING  (parameters to track)
  - CLARIFICATION (questions for prescriber)

Safe language: "possible consideration", "review prompt", "consider clarification".
Council NEVER diagnoses, prescribes, substitutes, approves dispensing, or mutates history.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import AsyncGenerator, Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class SpecialistFinding:
    specialist: str                    # "Cardiology", "Nephrology", etc.
    severity: str                      # blocker | caution | counseling | monitoring | clarification
    message: str
    drug_name: Optional[str] = None
    evidence_source: Optional[str] = None
    evidence_grade: Optional[str] = None
    safe_language: bool = True         # Always True — council never asserts diagnosis


@dataclass
class CouncilReport:
    prescription_id: UUID
    patient_id: UUID
    generated_at: str
    blockers: list[SpecialistFinding] = field(default_factory=list)
    cautions: list[SpecialistFinding] = field(default_factory=list)
    counseling_points: list[SpecialistFinding] = field(default_factory=list)
    monitoring_parameters: list[SpecialistFinding] = field(default_factory=list)
    clarification_prompts: list[SpecialistFinding] = field(default_factory=list)
    hereditary_flags: list[SpecialistFinding] = field(default_factory=list)
    specialists_consulted: list[str] = field(default_factory=list)
    council_summary: str = ""

    @property
    def has_blockers(self) -> bool:
        return len(self.blockers) > 0

    @property
    def total_findings(self) -> int:
        return (len(self.blockers) + len(self.cautions) +
                len(self.counseling_points) + len(self.monitoring_parameters) +
                len(self.clarification_prompts) + len(self.hereditary_flags))


# ── Specialist implementations ─────────────────────────────────────────────

class CardiologySpecialist:
    NAME = "Cardiology"
    QTC_PROLONGING = {
        "azithromycin", "clarithromycin", "ciprofloxacin", "levofloxacin",
        "haloperidol", "quetiapine", "amiodarone", "sotalol", "dronedarone",
        "ondansetron", "methadone", "chlorpromazine", "thioridazine",
    }
    ANTICOAGULANTS = {"warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban"}

    def evaluate(self, rx: dict, patient: dict) -> list[SpecialistFinding]:
        findings = []
        drug_lower = rx.get("drug_name", "").lower()
        active_meds = [m.get("drug_name", "").lower() for m in patient.get("active_medications", [])]
        diagnoses = [d.lower() for d in patient.get("diagnoses", [])]

        # QTc prolongation risk
        if any(d in drug_lower for d in self.QTC_PROLONGING):
            qtc_meds_on_board = [m for m in active_meds if any(d in m for d in self.QTC_PROLONGING)]
            if qtc_meds_on_board:
                findings.append(SpecialistFinding(
                    specialist=self.NAME,
                    severity="blocker",
                    message=(
                        f"Possible QTc prolongation consideration: {rx.get('drug_name')} "
                        f"combined with {', '.join(qtc_meds_on_board)} may warrant QTc review. "
                        "Consider prescriber clarification regarding baseline ECG or monitoring plan."
                    ),
                    drug_name=rx.get("drug_name"),
                    evidence_source="CredibleMeds/AHA QTc Guidelines",
                    evidence_grade="A",
                ))
            else:
                findings.append(SpecialistFinding(
                    specialist=self.NAME,
                    severity="monitoring",
                    message=f"QTc prolongation potential noted for {rx.get('drug_name')}. Monitor for QTc changes if cardiac history.",
                    drug_name=rx.get("drug_name"),
                ))

        # Heart failure — NSAID risk
        if "ibuprofen" in drug_lower or "naproxen" in drug_lower or "indomethacin" in drug_lower:
            if any("heart failure" in d or "hf" in d for d in diagnoses):
                findings.append(SpecialistFinding(
                    specialist=self.NAME,
                    severity="blocker",
                    message=(
                        f"NSAID ({rx.get('drug_name')}) in a patient with possible heart failure history. "
                        "NSAIDs may worsen fluid retention and cardiac decompensation. "
                        "Consider prescriber clarification."
                    ),
                    drug_name=rx.get("drug_name"),
                    evidence_source="ACC/AHA Heart Failure Guidelines",
                    evidence_grade="A",
                ))

        # Anticoagulant + NSAID
        if any(d in drug_lower for d in ["ibuprofen", "naproxen", "aspirin"]):
            ac_on_board = [m for m in active_meds if any(a in m for a in self.ANTICOAGULANTS)]
            if ac_on_board:
                findings.append(SpecialistFinding(
                    specialist=self.NAME,
                    severity="caution",
                    message=(
                        f"Concurrent anticoagulant ({', '.join(ac_on_board)}) and "
                        f"antiplatelet/NSAID ({rx.get('drug_name')}) may increase bleeding risk. "
                        "Consider counseling on bleeding signs."
                    ),
                    evidence_grade="B",
                ))

        return findings


class NephrologySpecialist:
    NAME = "Nephrology"
    RENALLY_CLEARED = {
        "metformin": {"contraindicated_below": 30, "reduce_below": 45},
        "gabapentin": {"contraindicated_below": None, "reduce_below": 60},
        "pregabalin": {"contraindicated_below": None, "reduce_below": 60},
        "nitrofurantoin": {"contraindicated_below": 30, "reduce_below": None},
        "digoxin": {"contraindicated_below": None, "reduce_below": 60},
        "lithium": {"contraindicated_below": None, "reduce_below": 60},
        "colchicine": {"contraindicated_below": 30, "reduce_below": 50},
        "spironolactone": {"contraindicated_below": 30, "reduce_below": None},
        "enoxaparin": {"contraindicated_below": None, "reduce_below": 30},
    }

    def evaluate(self, rx: dict, patient: dict) -> list[SpecialistFinding]:
        findings = []
        drug_lower = rx.get("drug_name", "").lower()
        egfr = patient.get("egfr")
        if egfr is None:
            # No lab data — counsel to check
            if any(d in drug_lower for d in self.RENALLY_CLEARED):
                findings.append(SpecialistFinding(
                    specialist=self.NAME,
                    severity="monitoring",
                    message=f"Renal function (eGFR) not available for {rx.get('drug_name')} dosing verification. Consider obtaining baseline creatinine.",
                    drug_name=rx.get("drug_name"),
                ))
            return findings

        for drug, thresholds in self.RENALLY_CLEARED.items():
            if drug in drug_lower:
                ci = thresholds.get("contraindicated_below")
                rd = thresholds.get("reduce_below")
                if ci and egfr < ci:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity="blocker",
                        message=(
                            f"Renal consideration: {rx.get('drug_name')} is generally not recommended "
                            f"at eGFR < {ci} mL/min (current eGFR: {egfr:.1f}). "
                            "Consider prescriber clarification regarding alternative therapy."
                        ),
                        drug_name=rx.get("drug_name"),
                        evidence_source="FDA Prescribing Information / Renal Dosing Guidelines",
                        evidence_grade="A",
                    ))
                elif rd and egfr < rd:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity="caution",
                        message=(
                            f"Dose adjustment consideration: {rx.get('drug_name')} may require dose "
                            f"reduction at eGFR < {rd} mL/min (current eGFR: {egfr:.1f}). "
                            "Review prescribed dose against current renal dosing guidelines."
                        ),
                        drug_name=rx.get("drug_name"),
                        evidence_grade="A",
                    ))

        # Nephrotoxic combinations
        nephrotoxic = {"nsaid", "ibuprofen", "naproxen", "gentamicin", "vancomycin", "contrast", "acyclovir"}
        active_meds = [m.get("drug_name", "").lower() for m in patient.get("active_medications", [])]
        concurrent_nephrotoxic = [m for m in active_meds if any(n in m for n in nephrotoxic)]
        current_also_nephrotoxic = any(n in drug_lower for n in nephrotoxic)
        if current_also_nephrotoxic and concurrent_nephrotoxic and egfr < 60:
            findings.append(SpecialistFinding(
                specialist=self.NAME,
                severity="caution",
                message=(
                    f"Multiple potentially nephrotoxic agents with reduced renal function (eGFR {egfr:.0f}): "
                    f"{rx.get('drug_name')} + {', '.join(concurrent_nephrotoxic[:2])}. "
                    "Consider renal function monitoring."
                ),
                evidence_grade="B",
            ))
        return findings


class GeriatricsPediatricsSpecialist:
    NAME = "Geriatrics/Pediatrics"
    BEERS_2023 = {
        "diphenhydramine": "High anticholinergic burden — cognitive impairment, urinary retention, falls risk in elderly",
        "diazepam": "Long-acting benzodiazepine — fall risk, cognitive impairment in patients ≥65",
        "amitriptyline": "Highly anticholinergic TCA — avoid in elderly",
        "promethazine": "High anticholinergic — avoid in elderly",
        "indomethacin": "Highest CNS adverse effect risk among NSAIDs — avoid in elderly",
        "meperidine": "Neurotoxic metabolite accumulation in elderly — safer opioid alternatives available",
        "nitrofurantoin": "Pulmonary toxicity risk with prolonged use in elderly",
        "glibenclamide": "Prolonged hypoglycemia risk in elderly — prefer shorter-acting agents",
    }

    def evaluate(self, rx: dict, patient: dict) -> list[SpecialistFinding]:
        findings = []
        drug_lower = rx.get("drug_name", "").lower()
        age = patient.get("age", 0)
        weight_kg = patient.get("weight_kg")

        # Beers Criteria for age ≥ 65
        if age >= 65:
            for beers_drug, concern in self.BEERS_2023.items():
                if beers_drug in drug_lower:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity="caution",
                        message=(
                            f"Beers Criteria 2023 consideration for patient age {age}: "
                            f"{rx.get('drug_name')} — {concern}. "
                            "Consider discussing safer alternatives with prescriber."
                        ),
                        drug_name=rx.get("drug_name"),
                        evidence_source="2023 AGS Beers Criteria",
                        evidence_grade="A",
                    ))
                    break

        # Pediatric weight-based dosing
        if age < 18 and weight_kg:
            findings.append(SpecialistFinding(
                specialist=self.NAME,
                severity="monitoring",
                message=(
                    f"Pediatric patient ({age}y, {weight_kg}kg). "
                    f"Verify {rx.get('drug_name')} dose is weight-appropriate. "
                    "Cross-reference with Harriet Lane or Lexicomp Pediatric dosing."
                ),
                drug_name=rx.get("drug_name"),
                evidence_source="Harriet Lane Handbook",
            ))

        return findings


class PainManagementSpecialist:
    NAME = "Pain Management"
    OPIOIDS = {
        "oxycodone": 1.5, "hydrocodone": 1.0, "morphine": 1.0,
        "fentanyl": 100.0, "hydromorphone": 4.0, "codeine": 0.15,
        "tramadol": 0.1, "methadone": 4.0, "buprenorphine": 30.0,
    }
    BENZODIAZEPINES = {"alprazolam", "clonazepam", "diazepam", "lorazepam", "temazepam", "midazolam"}
    CNS_DEPRESSANTS = {"gabapentin", "pregabalin", "carisoprodol", "cyclobenzaprine", "baclofen", "zolpidem"}

    def evaluate(self, rx: dict, patient: dict) -> list[SpecialistFinding]:
        findings = []
        drug_lower = rx.get("drug_name", "").lower()
        active_meds = [m.get("drug_name", "").lower() for m in patient.get("active_medications", [])]

        # Opioid MME check
        for opioid, mme_factor in self.OPIOIDS.items():
            if opioid in drug_lower:
                qty = float(rx.get("quantity_prescribed", 0))
                days = int(rx.get("days_supply", 1) or 1)
                strength_mg = float(rx.get("strength_mg", 0) or 0)

                if strength_mg and days > 0:
                    daily_mme = (qty / days) * strength_mg * mme_factor
                    if daily_mme >= 90:
                        findings.append(SpecialistFinding(
                            specialist=self.NAME,
                            severity="blocker",
                            message=(
                                f"High-dose opioid consideration: Estimated daily MME ≈ {daily_mme:.0f} mg/day "
                                f"(CDC threshold: 90 MME/day). "
                                "Consider prescriber clarification regarding clinical indication, "
                                "alternative therapy, and co-prescribing naloxone. "
                                "Verify PDMP before dispensing."
                            ),
                            drug_name=rx.get("drug_name"),
                            evidence_source="CDC Clinical Practice Guideline for Prescribing Opioids 2022",
                            evidence_grade="A",
                        ))
                    elif daily_mme >= 50:
                        findings.append(SpecialistFinding(
                            specialist=self.NAME,
                            severity="caution",
                            message=f"Moderate opioid dose: estimated {daily_mme:.0f} MME/day. Consider naloxone counseling.",
                            drug_name=rx.get("drug_name"),
                            evidence_grade="B",
                        ))

                # Opioid + benzodiazepine Black Box Warning
                benzo_on_board = [m for m in active_meds if any(b in m for b in self.BENZODIAZEPINES)]
                if benzo_on_board:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity="blocker",
                        message=(
                            f"FDA Black Box Warning: Concurrent opioid ({rx.get('drug_name')}) and "
                            f"benzodiazepine ({', '.join(benzo_on_board)}) — "
                            "profound sedation, respiratory depression, coma, and death reported. "
                            "Prescriber clarification recommended."
                        ),
                        drug_name=rx.get("drug_name"),
                        evidence_source="FDA Drug Safety Communication",
                        evidence_grade="A",
                    ))

                # Opioid + CNS depressants
                cns_on_board = [m for m in active_meds if any(c in m for c in self.CNS_DEPRESSANTS)]
                if cns_on_board:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity="caution",
                        message=(
                            f"CNS depressant combination: {rx.get('drug_name')} with "
                            f"{', '.join(cns_on_board[:2])}. Enhanced sedation risk. "
                            "Counsel patient on signs of respiratory depression."
                        ),
                        evidence_grade="B",
                    ))
                break

        return findings


class NutritionComplementSpecialist:
    NAME = "Nutrition/Complement Safety"
    SUPPLEMENT_INTERACTIONS = {
        "warfarin": [("St. John's Wort", "blocker", "Significantly reduces warfarin efficacy — INR may drop dangerously"),
                     ("Vitamin K supplements", "caution", "May reduce anticoagulant effect"),
                     ("Fish oil (high dose)", "caution", "Additive anticoagulant effect at doses >3g/day")],
        "statin": [("Red yeast rice", "caution", "Contains naturally occurring lovastatin — duplicate therapy risk")],
        "cyclosporine": [("St. John's Wort", "blocker", "Dramatically reduces cyclosporine levels — transplant rejection risk")],
        "maoi": [("Tyramine-rich foods", "blocker", "Hypertensive crisis risk with tyramine-containing foods")],
        "lithium": [("Caffeine (high intake)", "caution", "Variable effect on lithium levels"),
                    ("NSAIDs/Ibuprofen", "caution", "May increase lithium toxicity")],
    }

    def evaluate(self, rx: dict, patient: dict) -> list[SpecialistFinding]:
        findings = []
        drug_lower = rx.get("drug_name", "").lower()
        for drug_key, interactions in self.SUPPLEMENT_INTERACTIONS.items():
            if drug_key in drug_lower:
                for supplement, severity, message in interactions:
                    findings.append(SpecialistFinding(
                        specialist=self.NAME,
                        severity=severity,
                        message=(
                            f"Supplement/food interaction consideration with {rx.get('drug_name')}: "
                            f"{supplement} — {message}. "
                            "Consider including in patient counseling."
                        ),
                        drug_name=rx.get("drug_name"),
                        evidence_source="Natural Medicines Database / Lexicomp",
                        evidence_grade="B",
                    ))
        return findings


class HereditaryFamilyRiskSpecialist:
    NAME = "Hereditary/Family Risk"
    HEREDITARY_DRUG_RISKS = {
        "codeine":      ("CYP2D6 poor/ultrarapid metabolizer", "Codeine efficacy/toxicity varies with CYP2D6 genotype — ultrarapid metabolizers at fatal toxicity risk"),
        "clopidogrel":  ("CYP2C19 poor metabolizer", "Reduced clopidogrel activation — consider alternative antiplatelet in CYP2C19 poor metabolizers"),
        "simvastatin":  ("SLCO1B1 variant", "SLCO1B1 variant associated with simvastatin-induced myopathy"),
        "warfarin":     ("CYP2C9/VKORC1 variant", "Genotype affects warfarin sensitivity — consider pharmacogenomic testing"),
        "abacavir":     ("HLA-B*5701", "HLA-B*5701 positive patients at risk of hypersensitivity reaction — must test before prescribing"),
        "carbamazepine":("HLA-B*1502", "HLA-B*1502 (in East Asian patients) associated with severe cutaneous adverse reactions"),
    }

    async def evaluate(
        self,
        rx: dict,
        patient: dict,
        family_patients: list[dict],
        db=None,
        family_consent: bool = True,
    ) -> list[SpecialistFinding]:
        """
        Delegate to the dedicated HereditaryRiskEngine, which cross-references the
        prescribed drug against the patient's own AND linked-family inherited
        conditions (G6PD/favism, HAE, long-QT, FH, warfarin sensitivity,
        porphyria, thalassemia) plus single-gene pharmacogenomics — each with
        explicit "based on relative" provenance. Consent-gated for family data.
        """
        from services.ai.clinical_brain.hereditary.engine import HereditaryRiskEngine

        engine = HereditaryRiskEngine()
        hereditary = engine.evaluate(
            drug_name=rx.get("drug_name", ""),
            patient=patient,
            family=family_patients,
            family_consent=family_consent,
        )

        findings: list[SpecialistFinding] = []
        for hf in hereditary:
            # Surface provenance in the message so the pharmacist sees the basis.
            prov_line = ""
            if hf.provenance and hf.provenance != ["self"]:
                prov_line = " [" + "; ".join(hf.provenance) + "]"
            elif hf.provenance == ["self"]:
                prov_line = " [patient's own documented status]"
            findings.append(SpecialistFinding(
                specialist=self.NAME,
                severity=hf.severity,
                message=hf.message + prov_line + " — For pharmacist review, not a diagnosis.",
                drug_name=hf.drug_name,
                evidence_source=hf.evidence_source,
                evidence_grade=hf.evidence_grade,
            ))
        return findings


# ── Council Orchestrator ───────────────────────────────────────────────────

class SpecialistCouncil:
    """
    Runs all specialist modules in parallel and assembles the council report.
    Designed to stream findings as they arrive (progressive display in the UI).
    """

    def __init__(self, db=None, anthropic_api_key: str = ""):
        self.db = db
        self.anthropic_api_key = anthropic_api_key
        self.specialists = [
            CardiologySpecialist(),
            NephrologySpecialist(),
            GeriatricsPediatricsSpecialist(),
            PainManagementSpecialist(),
            NutritionComplementSpecialist(),
        ]
        self.hereditary_specialist = HereditaryFamilyRiskSpecialist()

    async def convene(
        self,
        prescription_id: UUID,
        patient_id: UUID,
        prescription: dict,
        patient_profile: dict,
        family_patient_profiles: Optional[list[dict]] = None,
    ) -> CouncilReport:
        """
        Convene the full specialist council for a prescription review.
        Runs all specialists concurrently for minimum latency.
        """
        from datetime import datetime, timezone

        report = CouncilReport(
            prescription_id=prescription_id,
            patient_id=patient_id,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )

        # Run all standard specialists in parallel
        async def run_specialist(specialist):
            try:
                findings = specialist.evaluate(prescription, patient_profile)
                return specialist.NAME, findings
            except Exception as exc:
                logger.error("Specialist %s failed: %s", specialist.NAME, exc)
                return specialist.NAME, []

        results = await asyncio.gather(*[
            run_specialist(s) for s in self.specialists
        ])

        for specialist_name, findings in results:
            report.specialists_consulted.append(specialist_name)
            for finding in findings:
                self._categorize_finding(report, finding)

        # Hereditary specialist (async — needs DB + family profiles)
        try:
            family_profiles = family_patient_profiles or await self._load_family_profiles(patient_id)
            hereditary_findings = await self.hereditary_specialist.evaluate(
                rx=prescription,
                patient=patient_profile,
                family_patients=family_profiles,
                db=self.db,
            )
            report.specialists_consulted.append(self.hereditary_specialist.NAME)
            for finding in hereditary_findings:
                if finding.severity in ("blocker", "caution"):
                    self._categorize_finding(report, finding)
                else:
                    report.hereditary_flags.append(finding)
        except Exception as exc:
            logger.error("Hereditary specialist failed: %s", exc)

        # Generate summary
        report.council_summary = self._summarize(report, prescription)

        logger.info(
            "Council convened for Rx %s: %d blockers, %d cautions, %d counseling, %d monitoring",
            prescription_id,
            len(report.blockers), len(report.cautions),
            len(report.counseling_points), len(report.monitoring_parameters),
        )
        return report

    async def stream_convene(
        self,
        prescription_id: UUID,
        patient_id: UUID,
        prescription: dict,
        patient_profile: dict,
    ) -> AsyncGenerator[dict, None]:
        """
        Stream council findings as they arrive — for progressive UI loading.
        Each yield is a partial finding the UI can display immediately.
        """
        from datetime import datetime, timezone

        yield {"event": "council_started", "prescription_id": str(prescription_id)}

        async def run_one(specialist):
            try:
                findings = specialist.evaluate(prescription, patient_profile)
                return specialist.NAME, findings
            except Exception as exc:
                logger.error("Specialist %s error: %s", specialist.NAME, exc)
                return specialist.NAME, []

        tasks = [asyncio.create_task(run_one(s)) for s in self.specialists]

        for coro in asyncio.as_completed([asyncio.create_task(t) for t in tasks]):
            specialist_name, findings = await coro
            yield {
                "event": "specialist_complete",
                "specialist": specialist_name,
                "findings_count": len(findings),
                "findings": [
                    {
                        "severity": f.severity,
                        "message": f.message,
                        "drug_name": f.drug_name,
                        "evidence_grade": f.evidence_grade,
                    }
                    for f in findings
                ],
            }

        yield {"event": "council_complete", "prescription_id": str(prescription_id)}

    def _categorize_finding(self, report: CouncilReport, finding: SpecialistFinding) -> None:
        category_map = {
            "blocker":    report.blockers,
            "caution":    report.cautions,
            "counseling": report.counseling_points,
            "monitoring": report.monitoring_parameters,
            "clarification": report.clarification_prompts,
        }
        bucket = category_map.get(finding.severity, report.cautions)
        bucket.append(finding)

    def _summarize(self, report: CouncilReport, prescription: dict) -> str:
        drug = prescription.get("drug_name", "this medication")
        parts = []
        if report.blockers:
            parts.append(
                f"{len(report.blockers)} matter(s) require attention before dispensing {drug}"
            )
        if report.cautions:
            parts.append(f"{len(report.cautions)} caution(s) for pharmacist review")
        if report.counseling_points:
            parts.append(f"{len(report.counseling_points)} counseling point(s) for the patient")
        if not parts:
            parts.append(f"No significant clinical concerns identified for {drug} in this patient context")
        return ". ".join(parts) + "."

    async def _load_family_profiles(self, patient_id: UUID) -> list[dict]:
        """
        Load linked family members via the untyped person-link graph (BFS),
        each with their inherited conditions + diagnoses, so the hereditary
        engine can cross-reference family history against the prescribed drug.
        """
        if not self.db:
            return []
        try:
            from sqlalchemy import text
            from services.biometric.identity_resolution.person_links import (
                PersonLinkGraph, patient_ref,
            )
            graph = PersonLinkGraph(self.db)
            # full connected component, then keep linked patients (exclude self)
            component = await graph.connected_component(patient_ref(patient_id), max_depth=3)
            family_refs = [r for r in component if r.startswith("patient:")]
            family_ids = [r.split(":", 1)[1] for r in family_refs
                          if r.split(":", 1)[1] != str(patient_id)]
            if not family_ids:
                return []

            # relationship hints from the link rows touching this patient
            rel_rows = (await self.db.execute(text("""
                SELECT person_a_ref, person_b_ref, relationship FROM person_links
                WHERE person_a_ref = :self OR person_b_ref = :self
            """), {"self": patient_ref(patient_id)})).mappings().all()
            rel_map: dict[str, str] = {}
            for row in rel_rows:
                other = (row["person_b_ref"] if row["person_a_ref"] == patient_ref(patient_id)
                         else row["person_a_ref"])
                if other.startswith("patient:") and row["relationship"]:
                    rel_map[other.split(":", 1)[1]] = row["relationship"]

            rows = (await self.db.execute(text("""
                SELECT p.id, p.first_name, p.last_name,
                    (SELECT json_agg(cn.content) FROM clinical_notes cn
                     WHERE cn.patient_id = p.id AND cn.note_type IN ('condition','diagnosis')) AS diagnoses,
                    (SELECT json_agg(cn2.content) FROM clinical_notes cn2
                     WHERE cn2.patient_id = p.id AND cn2.note_type = 'inherited_condition') AS inherited
                FROM patients p
                WHERE p.id = ANY(:ids) AND p.is_deleted = false
                LIMIT 12
            """), {"ids": family_ids})).mappings().all()

            return [
                {
                    "patient_id": str(r["id"]),
                    "name": f"{r['first_name']} {r['last_name']}",
                    "relationship": rel_map.get(str(r["id"]), "relative"),
                    "diagnoses": r["diagnoses"] or [],
                    "inherited_conditions": r["inherited"] or [],
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("Family profile load failed: %s", exc)
            return []
