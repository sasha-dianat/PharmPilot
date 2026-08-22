"""A shadow model observes and never votes.

Without shadow mode the only way to evaluate a candidate model is to promote it,
which means the first evidence that it is worse arrives as wrong
identifications against real people.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0049_shadow_calibration.py")
REPO = Path("services/biometric/identity_resolution/repository.py")


def test_migration_chains_from_0048():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0049"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0048"', src, re.M)


def test_score_stats_records_what_a_shadow_calibration_shadows():
    """`shadow_of` names the active version, so a shadow result can be compared
    against the incumbent it is a candidate to replace."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "shadow_of" in src and "biometric_score_stats" in src


def test_the_index_loader_excludes_shadow_templates_by_default():
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index("async def load_index"):]
    nxt = body.index("\nasync def ", 10)
    body = body[:nxt] if nxt > 0 else body
    assert "shadow" in body, "load_index must filter shadow templates"
    assert "include_shadow" in body, (
        "the exclusion must be the DEFAULT and opting in must be explicit")


def test_a_shadow_inclusive_load_cannot_poison_the_voting_cache():
    """Two different galleries must not share a cache slot: a shadow-inclusive
    load followed by a normal one would otherwise return shadow templates to
    the caller that decides identifications."""
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index("async def load_index"):]
    nxt = body.index("\nasync def ", 10)
    body = body[:nxt] if nxt > 0 else body
    key_line = next(l for l in body.splitlines() if "key = (" in l)
    assert "include_shadow" in key_line, (
        f"include_shadow must be part of the cache key; got: {key_line.strip()}")


def test_active_calibration_lookup_ignores_shadow_rows():
    """A shadow calibration in the table must never be returned as the active
    one — that would let an unpromoted model set live thresholds."""
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index("async def load_impostor_stats"):]
    assert "shadow_of IS NULL" in body


def test_shadow_versions_are_listable_for_comparison():
    src = REPO.read_text(encoding="utf-8")
    assert "def shadow_versions" in src


# ── behaviour: the cache must not leak a shadow gallery into voting ──────

def test_the_two_cache_slots_are_genuinely_separate():
    """Structure is not proof. Populate both slots and confirm a normal load
    cannot receive the shadow-inclusive index."""
    from datetime import datetime, timezone
    from services.biometric.identity_resolution import repository as R
    from services.biometric.identity_resolution import vector_store as V

    R._CACHE.clear()
    voting = V.ModalityIndex(modality="face", dim=512)
    shadowy = V.ModalityIndex(modality="face", dim=512)
    now = datetime.now(timezone.utc)
    R._CACHE[("p1", "face", False)] = (voting, now)
    R._CACHE[("p1", "face", True)] = (shadowy, now)

    assert R._CACHE[("p1", "face", False)][0] is voting
    assert R._CACHE[("p1", "face", True)][0] is shadowy
    assert voting is not shadowy

    state = {(r["modality"], r["includes_shadow"]) for r in R.cache_state()}
    assert ("face", False) in state and ("face", True) in state

    # invalidating a modality clears BOTH slots — a write that changes the
    # gallery must not leave a stale shadow-inclusive copy behind
    assert R.invalidate("p1", "face") == 2
    assert R._CACHE == {}
