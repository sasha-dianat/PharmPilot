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

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.pharmacogenomics import engine
from services.ai.pharmacogenomics.schema import (
    MODEL_VERSION,
    PHARMACIST_VERIFICATION_NOTICE,
    PGxContext,
    PGxGenotype,
    PGxResult,
)
from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from shared.models.auth import Staff
from shared.models.clinical import ClinicalAuditLog, GenotypeResult, Medication
from shared.models.patient import Patient

router = APIRouter()


class GenotypeInput(BaseModel):
    gene: str
    diplotype: str | None = None
    phenotype: str | None = None
    source: str | None = "request"


class PGxInterpretRequest(BaseModel):
    patient_id: UUID
    drugs: list[str] | None = None
    genotypes: list[GenotypeInput] | None = None


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


async def _load_context(
    *,
    db: AsyncSession,
    patient: Patient,
    staff: Staff,
    body: PGxInterpretRequest,
) -> PGxContext:
    if body.genotypes is not None:
        genotypes = [
            PGxGenotype(
                gene=item.gene,
                diplotype=item.diplotype,
                phenotype=item.phenotype,
                source=item.source or "request",
            )
            for item in body.genotypes
            if item.gene.strip()
        ]
    else:
        genotype_result = await db.execute(
            select(GenotypeResult).where(
                GenotypeResult.patient_id == patient.id,
                GenotypeResult.pharmacy_id == staff.pharmacy_id,
                GenotypeResult.is_deleted == False,  # noqa: E712
            )
        )
        genotypes = [
            PGxGenotype(
                gene=row.gene,
                diplotype=row.diplotype,
                phenotype=row.phenotype,
                source=row.source,
            )
            for row in genotype_result.scalars().all()
        ]

    med_result = await db.execute(
        select(Medication).where(
            Medication.patient_id == patient.id,
            Medication.pharmacy_id == staff.pharmacy_id,
            Medication.status == "active",
            Medication.is_deleted == False,  # noqa: E712
        )
    )
    medications = [
        normalize(row.normalized_name or row.drug_name)
        for row in med_result.scalars().all()
        if (row.normalized_name or row.drug_name)
    ]
    requested_drugs = [normalize(drug) for drug in (body.drugs or []) if drug.strip()]
    return PGxContext(
        genotypes=genotypes,
        medications=medications,
        requested_drugs=requested_drugs,
    )


def _context_snapshot(patient: Patient, context: PGxContext) -> dict:
    return {
        "patient_id": str(patient.id),
        "genotypes": [asdict(genotype) for genotype in context.genotypes],
        "medications": context.medications,
        "requested_drugs": context.requested_drugs,
    }


def _result_snapshot(result: PGxResult) -> dict:
    return {
        "interpretations": [asdict(item) for item in result.interpretations],
        "missing_information": result.missing_information,
    }


@router.post("/interpret")
async def interpret_pgx(
    body: PGxInterpretRequest,
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

    context = await _load_context(db=db, patient=patient, staff=staff, body=body)
    evaluated_at = datetime.now(timezone.utc)
    note: str | None = None
    try:
        review_result = engine.interpret_with_missing(context)
    except Exception as exc:  # pragma: no cover - defensive fail-safe for clinical endpoint behavior
        note = f"PGx interpretation could not be completed deterministically: {type(exc).__name__}."
        review_result = PGxResult(
            interpretations=[],
            missing_information=[note],
            model_version=MODEL_VERSION,
        )

    if not context.genotypes:
        note = "No genotype results available for PGx interpretation."

    interpretations = [asdict(item) for item in review_result.interpretations]
    output_snapshot = _result_snapshot(review_result)
    db.add(
        ClinicalAuditLog(
            user_id=staff.id,
            patient_id=patient.id,
            module="pgx",
            input_snapshot=_jsonable(_context_snapshot(patient, context)),
            output_snapshot=_jsonable(output_snapshot),
            rules_triggered=[f"{item.gene}:{item.drug}" for item in review_result.interpretations],
            model_version=MODEL_VERSION,
            created_by=staff.id,
            updated_by=staff.id,
        )
    )
    await db.flush()

    response = {
        "patient_id": str(patient.id),
        "evaluated_at": evaluated_at.isoformat(),
        "model_version": MODEL_VERSION,
        "genotypes": [asdict(genotype) for genotype in context.genotypes],
        "interpretations": interpretations,
        "missing_information": review_result.missing_information,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }
    if note:
        response["note"] = note
    return response
