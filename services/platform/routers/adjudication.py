"""Adjudication router — submit claims, handle rejects, reversals, ERA."""
from datetime import date
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.database import get_db
from services.core.adjudication.engine import AdjudicationEngine
from services.core.adjudication.reject_resolver import RejectResolver
from shared.models.auth import Staff
from shared.models.claims import ClaimTransaction
from shared.models.prescription import Prescription, PrescriptionFill, RxStatus
from shared.models.patient import Patient
from shared.models.insurance import PatientInsurance
from shared.models.prescriber import Prescriber

router = APIRouter()
_resolver = RejectResolver()


class ClaimSubmitRequest(BaseModel):
    fill_id: UUID
    insurance_id: UUID
    ingredient_cost: float
    dispensing_fee: float = 1.00
    usual_and_customary: Optional[float] = None
    submission_clarification_code: Optional[str] = None
    prior_auth_number: Optional[str] = None
    prior_auth_type_code: Optional[str] = None


@router.post("/submit")
async def submit_claim(
    body: ClaimSubmitRequest,
    staff: Staff = Depends(require_permission("claims:submit")),
    db: AsyncSession = Depends(get_db),
):
    """Submit a claim for a prescription fill to the PBM."""

    # Load fill
    fill_result = await db.execute(
        select(PrescriptionFill).where(PrescriptionFill.id == body.fill_id)
    )
    fill = fill_result.scalar_one_or_none()
    if not fill:
        raise HTTPException(404, "Fill not found")

    # Load prescription
    rx_result = await db.execute(
        select(Prescription).where(Prescription.id == fill.prescription_id)
    )
    rx = rx_result.scalar_one_or_none()
    if not rx:
        raise HTTPException(404, "Prescription not found")

    # Load patient
    patient_result = await db.execute(select(Patient).where(Patient.id == rx.patient_id))
    patient = patient_result.scalar_one_or_none()

    # Load insurance
    insurance_result = await db.execute(
        select(PatientInsurance).where(PatientInsurance.id == body.insurance_id)
    )
    insurance = insurance_result.scalar_one_or_none()
    if not insurance:
        raise HTTPException(404, "Insurance not found")

    # Load prescriber NPI
    prescriber_result = await db.execute(
        select(Prescriber).where(Prescriber.id == rx.prescriber_id)
    )
    prescriber = prescriber_result.scalar_one_or_none()

    engine = AdjudicationEngine(db)
    result = await engine.submit_claim(
        fill_id=body.fill_id,
        claim_data={
            "rx_number": rx.rx_number,
            "fill_number": fill.fill_number,
            "ndc": fill.ndc_dispensed,
            "quantity": float(fill.quantity_dispensed),
            "days_supply": fill.days_supply,
            "date_of_service": fill.fill_date.isoformat(),
            "daw_code": rx.daw_code,
            "fill_id": str(body.fill_id),
            "ingredient_cost": body.ingredient_cost,
            "dispensing_fee": body.dispensing_fee,
            "usual_and_customary": body.usual_and_customary,
            "submission_clarification_code": body.submission_clarification_code or "",
            "prior_auth_number": body.prior_auth_number or "",
            "prior_auth_type_code": body.prior_auth_type_code or "",
        },
        insurance={
            "bin_number": insurance.bin_number,
            "pcn": insurance.pcn or "",
            "group_number": insurance.group_number or "",
            "member_id": insurance.member_id,
            "person_code": insurance.person_code,
        },
        patient={
            "first_name": patient.first_name if patient else "",
            "last_name": patient.last_name if patient else "",
            "date_of_birth": patient.date_of_birth.isoformat() if patient else "",
        },
        prescriber_npi=prescriber.npi if prescriber else "",
    )

    # Persist claim transaction
    claim = ClaimTransaction(
        id=result.claim_id,
        fill_id=body.fill_id,
        pharmacy_id=staff.pharmacy_id,
        patient_insurance_id=body.insurance_id,
        bin_number=insurance.bin_number,
        pcn=insurance.pcn,
        group_number=insurance.group_number,
        member_id=insurance.member_id,
        person_code=insurance.person_code,
        ndc=fill.ndc_dispensed,
        quantity=float(fill.quantity_dispensed),
        days_supply=fill.days_supply,
        daw_code=rx.daw_code,
        date_of_service=fill.fill_date,
        ingredient_cost_submitted=body.ingredient_cost,
        dispensing_fee_submitted=body.dispensing_fee,
        usual_and_customary=body.usual_and_customary,
        status=result.status,
        response_status=result.response_status,
        reject_codes=result.reject_codes,
        reject_messages=result.reject_messages,
        ingredient_cost_paid=result.ingredient_cost_paid,
        dispensing_fee_paid=result.dispensing_fee_paid,
        total_amount_paid=result.total_amount_paid,
        patient_pay_amount=result.patient_pay_amount,
        response_time_ms=result.response_time_ms,
        raw_request=result.raw_request,
        raw_response=result.raw_response,
        created_by=staff.id,
    )
    db.add(claim)

    # Advance Rx state based on result
    from services.core.pharmacy_workflow.state_machine import RxStateMachine
    sm = RxStateMachine(db)
    if result.status == "approved":
        await sm.transition(
            prescription_id=rx.id,
            to_status=RxStatus.READY_TO_FILL,
            triggered_by_id=staff.id,
            triggered_by_type="system",
            reason=f"Claim approved — copay ${result.patient_pay_amount:.2f}",
        )
    elif result.status == "rejected":
        if "75" in result.reject_codes:
            await sm.transition(
                prescription_id=rx.id,
                to_status=RxStatus.PENDING_PA,
                triggered_by_id=staff.id,
                triggered_by_type="system",
                reason=f"Reject code 75 — PA required",
            )
        else:
            await sm.transition(
                prescription_id=rx.id,
                to_status=RxStatus.ADJUDICATION_REJECTED,
                triggered_by_id=staff.id,
                triggered_by_type="system",
                reason=f"Rejected: {', '.join(result.reject_codes)}",
            )

    pharmacist_instructions = None
    if result.reject_codes:
        pharmacist_instructions = _resolver.get_pharmacist_instructions(result.reject_codes)

    return {
        "claim_id": str(result.claim_id),
        "status": result.status,
        "approved": result.status == "approved",
        "response_time_ms": result.response_time_ms,
        "patient_pay_amount": result.patient_pay_amount,
        "total_amount_paid": result.total_amount_paid,
        "reject_codes": result.reject_codes,
        "reject_messages": result.reject_messages,
        "pharmacist_instructions": pharmacist_instructions,
        "auto_resolution_attempted": result.auto_resolution_attempted,
        "auto_resolution_action": result.auto_resolution_action,
    }


