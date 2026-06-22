from __future__ import annotations

import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dataclasses import asdict

from services.ai.clinical_decision_support import engine
from services.ai.clinical_decision_support.interaction.engine import evaluate as eval_interactions
from services.ai.clinical_decision_support.interaction.review_set import build_review_set
from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.clinical_decision_support.schema import (
    CDSContext,
    CDSMedication,
    LabValue,
    PHARMACIST_VERIFICATION_NOTICE,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAlert, ClinicalAuditLog, Medication
from shared.models.patient import LabResult, Patient, PatientAllergy
from shared.models.prescription import Prescription, RxStatus

router = APIRouter()


class MedicationInput(BaseModel):
    drug_name: str
    strength: str | None = None
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    source: str | None = "request"


class CDSEvaluateRequest(BaseModel):
    patient_id: UUID
    medications: list[MedicationInput] | None = None


def _age_from_dob(dob: date | None, today: date | None = None) -> int | None:
    if not dob:
        return None
    today = today or date.today()
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def _jsonable(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _medication_entry(drug_name: str, source: str | None = None) -> CDSMedication:
    normalized = normalize(drug_name)
    return CDSMedication(
        drug_name=drug_name,
        normalized_name=normalized,
        classes=classes_of(normalized),
        source=source,
    )


def _lab_key(test_name: str) -> str:
    return re.sub(r"\s+", " ", test_name.strip().lower())


def _context_snapshot(patient: Patient, context: CDSContext) -> dict:
    return {
        "patient_id": str(patient.id),
        "age": context.age,
        "weight_kg": context.weight_kg,
        "pregnancy_status": context.pregnancy_status,
        "renal_function": context.renal_function,
        "hepatic_status": context.hepatic_status,
        "conditions": context.conditions,
        "allergies": context.allergies,
        "medications": [
            {
                "drug_name": med.drug_name,
                "normalized_name": med.normalized_name,
                "classes": sorted(med.classes),
                "source": med.source,
            }
            for med in context.medications
        ],
        "labs": {
            key: {
                "value": lab.value,
                "unit": lab.unit,
                "collected_at": lab.collected_at,
            }
            for key, lab in context.labs.items()
        },
    }


async def _load_context(
    *,
    db: AsyncSession,
    patient: Patient,
    pharmacy_id: UUID,
    requested_medications: list[MedicationInput] | None,
) -> CDSContext:
    if requested_medications is not None:
        medications = [
            _medication_entry(med.drug_name, med.source or "request")
            for med in requested_medications
            if med.drug_name.strip()
        ]
    else:
        med_result = await db.execute(
            select(Medication).where(
                Medication.patient_id == patient.id,
                Medication.pharmacy_id == pharmacy_id,
                Medication.status == "active",
                Medication.is_deleted == False,  # noqa: E712
            )
        )
        medication_rows = med_result.scalars().all()
        medications = [
            _medication_entry(row.normalized_name or row.drug_name, row.source)
            for row in medication_rows
        ]

        active_rx_statuses = [
            status.value for status in RxStatus
            if status not in (
                RxStatus.DISPENSED,
                RxStatus.CANCELLED,
                RxStatus.RETURNED_TO_STOCK,
                RxStatus.TRANSFERRED_OUT,
            )
        ]
        rx_result = await db.execute(
            select(Prescription).where(
                Prescription.patient_id == patient.id,
                Prescription.pharmacy_id == pharmacy_id,
                Prescription.status.in_(active_rx_statuses),
                Prescription.is_deleted == False,  # noqa: E712
            )
        )
        for rx in rx_result.scalars().all():
            medications.append(_medication_entry(rx.drug_name, "prescription"))

    allergy_result = await db.execute(
        select(PatientAllergy).where(
            PatientAllergy.patient_id == patient.id,
            PatientAllergy.is_deleted == False,  # noqa: E712
        )
    )
    allergies = [allergy.allergen_name for allergy in allergy_result.scalars().all()]

    lab_result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_id == patient.id, LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc())
    )
    labs: dict[str, LabValue] = {}
    for lab in lab_result.scalars().all():
        key = _lab_key(lab.test_name)
        if key in labs:
            continue
        labs[key] = LabValue(
            value=_to_float(lab.value),
            unit=lab.unit,
            collected_at=lab.result_date.isoformat() if lab.result_date else None,
        )

    return CDSContext(
        age=_age_from_dob(getattr(patient, "date_of_birth", None)),
        weight_kg=_to_float(getattr(patient, "weight_kg", None)),
        pregnancy_status=getattr(patient, "pregnancy_status", None),
        renal_function=getattr(patient, "renal_function", None),
        hepatic_status=getattr(patient, "hepatic_status", None),
        conditions=getattr(patient, "conditions", None) or [],
        allergies=allergies,
        medications=medications,
        labs=labs,
    )


