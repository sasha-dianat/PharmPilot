"""Auth router — login, logout, refresh, staff registration."""
import hashlib
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import (
    authenticate_staff, get_current_staff, hash_password, require_permission
)
from services.platform.database import get_db
from shared.models.auth import Staff, StaffRole, StaffSession

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str
    workstation_id: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    staff_id: UUID
    role: str
    permissions: list[str]
    pharmacy_id: UUID


class StaffCreateRequest(BaseModel):
    email: EmailStr
    username: str
    password: str
    first_name: str
    last_name: str
    role: StaffRole
    pharmacist_license_number: Optional[str] = None
    pharmacist_license_state: Optional[str] = None
    npi: Optional[str] = None


class RefreshRequest(BaseModel):
    refresh_token: str


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Authenticate pharmacy staff. Returns JWT access + refresh tokens."""
    ip = request.client.host if request.client else None
    access_token, refresh_token, staff = await authenticate_staff(
        username=body.username,
        password=body.password,
        db=db,
        ip_address=ip,
        workstation_id=body.workstation_id,
    )

    from shared.models.auth import ROLE_PERMISSIONS
    perms = ROLE_PERMISSIONS.get(staff.role, [])

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        staff_id=staff.id,
        role=staff.role,
        permissions=perms,
        pharmacy_id=staff.pharmacy_id,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange a refresh token for a new access token."""
    import secrets
    from services.platform.auth import create_access_token, create_refresh_token

    refresh_hash = hashlib.sha256(body.refresh_token.encode()).hexdigest()

    session_result = await db.execute(
        select(StaffSession).where(
            StaffSession.refresh_token_hash == refresh_hash,
            StaffSession.revoked == False,  # noqa: E712
        )
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    if session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Refresh token expired")

    # Revoke old session
    session.revoked = True
    session.revoked_at = datetime.now(timezone.utc)

    staff_result = await db.execute(select(Staff).where(Staff.id == session.staff_id))
    staff = staff_result.scalar_one_or_none()
    if not staff or not staff.is_active:
        raise HTTPException(status_code=401, detail="Staff not found or inactive")

    # Issue new tokens
    new_jti = secrets.token_hex(32)
    access_token = create_access_token(
        staff_id=staff.id,
        pharmacy_id=staff.pharmacy_id,
        role=staff.role,
        jti=new_jti,
    )
    new_refresh_raw, new_refresh_hash = create_refresh_token(staff.id)

    from datetime import timedelta
    from services.platform.config import settings
    new_session = StaffSession(
        staff_id=staff.id,
        token_jti=new_jti,
        refresh_token_hash=new_refresh_hash,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(new_session)

    from shared.models.auth import ROLE_PERMISSIONS
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh_raw,
        staff_id=staff.id,
        role=staff.role,
        permissions=ROLE_PERMISSIONS.get(staff.role, []),
        pharmacy_id=staff.pharmacy_id,
    )


@router.post("/logout")
async def logout(
    staff: Staff = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Revoke current session token."""
    # The JTI is embedded in the token — find and revoke
    result = await db.execute(
        select(StaffSession).where(StaffSession.staff_id == staff.id, StaffSession.revoked == False)  # noqa: E712
    )
    sessions = result.scalars().all()
    for s in sessions:
        s.revoked = True
        s.revoked_at = datetime.now(timezone.utc)
    return {"status": "logged_out", "sessions_revoked": len(sessions)}


@router.post("/staff", response_model=dict, status_code=201)
async def create_staff(
    body: StaffCreateRequest,
    _: Staff = Depends(require_permission("staff:write")),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pharmacy staff account. Requires staff:write permission."""
    existing = await db.execute(
        select(Staff).where(
            (Staff.email == body.email) | (Staff.username == body.username)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email or username already exists")

    # Get pharmacy_id from token (can only create staff for your own pharmacy)
    from services.platform.auth import get_current_staff as _gcs
    requesting_staff = await db.execute(select(Staff).where(Staff.email == body.email))

    # Create staff in same pharmacy as requesting user
    new_staff = Staff(
        pharmacy_id=_.pharmacy_id,
        email=body.email,
        username=body.username,
        hashed_password=hash_password(body.password),
        first_name=body.first_name,
        last_name=body.last_name,
        role=body.role,
        pharmacist_license_number=body.pharmacist_license_number,
        pharmacist_license_state=body.pharmacist_license_state,
        npi=body.npi,
    )
    db.add(new_staff)
    await db.flush()

    return {
        "staff_id": str(new_staff.id),
        "username": new_staff.username,
        "role": new_staff.role,
        "status": "created",
    }


@router.get("/me")
async def get_me(staff: Staff = Depends(get_current_staff)):
    """Return current authenticated staff profile."""
    from shared.models.auth import ROLE_PERMISSIONS
    return {
        "staff_id": str(staff.id),
        "username": staff.username,
        "first_name": staff.first_name,
        "last_name": staff.last_name,
        "role": staff.role,
        "pharmacy_id": str(staff.pharmacy_id),
        "permissions": ROLE_PERMISSIONS.get(staff.role, []),
        "epcs_enrolled": staff.epcs_enrolled,
        "epcs_identity_proofed": staff.epcs_identity_proofed,
    }
