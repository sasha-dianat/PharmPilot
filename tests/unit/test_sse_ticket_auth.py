"""
Unit tests for the short-lived SSE ticket auth mechanism (services/platform/auth.py).

Background: CouncilReport.tsx used to open its EventSource with the long-lived
access JWT in the URL query string (`?token=<jwt>`), which exposes a
session-length bearer credential via server logs, browser history, and Referer
headers — unacceptable for a PHI application. The fix mints a short-lived
(SSE_TICKET_EXPIRE_SECONDS), single-purpose `type: "sse"` token via an
authenticated POST /auth/sse-ticket, and get_current_staff_sse now only
accepts that ticket type via the query string — never a general access token.

Tests run without a real DB by stubbing the AsyncSession, following the same
pattern as tests/unit/test_intake_precompute.py.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from jose import jwt

from services.platform.auth import (
    SSE_TICKET_EXPIRE_SECONDS,
    create_access_token,
    create_sse_ticket,
    decode_token,
    get_current_staff_sse,
    _resolve_staff_from_token,
)
from services.platform.config import settings


STAFF_ID = uuid4()
PHARMACY_ID = uuid4()
JTI = "session-jti-123"


def _make_db():
    """Stub AsyncSession returning a valid, non-revoked session and active staff."""
    db = AsyncMock()

    session = MagicMock()
    session.token_jti = JTI
    session.revoked = False
    session.expires_at = datetime.now(timezone.utc) + timedelta(hours=1)

    staff = MagicMock()
    staff.id = STAFF_ID
    staff.is_active = True
    staff.locked_until = None
    staff.pharmacy_id = PHARMACY_ID

    async def _execute(query, params=None):
        r = MagicMock()
        q = str(query).lower()
        if "staffsession" in q or "staff_sessions" in q:
            r.scalar_one_or_none.return_value = session
        elif "staff" in q:
            r.scalar_one_or_none.return_value = staff
        else:
            r.scalar_one_or_none.return_value = None
        return r

    db.execute = AsyncMock(side_effect=_execute)
    return db


# ── create_sse_ticket ────────────────────────────────────────────────────────

def test_sse_ticket_has_short_expiry_and_sse_type():
    ticket = create_sse_ticket(STAFF_ID, JTI)
    payload = jwt.decode(ticket, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])

    assert payload["type"] == "sse"
    assert payload["sub"] == str(STAFF_ID)
    assert payload["jti"] == JTI

    ttl = payload["exp"] - payload["iat"]
    assert ttl == SSE_TICKET_EXPIRE_SECONDS
    assert SSE_TICKET_EXPIRE_SECONDS <= 120, "SSE tickets must stay short-lived"


def test_sse_ticket_is_distinguishable_from_access_token():
    """An access token and an SSE ticket must never be interchangeable —
    the whole point is that a leaked ticket can't be used as a bearer credential."""
    access = create_access_token(STAFF_ID, PHARMACY_ID, role="pharmacist", jti=JTI)
    ticket = create_sse_ticket(STAFF_ID, JTI)

    assert decode_token(access)["type"] == "access"
    assert decode_token(ticket)["type"] == "sse"
    assert decode_token(access)["type"] != decode_token(ticket)["type"]


# ── _resolve_staff_from_token type enforcement ──────────────────────────────

def test_resolve_staff_rejects_access_token_when_sse_expected():
    db = _make_db()
    access = create_access_token(STAFF_ID, PHARMACY_ID, role="pharmacist", jti=JTI)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_resolve_staff_from_token(access, db, expected_type="sse"))
    assert exc_info.value.status_code == 401


def test_resolve_staff_rejects_sse_ticket_when_access_expected():
    """An SSE ticket must not work as a general bearer credential on normal routes."""
    db = _make_db()
    ticket = create_sse_ticket(STAFF_ID, JTI)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_resolve_staff_from_token(ticket, db, expected_type="access"))
    assert exc_info.value.status_code == 401


def test_resolve_staff_accepts_matching_sse_ticket():
    db = _make_db()
    ticket = create_sse_ticket(STAFF_ID, JTI)

    staff = asyncio.run(_resolve_staff_from_token(ticket, db, expected_type="sse"))
    assert staff.id == STAFF_ID


# ── get_current_staff_sse ────────────────────────────────────────────────────

def test_get_current_staff_sse_accepts_ticket_via_query_param():
    db = _make_db()
    ticket = create_sse_ticket(STAFF_ID, JTI)

    staff = asyncio.run(get_current_staff_sse(credentials=None, ticket=ticket, db=db))
    assert staff.id == STAFF_ID


def test_get_current_staff_sse_rejects_access_token_via_query_param():
    """The long-lived access token must never be accepted from the query string —
    only the short-lived sse ticket is allowed there."""
    db = _make_db()
    access = create_access_token(STAFF_ID, PHARMACY_ID, role="pharmacist", jti=JTI)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_current_staff_sse(credentials=None, ticket=access, db=db))
    assert exc_info.value.status_code == 401


def test_get_current_staff_sse_requires_some_credential():
    db = _make_db()
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(get_current_staff_sse(credentials=None, ticket=None, db=db))
    assert exc_info.value.status_code == 401
