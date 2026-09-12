"""Admin surface for stratified calibration and the release gate.

Static checks in the style of tests/unit/test_route_authentication.py: no
database needed, and they catch the failure that actually happened on this
codebase — a route shipping with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ADMIN = Path("services/platform/routers/biometric_admin.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user")


def _handlers():
    src = ADMIN.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_the_two_new_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/admin/gallery/calibrate-strata") in paths
    assert ("GET", "/admin/gallery/release-gate") in paths


def test_every_admin_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_calibration_is_a_privileged_write():
    """Calibration sets the thresholds that decide identifications. Reading the
    gallery is one permission; moving the thresholds is another."""
    for verb, path, sig in _handlers():
        if path == "/admin/gallery/calibrate-strata":
            assert "biometric:write" in sig, sig


def test_the_gate_endpoint_reports_failures_not_just_a_verdict():
    """A gate that says only 'failed' cannot be acted on."""
    src = ADMIN.read_text(encoding="utf-8")
    body = src[src.index('"/admin/gallery/release-gate"'):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "failures" in body
    assert "report" in body


def test_an_uncalibrated_stratum_explains_why_it_cannot_vote():
    """`can_vote: false` with no reason sends the operator hunting. The whole
    point of stratified calibration is knowing WHICH subset is unmeasured."""
    src = ADMIN.read_text(encoding="utf-8")
    body = src[src.index('"/admin/gallery/calibrate-strata"'):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    assert "can_vote" in body
    assert "why_excluded" in body
