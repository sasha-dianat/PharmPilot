"""
AI Clinical Brain — Orchestrator
Routes clinical queries to appropriate specialty modules and synthesizes results.
Uses Claude claude-opus-4-8 as the synthesis LLM for clinical reasoning.
"""
import logging
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

import anthropic

logger = logging.getLogger(__name__)

CLINICAL_SYSTEM_PROMPT = """You are PharmPilot's Clinical Brain, an advanced pharmaceutical AI assistant
supporting licensed pharmacists. You have expertise equivalent to a clinical pharmacist with board
certifications in multiple specialties including pharmacotherapy, cardiology, endocrinology, psychiatry,
oncology, nephrology, infectious disease, geriatrics, and pediatrics.

Your role is to:
1. Identify drug safety issues, interactions, and clinical risks
2. Provide evidence-based dosing recommendations
3. Flag clinically significant concerns the pharmacist should address
4. Suggest monitoring parameters and follow-up actions
5. Synthesize information from multiple specialty modules into a coherent recommendation

CRITICAL RULES:
- Every output requires pharmacist review and approval before action
- You do not diagnose — you support pharmacist clinical decision-making
- You do not prescribe — you evaluate existing prescriptions for safety
- Always cite the evidence source (guideline, drug label, study) and grade
- Express uncertainty clearly — never overstate confidence
- For controlled substances, apply additional scrutiny
- Flag any situation where patient safety may be immediately at risk"""


@dataclass
class PatientContextSummary:
    patient_id: UUID
    age: int
    gender: str
    weight_kg: Optional[float]
    egfr: Optional[float]
    hepatic_function: Optional[str]
    active_medications: list[dict]
    allergies: list[dict]
    diagnoses: list[str]
    recent_labs: dict
    pregnancy_status: Optional[str]
    pharmacogenomics: Optional[dict]


@dataclass
class ClinicalBrainResponse:
    consultation_id: UUID
    primary_finding: str
    severity: str                      # critical, high, moderate, informational
    findings: list[dict] = field(default_factory=list)
    recommendations: list[dict] = field(default_factory=list)
    monitoring_parameters: list[str] = field(default_factory=list)
    patient_counseling_points: list[str] = field(default_factory=list)
    prescriber_contact_recommended: bool = False
    prescriber_contact_reason: Optional[str] = None
    evidence_citations: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    requires_pharmacist_action: bool = True
    raw_llm_response: Optional[str] = None


