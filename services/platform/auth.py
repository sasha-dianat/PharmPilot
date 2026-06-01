"""
Auth service — JWT generation/validation, password hashing, RBAC enforcement.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.config import settings
from services.platform.database import get_db
from shared.models.auth import Staff, StaffRole, StaffSession

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
bearer_scheme = HTTPBearer()


# ── Password utilities ──────────────────────────────────────────────────────

def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


# ── Token generation ────────────────────────────────────────────────────────

def create_access_token(staff_id: UUID, pharmacy_id: UUID, role: str, jti: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(staff_id),
        "pharmacy_id": str(pharmacy_id),
        "role": role,
        "jti": jti,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(staff_id: UUID) -> tuple[str, str]:
    """Returns (token_string, hashed_token) — store only the hash."""
    raw = secrets.token_urlsafe(64)
    hashed = hashlib.sha256(raw.encode()).hexdigest()
    return raw, hashed


def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ── FastAPI dependency — get current authenticated staff ───────────────────

async def get_current_staff(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    payload = decode_token(credentials.credentials)

    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Not an access token")

    jti = payload.get("jti")
    staff_id = payload.get("sub")

    # Check session not revoked
    session_result = await db.execute(
        select(StaffSession).where(
            StaffSession.token_jti == jti,
            StaffSession.revoked == False,  # noqa: E712
        )
    )
    session = session_result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=401, detail="Session revoked or not found")

    if session.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=401, detail="Token expired")

    staff_result = await db.execute(
        select(Staff).where(Staff.id == UUID(staff_id), Staff.is_active == True)  # noqa: E712
    )
    staff = staff_result.scalar_one_or_none()
    if not staff:
        raise HTTPException(status_code=401, detail="Staff not found or inactive")

    # Check account lockout
    if staff.locked_until and staff.locked_until > datetime.now(timezone.utc):
        raise HTTPException(
            status_code=423,
            detail=f"Account locked until {staff.locked_until.isoformat()}",
        )

    return staff


def require_permission(permission: str):
    """Dependency factory — use as: Depends(require_permission('rx:verify'))"""
    async def _check(staff: Staff = Depends(get_current_staff)) -> Staff:
        if not staff.has_permission(permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission required: {permission}. Your role: {staff.role}",
            )
        return staff
    return _check


def require_pharmacist():
    """Shortcut for requiring pharmacist role for clinical actions."""
    async def _check(staff: Staff = Depends(get_current_staff)) -> Staff:
        if staff.role not in (StaffRole.PHARMACIST, StaffRole.PHARMACY_MANAGER, StaffRole.SUPER_ADMIN):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Pharmacist role required for this action",
            )
        return staff
    return _check


def require_epcs():
    """Require EPCS enrollment for controlled substance operations."""
    async def _check(staff: Staff = Depends(get_current_staff)) -> Staff:
        if not staff.epcs_enrolled or not staff.epcs_identity_proofed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="EPCS enrollment required. Contact pharmacy administrator.",
            )
        return staff
    return _check


# ── Login helper ─────────────────────────────────────────────────────────────

async def authenticate_staff(
    username: str,
    password: str,
    db: AsyncSession,
    ip_address: Optional[str] = None,
    workstation_id: Optional[str] = None,
) -> tuple[str, str, Staff]:
    """
    Authenticate staff, create session, return (access_token, refresh_token, staff).
    Enforces lockout after 5 failed attempts.
    """
    result = await db.execute(
        select(Staff).where(
            Staff.username == username,
            Staff.is_deleted == False,  # noqa: E712
        )
    )
    staff = result.scalar_one_or_none()

    if not staff or not verify_password(password, staff.hashed_password):
        if staff:
            staff.failed_login_attempts += 1
            if staff.failed_login_attempts >= 5:
                staff.locked_until = datetime.now(timezone.utc) + timedelta(minutes=30)
        raise HTTPException(status_code=401, detail="Invalid credentials")

    if not staff.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")

    if staff.locked_until and staff.locked_until > datetime.now(timezone.utc):
        raise HTTPException(status_code=423, detail="Account temporarily locked")

    # Reset failed attempts on success
    staff.failed_login_attempts = 0
    staff.locked_until = None
    staff.last_login_at = datetime.now(timezone.utc)

    jti = secrets.token_hex(32)
    access_token = create_access_token(
        staff_id=staff.id,
        pharmacy_id=staff.pharmacy_id,
        role=staff.role,
        jti=jti,
    )
    refresh_raw, refresh_hash = create_refresh_token(staff.id)

    session = StaffSession(
        staff_id=staff.id,
        token_jti=jti,
        refresh_token_hash=refresh_hash,
        ip_address=ip_address,
        workstation_id=workstation_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    db.add(session)
    await db.flush()

    return access_token, refresh_raw, staff
