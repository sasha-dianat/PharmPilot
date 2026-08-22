"""Access points and a surveyed radio map, stored.

The RF engine has had fingerprinting since phase 1 and it has never run in
production: the live endpoint passes RadioMap([]), so locate() always falls
through to trilateration — the method the module itself calls 5-15m and "close
to useless" in a depot. This phase supplies the missing data.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0050_rf_survey.py")
MODEL = Path("shared/models/rf_survey.py")


def test_migration_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0050"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0049"', src, re.M)


def test_both_tables_are_created():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "rf_access_points" in src
    assert "rf_fingerprints" in src


def test_an_access_point_is_unique_per_site():
    """The same BSSID can legitimately appear at two sites; within one site a
    duplicate would give the trilateration two contradictory positions."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "UniqueConstraint" in src or "unique_constraint" in src
    assert "ap_id" in src


def test_a_fingerprint_records_when_it_was_surveyed():
    """A radio map is a photograph of a building's RF environment. Move a
    shelving run and it is wrong in ways that produce confident bad fixes."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "surveyed_at" in src


def test_a_fingerprint_records_how_many_aps_it_saw():
    """A survey point that heard one AP cannot constrain a position. Storing the
    count makes a thin fingerprint filterable instead of silently weightless."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ap_count" in src


def test_the_models_exist_and_are_registered():
    src = MODEL.read_text(encoding="utf-8")
    assert "class RfAccessPoint" in src
    assert "class RfFingerprint" in src
    init = Path("shared/models/__init__.py").read_text(encoding="utf-8")
    assert "rf_survey" in init


# ── loading and filtering ────────────────────────────────────────────────

import pytest

from services.core.rf_mapping import store as S


def test_thin_fingerprints_are_excluded_from_the_map():
    """A survey point that heard fewer than three APs cannot constrain a
    position, and including it drags the weighted average toward wherever it
    happened to be. Filtered, not down-weighted."""
    rows = [
        {"x": 1.0, "y": 2.0, "rssi": {"a": -50, "b": -60, "c": -70}, "ap_count": 3},
        {"x": 9.0, "y": 9.0, "rssi": {"a": -80}, "ap_count": 1},
    ]
    fps = S.rows_to_fingerprints(rows, min_aps=3)
    assert len(fps) == 1
    assert (fps[0].x, fps[0].y) == (1.0, 2.0)


def test_a_fingerprint_keeps_every_ap_it_heard():
    rows = [{"x": 0.0, "y": 0.0, "rssi": {"a": -50, "b": -60, "c": -70},
             "ap_count": 3}]
    fp = S.rows_to_fingerprints(rows)[0]
    assert set(fp.rssi) == {"a", "b", "c"}


def test_an_empty_map_is_returned_rather_than_none():
    """locate() takes a RadioMap and checks its length. Returning None here
    would move the emptiness check into every caller."""
    assert len(S.rows_to_fingerprints([])) == 0


def test_staleness_is_reported_in_days():
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=200)
    assert S.age_days(old) == pytest.approx(200, abs=1)
    assert S.age_days(None) is None


def test_a_map_older_than_the_threshold_is_flagged_stale():
    """Not blocked — flagged. A stale map still beats trilateration; the
    operator needs to know it is aging, not be locked out of positioning."""
    from datetime import datetime, timedelta, timezone
    old = datetime.now(timezone.utc) - timedelta(days=S.STALE_AFTER_DAYS + 10)
    fresh = datetime.now(timezone.utc) - timedelta(days=5)
    assert S.is_stale(old) is True
    assert S.is_stale(fresh) is False
    assert S.is_stale(None) is True          # never surveyed counts as stale


def test_the_cache_is_keyed_by_site():
    """The pharmacy and the depot have different floor plans and different APs.
    Sharing a slot would hand one site's radio map to the other."""
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert len(S._CACHE) == 2
    assert S.invalidate("p1", "depot") == 1
    assert ("p1", "pharmacy") in S._CACHE


def test_invalidating_a_pharmacy_clears_every_site():
    S._CACHE.clear()
    from services.core.rf_mapping import RadioMap
    S._CACHE[("p1", "depot")] = (RadioMap([]), None)
    S._CACHE[("p1", "pharmacy")] = (RadioMap([]), None)
    assert S.invalidate("p1") == 2
    assert S._CACHE == {}
