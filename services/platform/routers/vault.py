"""
Biometric Evidence Vault API — admin-only access with legal-reason auditing.
All operations produce immutable chain-of-custody records.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff
from services.platform.database import get_db
from services.biometric.evidence_vault.vault import (
    BiometricEvidenceVault, InsufficientVaultPrivilegeError,
    LegalHoldActiveError, VaultAccessReason, VaultObjectNotFoundError,
    VaultKeyProvider, IntegrityError
)
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_vault(db) -> BiometricEvidenceVault:
    return BiometricEvidenceVault(
        key_provider=VaultKeyProvider(provider="env"),
        db=db,
    )


class VaultAccessRequest(BaseModel):
    vault_id: UUID
    legal_reason: VaultAccessReason
    legal_notes: str


class ExportRequest(BaseModel):
    identity_id: UUID
    legal_reason: VaultAccessReason
    authority_name: str
    case_reference: str


class LegalHoldRequest(BaseModel):
    identity_id: UUID
    hold_reason: str


@router.get("/objects/{identity_id}")
async def list_vault_objects(
    identity_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    List all vault objects for an identity (metadata only — no decryption).
    Shows types, timestamps, zones, and legal hold status.
    """
    if staff.role not in ("super_admin", "pharmacy_manager"):
        raise HTTPException(403, "Vault access requires super_admin or pharmacy_manager role")

    result = await db.execute(text("""
        SELECT id, object_type, content_hash, size_bytes,
               camera_zone, captured_at, legal_hold, legal_hold_reason
        FROM biometric_vault_objects
        WHERE identity_id = :id
        ORDER BY captured_at DESC
    """), {"id": str(identity_id)})

    rows = result.mappings().all()
    return {
        "identity_id": str(identity_id),
        "total_objects": len(rows),
        "objects": [
            {
                "vault_id": str(row["id"]),
                "object_type": row["object_type"],
                "size_bytes": row["size_bytes"],
                "camera_zone": row["camera_zone"],
                "captured_at": row["captured_at"].isoformat() if row["captured_at"] else None,
                "legal_hold": row["legal_hold"],
                "legal_hold_reason": row["legal_hold_reason"],
            }
            for row in rows
        ],
    }


@router.post("/retrieve")
async def retrieve_vault_object(
    body: VaultAccessRequest,
    request_ip: Optional[str] = None,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Decrypt and return a single vault object.
    Requires: admin role + legal reason + detailed notes.
    Every call is permanently logged.
    """
    vault = _get_vault(db)
    try:
        obj = await vault.retrieve(
            vault_id=body.vault_id,
            requesting_staff_id=staff.id,
            requesting_staff_role=staff.role,
            legal_reason=body.legal_reason,
            legal_notes=body.legal_notes,
            ip_address=request_ip,
        )
    except InsufficientVaultPrivilegeError as e:
        raise HTTPException(403, str(e))
    except LegalHoldActiveError as e:
        raise HTTPException(423, str(e))
    except VaultObjectNotFoundError as e:
        raise HTTPException(404, str(e))
    except IntegrityError as e:
        logger.critical("VAULT INTEGRITY FAILURE: %s", e)
        raise HTTPException(500, f"Vault integrity check failed: {e}")

    # Determine response content type
    content_type_map = {
        "face_frame": "image/jpeg",
        "voice_audio": "audio/wav",
        "gait_clip": "video/mp4",
        "face_embedding": "application/octet-stream",
        "depth_map": "application/octet-stream",
        "fingerprint_raw": "application/octet-stream",
    }
    content_type = content_type_map.get(obj.object_type.value, "application/octet-stream")

    return Response(
        content=obj.raw_bytes,
        media_type=content_type,
        headers={
            "X-Vault-Id": str(obj.vault_id),
            "X-Identity-Id": str(obj.identity_id),
            "X-Captured-At": obj.captured_at.isoformat(),
            "X-Content-Hash": obj.content_hash_sha256,
            "X-Legal-Reason": body.legal_reason.value,
        },
    )


@router.post("/export")
async def export_for_authority(
    body: ExportRequest,
    request_ip: Optional[str] = None,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """
    Export all biometric evidence for an identity as a signed, time-stamped ZIP.
    For handoff to police, court, or insurance investigators.
    Download filename includes identity, authority, and case reference.
    """
    vault = _get_vault(db)
    try:
        zip_bytes = await vault.export_for_authority(
            identity_id=body.identity_id,
            requesting_staff_id=staff.id,
            requesting_staff_role=staff.role,
            legal_reason=body.legal_reason,
            authority_name=body.authority_name,
            case_reference=body.case_reference,
            ip_address=request_ip,
        )
    except InsufficientVaultPrivilegeError as e:
        raise HTTPException(403, str(e))
    except VaultObjectNotFoundError as e:
        raise HTTPException(404, str(e))

    from datetime import datetime
    filename = f"pharmpilot_evidence_{str(body.identity_id)[:8]}_{body.case_reference}_{datetime.now().strftime('%Y%m%d')}.zip"

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Export-Identity": str(body.identity_id),
            "X-Case-Reference": body.case_reference,
            "X-Authority": body.authority_name,
        },
    )


@router.post("/legal-hold")
async def set_legal_hold(
    body: LegalHoldRequest,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Place a legal hold — prevents deletion of all vault objects for this identity."""
    vault = _get_vault(db)
    try:
        await vault.set_legal_hold(
            identity_id=body.identity_id,
            hold_reason=body.hold_reason,
            requesting_staff_id=staff.id,
            requesting_staff_role=staff.role,
        )
    except InsufficientVaultPrivilegeError as e:
        raise HTTPException(403, str(e))
    return {"status": "legal_hold_active", "identity_id": str(body.identity_id)}


@router.delete("/legal-hold/{identity_id}")
async def lift_legal_hold(
    identity_id: UUID,
    lift_reason: str,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Lift a legal hold (requires documented reason)."""
    vault = _get_vault(db)
    try:
        await vault.lift_legal_hold(
            identity_id=identity_id,
            lift_reason=lift_reason,
            requesting_staff_id=staff.id,
            requesting_staff_role=staff.role,
        )
    except InsufficientVaultPrivilegeError as e:
        raise HTTPException(403, str(e))
    return {"status": "legal_hold_lifted", "identity_id": str(identity_id)}


@router.get("/custody-log/{identity_id}")
async def get_custody_log(
    identity_id: UUID,
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Full chain-of-custody log for an identity. Who accessed, when, why."""
    if staff.role not in ("super_admin", "pharmacy_manager"):
        raise HTTPException(403, "Chain-of-custody log requires admin role")

    result = await db.execute(text("""
        SELECT id, event_type, accessed_by_staff_id, accessed_by_role,
               legal_reason, ip_address, event_at, object_types,
               export_reference, signature_hex
        FROM vault_access_log
        WHERE identity_id = :id
        ORDER BY event_at DESC
    """), {"id": str(identity_id)})

    rows = result.mappings().all()
    return {
        "identity_id": str(identity_id),
        "total_events": len(rows),
        "events": [
            {
                "event_id": str(row["id"]),
                "event_type": row["event_type"],
                "staff_id": str(row["accessed_by_staff_id"]),
                "role": row["accessed_by_role"],
                "legal_reason": row["legal_reason"],
                "ip_address": row["ip_address"],
                "timestamp": row["event_at"].isoformat(),
                "object_types": row["object_types"],
                "export_reference": row["export_reference"],
                "signature_hex": row["signature_hex"][:16] + "…",
            }
            for row in rows
        ],
    }
