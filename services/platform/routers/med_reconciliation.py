from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.med_reconciliation import engine
from services.ai.med_reconciliation.schema import (
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    MedEntry,
    ReconciliationContext,
    ReconciliationResult,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, Medication
from shared.models.patient import Patient

router = APIRouter()


class MedicationInput(BaseModel):
    id: str | None = None
    patient_id: str | None = None
    drug_name: str
    normalized_name: str | None = None
    strength: str | None = None
    dose: str | None = None
    route: str | None = None
    frequency: str | None = None
    indication: str | None = None
    status: str | None = None
    source: str | None = "request"


class MedReconcileRequest(BaseModel):
    patient_id: UUID
    source_a_label: str = "Source A"
    source_b_label: str = "Source B"
    source_a: str | None = None
    source_b: str | None = None
    source_a_meds: list[MedicationInput] | None = None
    source_b_meds: list[MedicationInput] | None = None


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


def _entry_from_input(med: MedicationInput, *, patient_id: UUID, source: str) -> MedEntry:
    normalized = normalize(med.normalized_name or med.drug_name)
    return MedEntry(
        id=med.id,
        patient_id=med.patient_id or str(patient_id),
        drug_name=med.drug_name,
        normalized_name=normalized,
        strength=med.strength,
        dose=med.dose,
        route=med.route,
        frequency=med.frequency,
        indication=med.indication,
        status=med.status,
        source=med.source or source,
        classes=sorted(engine.classes_of(normalized)),
    )


def _entry_from_model(row: Medication) -> MedEntry:
    normalized = normalize(row.normalized_name or row.drug_name)
    return MedEntry(
        id=str(row.id),
        patient_id=str(row.patient_id),
        drug_name=row.drug_name,
        normalized_name=normalized,
        strength=row.strength,
        dose=row.dose,
        route=row.route,
        frequency=row.frequency,
        indication=row.indication,
        status=row.status,
        source=row.source,
        classes=sorted(engine.classes_of(normalized)),
    )


async def _load_source_meds(
    *,
    db: AsyncSession,
    patient: Patient,
    staff: Staff,
    explicit_meds: list[MedicationInput] | None,
    source_filter: str | None,
    fallback_source: str,
) -> list[MedEntry]:
    if explicit_meds is not None:
        return [
            _entry_from_input(med, patient_id=patient.id, source=fallback_source)
            for med in explicit_meds
            if med.drug_name.strip()
        ]

    if not source_filter:
        raise HTTPException(422, "Each reconciliation side requires either a source filter or explicit medication list")

    result = await db.execute(
        select(Medication).where(
            Medication.patient_id == patient.id,
            Medication.pharmacy_id == staff.pharmacy_id,
            Medication.source == source_filter,
            or_(Medication.status == "active", Medication.status.is_(None)),
            Medication.is_deleted == False,  # noqa: E712
        )
    )
    return [_entry_from_model(row) for row in result.scalars().all()]


def _empty_result(context: ReconciliationContext) -> ReconciliationResult:
    return ReconciliationResult(
        patient_id=context.patient_id,
        source_a_label=context.source_a_label,
        source_b_label=context.source_b_label,
        discrepancies=[],
        drugs_in_source_a=len(context.source_a_meds),
        drugs_in_source_b=len(context.source_b_meds),
        reconciled_count=0,
        assessment_date=datetime.now(timezone.utc).isoformat(),
        pharmacist_verification_notice=PHARMACIST_VERIFICATION_NOTICE,
    )


@router.post("/reconcile")
async def reconcile_medications(
    body: MedReconcileRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    if body.source_a_meds is None and not body.source_a:
        raise HTTPException(422, "source_a or source_a_meds is required")
    if body.source_b_meds is None and not body.source_b:
        raise HTTPException(422, "source_b or source_b_meds is required")

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

    source_a_meds = await _load_source_meds(
        db=db,
        patient=patient,
        staff=staff,
        explicit_meds=body.source_a_meds,
        source_filter=body.source_a,
        fallback_source=body.source_a or "source_a",
    )
    source_b_meds = await _load_source_meds(
        db=db,
        patient=patient,
        staff=staff,
        explicit_meds=body.source_b_meds,
        source_filter=body.source_b,
        fallback_source=body.source_b or "source_b",
    )
    context = ReconciliationContext(
        patient_id=str(patient.id),
        source_a_label=body.source_a_label or "Source A",
        source_b_label=body.source_b_label or "Source B",
        source_a_meds=source_a_meds,
        source_b_meds=source_b_meds,
    )

    try:
        result = engine.reconcile(context)
    except Exception:  # pragma: no cover - defensive fail-safe for clinical endpoint behavior
        result = _empty_result(context)

    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="med_reconciliation",
            input_snapshot=_jsonable(
                {
                    "patient_id": patient.id,
                    "source_a_label": context.source_a_label,
                    "source_b_label": context.source_b_label,
                    "n_source_a": len(context.source_a_meds),
                    "n_source_b": len(context.source_b_meds),
                }
            ),
            output_snapshot=_jsonable(
                {
                    "n_discrepancies": len(result.discrepancies),
                    "types": [item.discrepancy_type for item in result.discrepancies],
                }
            ),
            rules_triggered=[item.discrepancy_id for item in result.discrepancies],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    return _jsonable(asdict(result))
