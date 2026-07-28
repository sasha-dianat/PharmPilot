"""
Auth service — JWT generation/validation, password hashing, RBAC enforcement.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Query, WebSocket, status
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


# SSE tickets are short-lived, single-purpose tokens used ONLY to authenticate
# browser EventSource connections (which cannot send Authorization headers).
# Unlike the long-lived access token, a leaked SSE ticket via logs/history/
# referrers is only useful for ~60 seconds and only for opening a stream —
# it can never be used as a general bearer credential.
SSE_TICKET_EXPIRE_SECONDS = 60


def create_sse_ticket(staff_id: UUID, jti: str) -> str:
    expires = datetime.now(timezone.utc) + timedelta(seconds=SSE_TICKET_EXPIRE_SECONDS)
    payload = {
        "sub": str(staff_id),
        "jti": jti,
        "exp": expires,
        "iat": datetime.now(timezone.utc),
        "type": "sse",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


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

async def _resolve_staff_from_token(
    token: str, db: AsyncSession, expected_type: str = "access"
) -> Staff:
    """
    Shared core of token validation: decode JWT, verify session not revoked/
    expired, load Staff, check lockout. Used by both the standard Bearer-header
    dependency (get_current_staff, expects "access") and the short-lived SSE
    ticket dependency (get_current_staff_sse, expects "sse").
    """
    payload = decode_token(token)

    if payload.get("type") != expected_type:
        raise HTTPException(status_code=401, detail=f"Not a valid {expected_type} token")

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


async def get_current_staff(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    return await _resolve_staff_from_token(credentials.credentials, db, expected_type="access")


async def get_current_staff_with_jti(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> tuple[Staff, str]:
    """
    Like get_current_staff, but also returns the access token's `jti` so the
    caller can mint a short-lived SSE ticket bound to the same revocable
    session (see create_sse_ticket / POST /auth/sse-ticket).
    """
    payload = decode_token(credentials.credentials)
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Not a valid access token")
    staff = await _resolve_staff_from_token(credentials.credentials, db, expected_type="access")
    return staff, payload["jti"]


# Optional Bearer scheme — does not raise when the header is absent. Needed for
# get_current_staff_sse below so browsers' EventSource (which cannot set custom
# HTTP headers) can authenticate via a short-lived `?ticket=` query parameter
# instead of the long-lived access token.
_optional_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_staff_sse(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer_scheme),
    ticket: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Staff:
    """
    Authenticates via the standard `Authorization: Bearer <jwt>` header (for
    non-browser/test clients) OR a short-lived `?ticket=<sse-ticket>` query
    parameter (for browsers' EventSource, which cannot attach custom headers).

    IMPORTANT: the long-lived access token must NEVER be placed in a URL
    (logs, browser history, and Referer headers would all expose it). Instead,
    the client first calls POST /auth/sse-ticket (authenticated normally via
    the Authorization header) to mint a single-purpose ticket that expires in
    SSE_TICKET_EXPIRE_SECONDS and can only be used to open a stream — never as
    a general bearer credential.

    Use ONLY for SSE/streaming routes; everything else should keep using
    get_current_staff so the Authorization header remains required.
    """
    if credentials:
        return await _resolve_staff_from_token(credentials.credentials, db, expected_type="access")

    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await _resolve_staff_from_token(ticket, db, expected_type="sse")


async def require_ws_staff(
    websocket: WebSocket,
    ticket: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> Optional[Staff]:
    """Authenticate a WebSocket via a short-lived `?ticket=` (POST /auth/sse-ticket).

    A browser cannot attach an Authorization header to a WebSocket handshake, so
    the same ticket mechanism the SSE routes use applies here — and for the same
    reason it must be a ticket and not the access token: the URL lands in server
    logs, browser history and Referer headers.

    Returns None after closing the socket with 1008 (policy violation) when the
    ticket is missing or invalid. Handlers must return immediately on None —
    `require_permission` cannot be used here because raising HTTPException in a
    WebSocket scope produces a failed handshake with no usable status.
    """
    if not ticket:
        await websocket.close(code=1008, reason="Not authenticated")
        return None
    try:
        return await _resolve_staff_from_token(ticket, db, expected_type="sse")
    except HTTPException:
        await websocket.close(code=1008, reason="Invalid or expired ticket")
        return None


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


# ── get_current_user — dict alias for routers that need a plain dict ──────────
# Several routers (drug_database, label_engine, package_verification, pos, etc.)
# were written using the generic name `get_current_user` with `dict` return type
# so they can call  current.get("sub"), current.get("pharmacy_id")  etc.
# This thin wrapper satisfies that contract without changing those routers.

async def get_current_user(
    staff: Staff = Depends(get_current_staff),
) -> dict:
    return {
        "sub":         str(staff.id),
        "username":    staff.username,
        "role":        staff.role.value if hasattr(staff.role, "value") else str(staff.role),
        "pharmacy_id": str(staff.pharmacy_id) if staff.pharmacy_id else None,
        "staff_id":    str(staff.id),
    }


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
