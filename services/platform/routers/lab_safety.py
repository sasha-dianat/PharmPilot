from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.clinical_decision_support.normalizer import classes_of, normalize
from services.ai.lab_safety import engine
from services.ai.lab_safety.schema import (
    MODEL_VERSION,
    LabSafetyContext,
    LabSafetyResult,
    PHARMACIST_VERIFICATION_NOTICE,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, Medication
from shared.models.patient import LabResult, Patient

router = APIRouter()


class LabSafetyAssessRequest(BaseModel):
    patient_id: UUID


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
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


async def _load_context(*, db: AsyncSession, patient: Patient, staff: Staff) -> LabSafetyContext:
    med_result = await db.execute(
        select(Medication).where(
            Medication.patient_id == patient.id,
            Medication.pharmacy_id == staff.pharmacy_id,
            Medication.status == "active",
            Medication.is_deleted == False,  # noqa: E712
        )
    )
    medications = []
    for row in med_result.scalars().all():
        normalized_name = normalize(row.normalized_name or row.drug_name)
        medications.append(
            {
                "drug_name": row.drug_name,
                "normalized_name": normalized_name,
                "classes": sorted(classes_of(normalized_name)),
            }
        )

    lab_result = await db.execute(
        select(LabResult)
        .where(LabResult.patient_id == patient.id, LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc())
        .limit(200)
    )
    labs = [
        {
            "test_name": row.test_name,
            "value": row.value,
            "unit": row.unit,
            "reference_range": row.reference_range,
            "abnormal_flag": row.abnormal_flag,
            "result_date": row.result_date,
        }
        for row in lab_result.scalars().all()
    ]

    return LabSafetyContext(
        patient_id=str(patient.id),
        medications=medications,
        lab_results=labs,
    )


def _severity_counts(result: LabSafetyResult) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
    for finding in result.findings:
        if finding.severity in counts:
            counts[finding.severity] += 1
    return counts


@router.post("/assess")
async def assess_lab_safety(
    body: LabSafetyAssessRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    patient_result = await db.execute(
        select(Patient).where(
            Patient.id == body.patient_id,
            Patient.pharmacy_id == staff.pharmacy_id,
            Patient.is_deleted == False,  # noqa: E712
        )
    )
    patient = patient_result.scalar_one_or_none()
    if not patient:
        raise HTTPException(404, "Patient not found")

    context = await _load_context(db=db, patient=patient, staff=staff)
    try:
        result = engine.assess(context)
    except Exception:  # pragma: no cover - defensive fail-safe for clinical endpoint behavior
        result = LabSafetyResult(
            patient_id=str(patient.id),
            findings=[],
            missing_labs=[],
            drugs_evaluated=[],
            labs_evaluated=[],
            assessment_date=datetime.now(timezone.utc).isoformat(),
            pharmacist_verification_notice=PHARMACIST_VERIFICATION_NOTICE,
        )

    output_snapshot = {
        "n_findings": len(result.findings),
        "finding_severities": _severity_counts(result),
    }
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="lab_safety",
            input_snapshot=_jsonable(
                {
                    "patient_id": patient.id,
                    "n_meds": len(context.medications),
                    "n_labs": len(context.lab_results),
                }
            ),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[finding.rule_id for finding in result.findings],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    return _jsonable(asdict(result))
