"""Survey capture endpoints.

Static checks in the style of tests/unit/test_route_authentication.py: no
database needed, and they catch the failure that actually happened on this
codebase — a route shipping with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/surveillance.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user", "require_ws_staff")


def _handlers():
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def _handler_body(marker: str, *, code_only: bool = False) -> str:
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index(marker):]
    nxt = body.find("\n@router")
    body = body[:nxt] if nxt > 0 else body
    if code_only:
        # Comments explaining a removed defect legitimately quote it. Strip
        # them so the check is about what the code DOES, not what it documents.
        body = "\n".join(l for l in body.splitlines()
                         if not l.lstrip().startswith("#"))
    return body


def test_the_three_survey_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/rf/access-points") in paths
    assert ("POST", "/rf/survey") in paths
    assert ("GET", "/rf/map-status") in paths


def test_every_route_still_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth"


def test_recording_a_survey_point_is_a_write_permission():
    """A survey defines where the system thinks people are. Reading a heatmap
    is one permission; moving the reference frame is another."""
    for verb, path, sig in _handlers():
        if path in ("/rf/survey", "/rf/access-points"):
            assert "write" in sig, f"{path}: {sig}"


def test_a_survey_write_invalidates_the_cached_map():
    """Otherwise the next fix is computed against the map as it was before the
    survey, and the surveyor sees no effect from their work."""
    assert "invalidate" in _handler_body('"/rf/survey"')


def test_the_batch_endpoint_no_longer_hard_codes_an_empty_radio_map():
    """The defect this phase exists to fix: RadioMap([]) meant locate() always
    fell through to trilateration and fingerprinting never ran."""
    body = _handler_body("async def ingest_rf_batch", code_only=True)
    assert "RadioMap([])" not in body, (
        "rf/batch still passes an empty radio map, so fingerprinting cannot run")
    assert "load_radio_map" in body


def test_the_batch_endpoint_uses_the_stored_access_points():
    """APs arriving in the request body meant every edge caller carried the
    whole layout and no two were guaranteed to agree."""
    assert "load_access_points" in _handler_body("async def ingest_rf_batch")


def test_the_fix_reports_which_method_produced_it():
    """Fingerprint and trilateration differ by roughly 3x in error. A caller
    that cannot tell them apart cannot weigh the result."""
    assert "method" in _handler_body("async def ingest_rf_batch")
