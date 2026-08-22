"""Calibration is per occlusion stratum, not per modality.

A single pooled ImpostorStats averages veiled faces in with unoccluded ones and
returns a threshold that is too LOW for the veiled subset — so the FPIR
guarantee silently fails for exactly the group it most affects. ImpostorStats
carries a `population` field for this and nothing has ever populated it.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0048_calibration_strata.py")


def test_migration_exists_and_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0048"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0047"', src, re.M)


def test_stratum_is_added_to_the_score_stats_table():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_score_stats" in src
    assert "stratum" in src


def test_the_unique_key_includes_the_stratum():
    """Without this a second stratum's calibration overwrites the first, and the
    table silently holds one row where it should hold four."""
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def upgrade"):src.index("def downgrade")]
    assert "stratum" in body
    # the old three-column key must be dropped, not merely supplemented
    assert "drop_constraint" in body or "DROP CONSTRAINT" in body


def test_templates_gain_a_shadow_flag():
    """A shadow model must be enrollable and measurable without voting."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "biometric_templates" in src
    assert "shadow" in src


def test_downgrade_is_written():
    src = MIGRATION.read_text(encoding="utf-8")
    body = src[src.index("def downgrade"):]
    assert "stratum" in body and "shadow" in body
