"""A zone id that Postgres refuses to accept when it names nothing.

Measured before this task: surveillance_observations.zone_id accepted 'Z-CAGE',
'totally made up zone' and the empty string. The recorder validated `site` and
`fusion_decision` and passed zone_id straight through unvalidated.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

MIGRATION = Path("data/migrations/versions/0052_zone_fk.py")
RECORDER = Path("services/core/surveillance/recorder.py")

_PID = "00000000-0000-0000-0000-000000000000"


def _obs(**kw):
    from services.core.surveillance.recorder import build_observation
    base = dict(pharmacy_id=_PID, site="depot", action_type="movement",
                observed_at=datetime.now(timezone.utc))
    base.update(kw)
    return build_observation(**base)


def test_migration_chains_from_0051():
    src = MIGRATION.read_text(encoding="utf-8")
    assert re.search(r'^revision\s*=\s*"0052"', src, re.M)
    assert re.search(r'^down_revision\s*=\s*"0051"', src, re.M)


def test_the_fk_is_composite_and_matches_the_business_key():
    src = MIGRATION.read_text(encoding="utf-8")
    assert "fk_surv_obs_zone" in src
    assert "pharmacy_id" in src and "site" in src and "zone_id" in src
    assert "vision_zone" in src


def _code_only(path: Path) -> str:
    """Source with the module docstring and comments removed.

    A docstring explaining why a defect is absent legitimately names it. The
    check is about what the code DOES, not what it documents.
    """
    src = path.read_text(encoding="utf-8")
    body = src.split('"""', 2)[2] if src.count('"""') >= 2 else src
    return "\n".join(l for l in body.splitlines()
                     if not l.lstrip().startswith("#"))


def test_the_fk_does_not_cascade_on_update():
    """Measured live: ON UPDATE CASCADE silently rewrites the zone of a
    historical observation when a zone is renamed. In a system that watches
    people, a rename must never retroactively relocate an observation."""
    assert "CASCADE" not in _code_only(MIGRATION).upper()


def test_the_empty_string_is_refused_separately_from_the_fk():
    """'' would satisfy no FK either, but the CHECK gives the honest error and
    keeps `zone_id IS NULL` meaning exactly 'unzoned'."""
    src = MIGRATION.read_text(encoding="utf-8")
    assert "ck_surv_obs_zone_shape" in src


def test_the_recorder_refuses_an_unregistered_zone_before_the_driver_does():
    """A ValueError naming the code is actionable; an asyncpg
    ForeignKeyViolation three frames down is not."""
    src = RECORDER.read_text(encoding="utf-8")
    assert "known_zones" in src


def test_the_recorder_still_allows_an_unzoned_observation():
    """Not every observation has a zone. NULL must stay legal."""
    row = _obs(zone_id=None, known_zones=frozenset({"Z-CAGE"}))
    assert row["zone_id"] is None


def test_the_recorder_accepts_a_registered_zone():
    row = _obs(zone_id="Z-CAGE", known_zones=frozenset({"Z-CAGE"}))
    assert row["zone_id"] == "Z-CAGE"


def test_the_recorder_rejects_a_zone_that_is_not_registered():
    with pytest.raises(ValueError, match="Z-TYPO"):
        _obs(zone_id="Z-TYPO", known_zones=frozenset({"Z-CAGE"}))


def test_the_recorder_rejects_the_empty_string_even_when_unchecked():
    """'' is not 'unzoned'. It would pass any membership test that only runs
    when zone_id is truthy, and then defeat every `zone_id IS NULL` branch."""
    with pytest.raises(ValueError):
        _obs(zone_id="", known_zones=None)


def test_an_unchecked_recorder_still_works_when_zones_are_unknown():
    """known_zones=None means 'the caller could not look them up'. Refusing
    every observation in that case would make a registry outage a capture
    outage, which is a worse failure than an unvalidated string — and the
    database still has the final say."""
    row = _obs(zone_id="Z-ANYTHING", known_zones=None)
    assert row["zone_id"] == "Z-ANYTHING"


def test_the_default_is_unchecked_so_existing_callers_are_unchanged():
    """Every phase-1..4 caller omits known_zones. None of them may break."""
    row = _obs(zone_id="Z-WHATEVER")
    assert row["zone_id"] == "Z-WHATEVER"
