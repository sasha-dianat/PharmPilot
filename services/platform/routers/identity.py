"""
Identity router — automatic, zero-manual-entry patient identification.
======================================================================
Endpoints the reception booth / workstation call to identify a customer from
biometrics + transcript + prescription photo + Iranian insurance, and to browse
the full set of linked candidate profiles. No name is ever typed by staff.
"""
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.config import settings
from services.platform.database import get_db
from services.integrations.iranian_insurance.registry import IranianInsuranceRegistry
from services.biometric.identity_resolution.identity_orchestrator import IdentityOrchestrator
from services.biometric.identity_resolution.person_links import (
    PersonLinkGraph, patient_ref,
)
from shared.models.auth import Staff

router = APIRouter()


def _registry() -> IranianInsuranceRegistry:
    return IranianInsuranceRegistry.from_settings(settings)


def _orchestrator(db: AsyncSession, identity_system: Optional[str] = None) -> IdentityOrchestrator:
    return IdentityOrchestrator(
        db, _registry(),
        identity_system=identity_system or settings.DEFAULT_IDENTITY_SYSTEM,
    )


class IdentifyRequest(BaseModel):
    pharmacy_id: UUID
    transcript_text: Optional[str] = None
    prescription_ocr_text: Optional[str] = None
    national_code: Optional[str] = None
    biometric_identity_id: Optional[UUID] = None
    biometric_confidence: float = 0.0
    identity_system: Optional[str] = None  # override default (iranian|american)


@router.post("/identify")
async def identify_customer(
    body: IdentifyRequest,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Resolve a customer's identity from every available automatic source and
    return all linked candidate profiles for the pharmacist to browse.
    """
    orch = _orchestrator(db, body.identity_system)
    result = await orch.identify(
        body.pharmacy_id,
        transcript_text=body.transcript_text,
        prescription_ocr_text=body.prescription_ocr_text,
        national_code=body.national_code,
        biometric_identity_id=body.biometric_identity_id,
        biometric_confidence=body.biometric_confidence,
    )
    return {
        "customer_id": result.customer_id,
        "primary_candidate_id": result.primary_candidate_id,
        "is_returning_customer": result.is_returning_customer,
        "auto_loaded": result.auto_loaded,
        "message": result.message,
        "extracted": result.extracted,
        "insurance": result.insurance_summary,
        "candidates": [
            {
                "patient_id": c.patient_id,
                "national_id": c.national_id,
                "name": c.name,
                "name_fa": c.name_fa,
                "dob": c.dob,
                "dob_jalali": c.dob_jalali,
                "gender": c.gender,
                "relationship_hint": c.relationship_hint,
                "is_self": c.is_self,
                "confidence": c.confidence,
                "active_rx_count": c.active_rx_count,
                "recent_fills": c.recent_fills,
                "allergies": c.allergies,
                "loyalty_points": c.loyalty_points,
                "total_visits": c.total_visits,
            }
            for c in result.candidates
        ],
    }


class LinkRequest(BaseModel):
    pharmacy_id: UUID
    patient_id_a: UUID
    patient_id_b: UUID
    relationship: Optional[str] = None   # optional — linking does NOT require it


@router.post("/link")
async def link_persons(
    body: LinkRequest,
    staff: Staff = Depends(require_permission("patient:write")),
    db: AsyncSession = Depends(get_db),
):
    """Manually link two patient profiles (relationship optional)."""
    graph = PersonLinkGraph(db)
    await graph.link(
        body.pharmacy_id,
        patient_ref(body.patient_id_a),
        patient_ref(body.patient_id_b),
        relationship=body.relationship,
        confidence=1.0,
        source="manual",
    )
    return {"status": "linked", "relationship": body.relationship}


@router.get("/candidates/{patient_id}")
async def get_linked_candidates(
    patient_id: UUID,
    staff: Staff = Depends(require_permission("patient:read")),
    db: AsyncSession = Depends(get_db),
):
    """Return every patient linked to this one (self + relations)."""
    graph = PersonLinkGraph(db)
    refs = await graph.connected_component(patient_ref(patient_id), max_depth=3)
    ids = graph.patient_ids_from_refs(refs)
    if not ids:
        ids = [str(patient_id)]
    rows = (await db.execute(
        text("""SELECT id, first_name, last_name, national_id, date_of_birth,
                       date_of_birth_jalali, gender
                FROM patients WHERE id = ANY(:ids) AND is_deleted = false"""),
        {"ids": ids},
    )).mappings().all()
    return {
        "patient_id": str(patient_id),
        "linked_count": len(rows),
        "candidates": [
            {
                "patient_id": str(r["id"]),
                "national_id": r["national_id"],
                "name": f"{r['first_name']} {r['last_name']}".strip(),
                "dob": str(r["date_of_birth"]) if r["date_of_birth"] else None,
                "dob_jalali": r["date_of_birth_jalali"],
                "gender": r["gender"],
                "is_self": str(r["id"]) == str(patient_id),
            }
            for r in rows
        ],
    }


class InsuranceLookupRequest(BaseModel):
    national_code: str


@router.post("/insurance/lookup")
async def insurance_lookup(
    body: InsuranceLookupRequest,
    staff: Staff = Depends(require_permission("patient:read")),
):
    """Cross-insurer eligibility + family-coverage lookup by national code."""
    from services.core.localization.national_id import validate_national_code
    if not validate_national_code(body.national_code):
        raise HTTPException(422, "Invalid Iranian national code (کد ملی)")
    reg = _registry()
    agg = await reg.aggregate_coverage(body.national_code)
    return {
        "national_code": body.national_code,
        "primary_org": agg.primary.org.value if agg.primary else None,
        "memberships": [
            {
                "org": m.org.value,
                "status": m.status.value,
                "name": f"{m.first_name or ''} {m.last_name or ''}".strip(),
                "dob_jalali": m.date_of_birth_jalali,
                "copay_percent": m.copay_percent,
            }
            for m in agg.all_memberships
        ],
        "linked_persons": [
            {
                "national_code": m.national_code,
                "name": f"{m.first_name or ''} {m.last_name or ''}".strip(),
                "relationship": m.relationship_to_principal,
                "gender": m.gender,
                "dob_jalali": m.date_of_birth_jalali,
            }
            for m in agg.linked_persons
        ],
    }