class ClinicalBrainOrchestrator:
    """
    Primary entry point for all clinical AI consultations.
    Combines rule-based specialty module analysis with LLM reasoning.
    """

    def __init__(self, anthropic_api_key: str, drug_db_client=None):
        self._client = anthropic.Anthropic(api_key=anthropic_api_key)
        self._drug_db = drug_db_client
        self._specialty_modules = self._load_specialty_modules()

    def _load_specialty_modules(self) -> dict:
        from services.ai.clinical_brain.specialties.drug_safety import DrugSafetyModule
        from services.ai.clinical_brain.specialties.renal import RenalDosingModule
        from services.ai.clinical_brain.specialties.geriatrics import GeriatricsModule
        from services.ai.clinical_brain.specialties.pain import PainManagementModule

        return {
            "drug_safety": DrugSafetyModule(),
            "renal": RenalDosingModule(),
            "geriatrics": GeriatricsModule(),
            "pain": PainManagementModule(),
        }

    async def comprehensive_rx_review(
        self,
        new_prescription: dict,
        patient: PatientContextSummary,
    ) -> ClinicalBrainResponse:
        """
        Full clinical review of a new prescription in the context of the patient's profile.
        Called automatically when a new Rx enters the verification queue.
        """
        from uuid import uuid4

        consultation_id = uuid4()
        module_findings: list[dict] = []

        # Run applicable specialty modules
        if patient.egfr is not None and patient.egfr < 60:
            renal_result = self._specialty_modules["renal"].evaluate(
                drug=new_prescription, egfr=patient.egfr
            )
            if renal_result:
                module_findings.append({"module": "renal", "finding": renal_result})

        if patient.age >= 65:
            beers_result = self._specialty_modules["geriatrics"].beers_check(
                drug=new_prescription, patient_conditions=patient.diagnoses
            )
            if beers_result:
                module_findings.append({"module": "geriatrics", "finding": beers_result})

        if new_prescription.get("dea_schedule") in ("CII", "CIII", "CIV", "CV"):
            pain_result = self._specialty_modules["pain"].evaluate(
                new_prescription=new_prescription,
                active_medications=patient.active_medications,
                patient_age=patient.age,
            )
            if pain_result:
                module_findings.append({"module": "pain_management", "finding": pain_result})

        drug_safety_result = self._specialty_modules["drug_safety"].check_interactions(
            new_drug=new_prescription,
            active_medications=patient.active_medications,
            allergies=patient.allergies,
        )
        if drug_safety_result:
            module_findings.extend([{"module": "drug_safety", "finding": f} for f in drug_safety_result])

        # LLM synthesis
        llm_response = await self._synthesize_with_llm(
            new_prescription=new_prescription,
            patient=patient,
            module_findings=module_findings,
        )

        return self._parse_llm_response(consultation_id, llm_response, module_findings)

    async def _synthesize_with_llm(
        self,
        new_prescription: dict,
        patient: PatientContextSummary,
        module_findings: list[dict],
    ) -> str:
        patient_summary = f"""
Patient Context:
- Age: {patient.age}, Gender: {patient.gender}
- Weight: {patient.weight_kg} kg
- eGFR: {patient.egfr} mL/min/1.73m² {"(RENAL IMPAIRMENT)" if patient.egfr and patient.egfr < 60 else ""}
- Hepatic Function: {patient.hepatic_function or "Not documented"}
- Pregnancy Status: {patient.pregnancy_status or "Not documented"}

Active Medications ({len(patient.active_medications)}):
{chr(10).join(f"  - {m.get('drug_name', 'Unknown')} {m.get('dose', '')} {m.get('frequency', '')}" for m in patient.active_medications)}

Allergies:
{chr(10).join(f"  - {a.get('allergen_name', 'Unknown')}: {a.get('reaction', 'reaction not documented')}" for a in patient.allergies) or "  None documented"}

Active Diagnoses:
{chr(10).join(f"  - {d}" for d in patient.diagnoses) or "  None documented"}

Recent Labs:
{chr(10).join(f"  - {k}: {v}" for k, v in patient.recent_labs.items()) if patient.recent_labs else "  None available"}
"""

        new_rx_summary = f"""
New Prescription Being Evaluated:
- Drug: {new_prescription.get('drug_name')} {new_prescription.get('strength', '')}
- NDC: {new_prescription.get('ndc', 'Not provided')}
- Sig: {new_prescription.get('sig_text', 'Not provided')}
- Days Supply: {new_prescription.get('days_supply')}
- DEA Schedule: {new_prescription.get('dea_schedule', 'Non-controlled')}
- Prescriber Specialty: {new_prescription.get('prescriber_specialty', 'Not documented')}
"""

        module_summary = ""
        if module_findings:
            module_summary = "\nSpecialty Module Pre-Analysis:\n"
            for finding in module_findings:
                module_summary += f"  [{finding['module'].upper()}]: {finding['finding']}\n"

        user_message = f"""Please perform a comprehensive clinical review of this new prescription.

{patient_summary}

{new_rx_summary}
{module_summary}

Provide your analysis in the following structure:
1. SEVERITY ASSESSMENT: (critical/high/moderate/informational)
2. PRIMARY FINDING: One sentence summary of the most important concern
3. DETAILED FINDINGS: All clinically relevant issues, each with evidence grade
4. RECOMMENDATIONS: Specific actionable steps for the pharmacist
5. MONITORING PARAMETERS: What to monitor and at what intervals
6. PATIENT COUNSELING POINTS: What to tell the patient
7. PRESCRIBER CONTACT: Yes/No and reason if yes
8. EVIDENCE CITATIONS: Guideline or reference for each finding"""

        response = self._client.messages.create(
            model="claude-opus-4-8",
            max_tokens=2048,
            system=CLINICAL_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        return response.content[0].text

    def _parse_llm_response(
        self,
        consultation_id,
        llm_text: str,
        module_findings: list[dict],
    ) -> ClinicalBrainResponse:
        import re

        severity = "moderate"
        severity_match = re.search(
            r"SEVERITY ASSESSMENT:\s*(critical|high|moderate|informational)",
            llm_text, re.IGNORECASE
        )
        if severity_match:
            severity = severity_match.group(1).lower()

        primary_finding = ""
        pf_match = re.search(r"PRIMARY FINDING:\s*(.+?)(?:\n|3\.)", llm_text, re.IGNORECASE | re.DOTALL)
        if pf_match:
            primary_finding = pf_match.group(1).strip()[:500]

        prescriber_contact = bool(re.search(r"PRESCRIBER CONTACT:\s*yes", llm_text, re.IGNORECASE))
        prescriber_reason = None
        if prescriber_contact:
            pc_match = re.search(r"PRESCRIBER CONTACT:\s*yes[,\s]*(.+?)(?:\n8\.|\Z)", llm_text, re.IGNORECASE | re.DOTALL)
            if pc_match:
                prescriber_reason = pc_match.group(1).strip()[:300]

        return ClinicalBrainResponse(
            consultation_id=consultation_id,
            primary_finding=primary_finding,
            severity=severity,
            findings=[{"source": "llm_synthesis", "content": llm_text}] + module_findings,
            prescriber_contact_recommended=prescriber_contact,
            prescriber_contact_reason=prescriber_reason,
            confidence=0.90 if module_findings else 0.75,
            requires_pharmacist_action=severity in ("critical", "high"),
            raw_llm_response=llm_text,
        )
