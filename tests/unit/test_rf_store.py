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
