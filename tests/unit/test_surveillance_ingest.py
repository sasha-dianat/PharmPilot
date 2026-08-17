"""Surveillance ingest endpoints.

Static checks, in the style of tests/unit/test_route_authentication.py: these
run without a database and catch the failure that actually happened on this
codebase — a PHI-adjacent route shipped with no auth dependency.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTER = Path("services/platform/routers/surveillance.py")
MAIN = Path("services/platform/main.py")

AUTH = ("require_permission", "require_pharmacist", "get_current_staff",
        "get_current_user", "require_ws_staff")


def _handlers() -> list[tuple[str, str, str]]:
    src = ROUTER.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r'@router\.(get|post)\(\s*"([^"]*)"', src):
        seg = src[m.start(): m.start() + 2000]
        sig = seg.split("\n)")[0] if "\n)" in seg[:2000] else seg[:900]
        out.append((m.group(1).upper(), m.group(2), sig))
    return out


def test_the_three_endpoints_exist():
    paths = {(v, p) for v, p, _ in _handlers()}
    assert ("POST", "/observations") in paths
    assert ("POST", "/rf/batch") in paths
    assert ("GET", "/heatmap") in paths


def test_every_surveillance_route_authenticates():
    for verb, path, sig in _handlers():
        assert any(a in sig for a in AUTH), f"{verb} {path} has no auth dependency"


def test_router_is_registered_in_main():
    src = MAIN.read_text(encoding="utf-8")
    assert "surveillance" in src
    assert "/api/v1/surveillance" in src


def test_ingest_never_accepts_raw_media():
    """Frames, crops and audio stay on the edge node. The ingest schema must not
    give them a field to arrive in."""
    src = ROUTER.read_text(encoding="utf-8")
    body = src[src.index("class ObservationIn"):]
    body = body[:body.index("\nclass ")]
    for forbidden in ("image", "frame", "crop", "audio", "embedding", "bytes"):
        assert forbidden not in body.lower(), (
            f"ObservationIn exposes a '{forbidden}' field — raw media and "
            f"embeddings must never cross the site boundary")


def test_heatmap_is_read_only():
    for verb, path, _ in _handlers():
        if path == "/heatmap":
            assert verb == "GET"