@router.post("/reverse/{claim_id}")
async def reverse_claim(
    claim_id: UUID,
    reason: str,
    staff: Staff = Depends(require_permission("claims:submit")),
    db: AsyncSession = Depends(get_db),
):
    """Reverse a previously approved claim."""
    result = await db.execute(
        select(ClaimTransaction).where(ClaimTransaction.id == claim_id)
    )
    claim = result.scalar_one_or_none()
    if not claim:
        raise HTTPException(404, "Claim not found")

    if claim.status != "approved":
        raise HTTPException(422, f"Can only reverse approved claims (current status: {claim.status})")

    engine = AdjudicationEngine(db)
    fill_result = await db.execute(
        select(PrescriptionFill).where(PrescriptionFill.id == claim.fill_id)
    )
    fill = fill_result.scalar_one_or_none()

    reversal_result = await engine.reverse_claim(
        original_claim_data={
            "rx_number": "",    # Will load from fill→prescription
            "fill_number": 0,
            "ndc": claim.ndc,
            "quantity": float(claim.quantity),
            "date_of_service": claim.date_of_service.isoformat(),
            "fill_id": str(claim.fill_id),
        },
        insurance={
            "bin_number": claim.bin_number,
            "pcn": claim.pcn or "",
            "member_id": claim.member_id,
            "person_code": claim.person_code,
        },
    )

    claim.status = reversal_result.status
    return {"status": reversal_result.status, "claim_id": str(claim_id)}


@router.get("/history/{pharmacy_id}")
async def get_claim_history(
    pharmacy_id: UUID,
    status: Optional[str] = None,
    limit: int = 100,
    staff: Staff = Depends(require_permission("claims:read")),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(ClaimTransaction).where(
        ClaimTransaction.pharmacy_id == pharmacy_id
    )
    if status:
        stmt = stmt.where(ClaimTransaction.status == status)
    stmt = stmt.order_by(ClaimTransaction.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    claims = result.scalars().all()
    return [
        {
            "id": str(c.id),
            "fill_id": str(c.fill_id),
            "ndc": c.ndc,
            "status": c.status,
            "total_amount_paid": float(c.total_amount_paid) if c.total_amount_paid else None,
            "patient_pay_amount": float(c.patient_pay_amount) if c.patient_pay_amount else None,
            "reject_codes": c.reject_codes,
            "response_time_ms": c.response_time_ms,
            "created_at": c.created_at.isoformat(),
        }
        for c in claims
    ]