@router.post("/evaluate")
async def evaluate_cds(
    body: CDSEvaluateRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == body.patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    context = await _load_context(
        db=db,
        patient=patient,
        pharmacy_id=staff.pharmacy_id,
        requested_medications=body.medications,
    )
    alerts = engine.evaluate(context)
    evaluated_at = datetime.now(timezone.utc)

    for alert in alerts:
        db.add(
            ClinicalAlert(
                pharmacy_id=staff.pharmacy_id,
                patient_id=patient.id,
                module="cds",
                rule_id=alert["rule_id"],
                severity=alert["severity"],
                title=alert["title"],
                explanation=f"{alert['clinical_problem']} {alert['mechanism']}",
                patient_specific_factors=alert["patient_specific_factors"],
                missing_data=alert["missing_information"],
                suggested_actions=alert["suggested_pharmacist_actions"],
                evidence_sources=alert["evidence_sources"],
                confidence=alert["confidence"],
                status="active",
                created_by=staff.id,
                updated_by=staff.id,
            )
        )

    output_snapshot = {"alerts": alerts}
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="cds",
            input_snapshot=_jsonable(_context_snapshot(patient, context)),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[alert["rule_id"] for alert in alerts],
            model_version=engine.MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    missing_summary = sorted({
        item for alert in alerts for item in alert["missing_information"]
    })
    response = {
        "patient_id": str(patient.id),
        "evaluated_at": evaluated_at.isoformat(),
        "model_version": engine.MODEL_VERSION,
        "alerts": alerts,
        "missing_information_summary": missing_summary,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }
    if not context.medications:
        response["note"] = "No active medications available for CDS evaluation."
    return response


class InteractionReportRequest(BaseModel):
    patient_id: UUID
    rx_ids: list[UUID] | None = None


def _finding_json(f) -> dict:
    d = asdict(f)
    d["severity"] = f.severity.value
    d["base_severity"] = f.base_severity.value
    return d


@router.post("/interaction-report")
async def interaction_report(
    body: InteractionReportRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Patient).where(
            Patient.id == body.patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = result.scalar_one_or_none()
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    review_set = await build_review_set(
        db=db, patient=patient, pharmacy_id=staff.pharmacy_id, rx_ids=body.rx_ids)
    report = eval_interactions(review_set)
    return {
        "summary": report.summary,
        "degraded": report.degraded,
        "findings": [_finding_json(f) for f in report.findings],
        "pharmacist_verification_notice": (
            report.findings[0].pharmacist_verification_notice if report.findings
            else "Advisory clinical decision support only."),
    }


from datetime import timezone

from services.ai.clinical_decision_support.interaction.precompute import recompute_and_cache
from services.ai.clinical_decision_support.interaction.report import report_to_dict
from services.ai.clinical_decision_support.interaction.review_set import (
    build_review_set as _build_rs, review_set_hash,
)
from shared.models.clinical import InteractionReportCache
from shared.models.prescriber import Prescriber


@router.get("/interaction-report/{patient_id}")
async def get_interaction_report(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = (await db.execute(select(Patient).where(
        Patient.id == patient_id, Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    rs = await _build_rs(db=db, patient=patient, pharmacy_id=staff.pharmacy_id)
    rsh = review_set_hash(rs)
    cache = (await db.execute(select(InteractionReportCache).where(
        InteractionReportCache.patient_id == patient_id,
        InteractionReportCache.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
    if cache and cache.review_set_hash == rsh:
        return {"report": cache.report, "findings_hash": cache.findings_hash,
                "review_set_hash": rsh, "cached": True,
                "computed_at": cache.computed_at.isoformat()}
    _report, rsh2, fh = await recompute_and_cache(
        db=db, patient=patient, pharmacy_id=staff.pharmacy_id, rs=rs)
    return {"report": report_to_dict(_report), "findings_hash": fh,
            "review_set_hash": rsh2, "cached": False,
            "computed_at": datetime.now(timezone.utc).isoformat()}


class InteractionAckRequest(BaseModel):
    patient_id: UUID
    rx_id: UUID | None = None
    findings_hash: str
    acknowledged: list[dict]


def _ack_snapshot(*, staff, patient, prescription, prescriber, acknowledged) -> dict:
    return {
        "pharmacist": {"id": str(staff.id),
                       "name": f"{getattr(staff,'first_name','')} {getattr(staff,'last_name','')}".strip(),
                       "license": getattr(staff, "pharmacist_license_number", None)},
        "physician": {
            "name": (f"{prescriber.first_name} {prescriber.last_name}" if prescriber else None),
            "medical_council_id": getattr(prescriber, "medical_council_id", None) if prescriber else None,
            "specialty": getattr(prescriber, "specialty", None) if prescriber else None,
        },
        "patient": {"id": str(patient.id),
                    "name": f"{getattr(patient,'first_name','')} {getattr(patient,'last_name','')}".strip(),
                    "national_id": getattr(patient, "national_id", None)},
        "prescription": {"rx_id": (str(prescription.id) if prescription else None),
                         "drug_name": getattr(prescription, "drug_name", None) if prescription else None},
        "findings": list(acknowledged),
    }


@router.post("/interaction-ack")
async def acknowledge_interactions(
    body: InteractionAckRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient = (await db.execute(select(Patient).where(
        Patient.id == body.patient_id, Patient.pharmacy_id == staff.pharmacy_id,
        Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")

    prescription = prescriber = None
    if body.rx_id:
        prescription = (await db.execute(select(Prescription).where(
            Prescription.id == body.rx_id,
            Prescription.pharmacy_id == staff.pharmacy_id))).scalar_one_or_none()
        if prescription:
            prescriber = (await db.execute(select(Prescriber).where(
                Prescriber.id == prescription.prescriber_id))).scalar_one_or_none()

    snapshot = _ack_snapshot(staff=staff, patient=patient, prescription=prescription,
                             prescriber=prescriber, acknowledged=body.acknowledged)
    audit = ClinicalAuditLog(
        user_id=staff.id, patient_id=patient.id, module="interaction_acknowledgment",
        input_snapshot=_jsonable(snapshot),
        output_snapshot=_jsonable({"acknowledged_by": str(staff.id),
                                   "acknowledged_at": datetime.now(timezone.utc).isoformat(),
                                   "findings_hash": body.findings_hash}),
        rules_triggered=[a.get("rule_id") for a in body.acknowledged],
        model_version="interaction-v1", created_by=staff.id, updated_by=staff.id)
    db.add(audit)
    await db.flush()
    return {"audit_id": str(audit.id), "findings_hash": body.findings_hash}
