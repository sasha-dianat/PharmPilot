"""Every route authenticates unless it is on an explicit, justified allowlist.

This exists because three of the most PHI-dense endpoints in the platform had
no authentication dependency at all — most seriously
`GET /api/v1/audio/transcripts/{patient_id}`, which returned a patient's full
counselling-conversation transcripts, plus the allergies, conditions and
medications extracted from them, to any unauthenticated caller. There is no
global auth middleware (services/platform/main.py installs only CORS and GZip),
so a missing per-route dependency means the route is genuinely open.

A static scan is deliberate: it needs no database, no app import and no network,
so it runs in the unit suite on every change and cannot be skipped when the
environment is incomplete. Adding a public route is still possible — it just has
to be written down here, with a reason.
"""
from __future__ import annotations

import pathlib
import re

import pytest

ROUTERS = pathlib.Path("services/platform/routers")

# Any of these in a handler signature counts as authenticated.
AUTH_DEPENDENCIES = (
    "require_permission",
    "require_pharmacist",
    "require_epcs",
    "get_current_staff",
    "get_current_staff_sse",
    "get_current_staff_with_jti",
    "get_current_user",
    "require_ws_staff",
)

# (file, METHOD, path) -> why this one is legitimately public.
PUBLIC_ROUTES: dict[tuple[str, str, str], str] = {
    ("auth.py", "POST", "/login"):
        "Issues the credential; cannot itself require one.",
    ("auth.py", "POST", "/refresh"):
        "Presents a refresh token in the body and is validated there.",
    ("rx_documents.py", "GET", "/document-types"):
        "Static enum of document types. No patient data, no side effects.",
}


def _routes():
    pat = re.compile(r'@router\.(get|post|put|patch|delete|websocket)\(\s*"([^"]*)"')
    for f in sorted(ROUTERS.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        for m in pat.finditer(src):
            seg = src[m.start():m.start() + 2500]
            # the handler signature runs to the first line that closes the def
            sig = seg.split("\n)")[0] if "\n)" in seg[:2500] else seg[:1200]
            yield f.name, m.group(1).upper(), m.group(2), sig


def test_no_route_is_unauthenticated_without_a_written_reason():
    unauthenticated = [
        (fname, method, path)
        for fname, method, path, sig in _routes()
        if not any(dep in sig for dep in AUTH_DEPENDENCIES)
    ]
    undeclared = [r for r in unauthenticated if r not in PUBLIC_ROUTES]
    assert not undeclared, (
        "These routes have no authentication dependency and are not on the "
        "public allowlist. There is NO global auth middleware, so each one is "
        "reachable by anyone who can reach the API:\n  "
        + "\n  ".join(f"{m} /api/v1/...{p}   ({f})" for f, m, p in undeclared))


def test_allowlist_has_no_stale_entries():
    """A public route that was later secured (or deleted) must leave the list,
    so the allowlist stays a live statement rather than accumulated debt."""
    live = {(f, m, p) for f, m, p, _ in _routes()}
    stale = [k for k in PUBLIC_ROUTES if k not in live]
    assert not stale, f"allowlist entries no longer match any route: {stale}"


# ── the specific endpoints this test was written for ─────────────────────

PHI_ROUTES = [
    ("audio.py", "GET", "/transcripts/{patient_id}"),
    ("audio.py", "POST", "/transcribe"),
    ("audio.py", "POST", "/transcripts/{transcript_id}/approve-enrichment/{action_id}"),
    ("biometric.py", "POST", "/identify"),
    ("biometric.py", "POST", "/enroll"),
]


@pytest.mark.parametrize("fname,method,path", PHI_ROUTES)
def test_phi_and_biometric_routes_authenticate(fname, method, path):
    sig = next((s for f, m, p, s in _routes()
                if (f, m, p) == (fname, method, path)), None)
    assert sig is not None, f"route vanished: {method} {path} in {fname}"
    assert any(d in sig for d in AUTH_DEPENDENCIES), (
        f"{method} {path} handles patient audio or biometric templates and "
        f"must authenticate")


def test_transcript_read_is_scoped_to_the_callers_pharmacy():
    """Authentication alone is not enough for a multi-tenant deployment: an
    authenticated user from pharmacy A must not be able to read pharmacy B's
    conversations by guessing a patient id."""
    src = (ROUTERS / "audio.py").read_text(encoding="utf-8")
    body = src[src.index('@router.get("/transcripts/{patient_id}")'):]
    body = body[:body.index("\n@router") if "\n@router" in body else len(body)]
    assert "pharmacy_id" in body, (
        "GET /transcripts/{patient_id} does not filter by pharmacy_id")


def test_enrichment_approver_is_taken_from_the_session():
    """The approver's identity is the audit record for a clinical write, so it
    must come from the verified session — never from a caller-supplied field."""
    src = (ROUTERS / "audio.py").read_text(encoding="utf-8")
    start = src.index("async def approve_enrichment_action")
    sig = src[start:start + 700].split("\n)")[0]
    assert "pharmacist_id: UUID," not in sig, (
        "approve-enrichment still accepts a caller-supplied pharmacist_id; "
        "the approving pharmacist must be derived from the authenticated staff")
