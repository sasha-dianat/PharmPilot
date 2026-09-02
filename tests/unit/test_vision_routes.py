"""The vision router: authentication, tenancy, and no dead permissions.

No test in this repo enforces tenancy scoping on a NEW table or route —
test_router_http_tenant_isolation.py imports only label_engine and
prescriptions. A tenancy-blind vision endpoint would ship green. This is that
missing test, landing with the FIRST route rather than after four more.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/vision.py")
AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user")


def _handlers():
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post|patch)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_every_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_reading_and_writing_zones_are_different_permissions():
    """A zone defines where policy applies. Reading the map is one thing;
    moving the boundary is another."""
    seen = set()
    for verb, path, sig in _handlers():
        if path == "/zones" and verb == "POST":
            assert "vision:write" in sig
            seen.add("post")
        if path == "/zones" and verb == "GET":
            assert "vision:read" in sig
            seen.add("get")
    assert seen == {"get", "post"}, f"missing a /zones handler: {seen}"


def test_no_handler_takes_pharmacy_id_from_the_request():
    """Tenancy comes from the authenticated staff row, never from the caller.
    There is a still-open defect in this repo where an endpoint is
    permission-gated but not tenancy-gated; this is what makes it
    unrepeatable rather than merely un-repeated."""
    src = ROUTER.read_text(encoding="utf-8")
    for bad in ("pharmacy_id: UUID = Query", "pharmacy_id: UUID = Body",
                "body.pharmacy_id"):
        assert bad not in src, f"tenancy must not come from the request: {bad}"
    assert "staff.pharmacy_id" in src


def test_every_query_is_scoped_to_the_staff_pharmacy():
    """A SELECT over vision_zone with no pharmacy filter returns every
    tenant's zones. Count the scoping clauses against the queries."""
    src = ROUTER.read_text(encoding="utf-8")
    selects = src.count("select(VisionZone")
    scoped = src.count("VisionZone.pharmacy_id == staff.pharmacy_id")
    assert selects > 0 and scoped >= selects, (
        f"{selects} queries, {scoped} scoped")


def test_only_two_vision_permissions_are_declared():
    """audio:read is already a dead permission granted to two roles and used by
    zero routes. A declared-but-unused permission is the same rot."""
    src = Path("shared/models/auth.py").read_text(encoding="utf-8")
    declared = set(re.findall(r'"(vision:[a-z:]+)"', src))
    assert declared == {"vision:read", "vision:write"}, declared


def test_both_declared_permissions_are_actually_used_by_a_route():
    """The inverse of the rot: a permission nobody enforces."""
    src = ROUTER.read_text(encoding="utf-8")
    for perm in ("vision:read", "vision:write"):
        assert perm in src, f"{perm} is declared but no route requires it"


def test_the_router_is_mounted():
    src = Path("services/platform/main.py").read_text(encoding="utf-8")
    assert "vision" in src
