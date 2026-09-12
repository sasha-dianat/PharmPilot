"""The zone registry: the first thing in this system that makes a zone a key.

Nine free-text zone vocabularies exist across this codebase and not one of them
is validated. Verified against the live schema in a rolled-back transaction:
'Z-CAGE', 'totally made up zone' and the EMPTY STRING were all accepted into
surveillance_observations.zone_id; only length >= 41 was rejected, and that is
varchar truncation rather than a domain check.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATION = Path("data/migrations/versions/0051_vision_zone.py")
MODEL = Path("shared/models/vision.py")


def test_migration_chains_from_the_current_head():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0051"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0050"', src, re.M)


def test_the_zone_carries_a_business_key_not_just_a_uuid():
    """A uuid PK cannot be referenced by surveillance_observations.zone_id,
    which is varchar(40). The composite (pharmacy_id, site, code) can."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "uq_zone_code_per_site" in src
    for col in ('"pharmacy_id"', '"site"', '"code"'):
        assert col in src


def test_constraint_names_follow_the_house_convention():
    """23 of 28 live CHECK constraints drop the table's namespace word and
    singularise. Guessing ck_<full_table>_<column> is the phase-3 mistake."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ck_vision_zone" not in src, "drop the namespace word: ck_zone_*"
    assert "ck_zone_class" in src
    assert "ck_zone_code_shape" in src


def test_a_zone_class_vocabulary_covers_the_design_doc():
    """Including 'credentialed' (Z-STAFFDOOR) and 'prohibited' (toilets,
    changing rooms, prayer room) — both are in the doc's own table and both
    are easy to omit."""
    from shared.models.vision import ZONE_CLASSES
    for c in ("public", "service", "restricted", "high_risk", "safety",
              "credentialed", "prohibited"):
        assert c in ZONE_CLASSES


def test_every_state_column_is_wide_enough_for_its_vocabulary():
    """Migration 0046 widened a CHECK to admit a 17-char value while the column
    stayed VARCHAR(12). House headroom is max_len + 2, rounded up to a multiple
    of four."""
    from shared.models.vision import ZONE_CLASSES
    src = MIGRATION.read_text(encoding="utf-8")
    longest = max(len(c) for c in ZONE_CLASSES)          # 'credentialed' = 12
    assert longest <= 16
    assert "sa.String(16)" in src


def test_retention_days_may_be_null_because_nothing_enforces_it_yet():
    """Provenance rule: a policy number nobody purges against is a claim the
    system cannot honour. NULL says 'not set', a default would say 'decided'."""
    src = MODEL.read_text(encoding="utf-8")
    assert "retention_days" in src
    assert re.search(r"retention_days.*Optional|retention_days.*\|\s*None",
                     src), "retention_days must be nullable"


def test_the_model_is_registered():
    init = Path("shared/models/__init__.py").read_text(encoding="utf-8")
    assert "vision" in init
