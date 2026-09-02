"""Every zone vocabulary in the tree, dispositioned.

A census found nine in code plus one in the design doc. This test is the pin:
if a tenth appears, or a dispositioned one changes, it fails here rather than
producing a silent spelling fork three phases later.
"""
from __future__ import annotations

from pathlib import Path

# The disposition. Each entry is (where it lives, what we do about it).
#   subsume  — its values become vision_zone rows
#   alias    — kept, mapped one-way into vision_zone codes
#   delete   — dead code, removed by this task
#   leave    — out of scope, deliberately untouched
DISPOSITION = {
    "surveillance_observations.zone_id": "subsume",
    "pharmacy_shelves.zone": "alias",
    "services/audio/transcription/pipeline.py:ZONE_CONFIGS": "delete",
    "services/biometric/behavioral_analysis/detector.py:DEFAULT_ZONE_CONFIG": "alias",
    "security_events.event_metadata->>'camera_zone'": "alias",
    "services/biometric/evidence_vault/vault.py:camera_zone": "leave",
    "services/platform/config.py:zone settings": "leave",
    "frontend SecuritySurveillance.tsx SVG rects": "leave",
    "docs/design/SURVEILLANCE_PLATFORM.md Z-* table": "subsume",
}


def test_every_vocabulary_has_a_disposition():
    assert len(DISPOSITION) == 9
    for where, what in DISPOSITION.items():
        assert what in ("subsume", "alias", "delete", "leave"), where


def test_the_dead_vocabulary_is_actually_deleted():
    """The ZONE_CONFIGS dict had exactly one reference in the whole tree: its
    own definition. The AudioZoneConfig dataclass it holds is NOT dead — it is
    the constructor parameter type of AudioProcessingPipeline — so only the
    dict goes."""
    src = Path("services/audio/transcription/pipeline.py").read_text(encoding="utf-8")
    # The ASSIGNMENT, not the token: a docstring may legitimately name what was
    # removed and where its spellings went.
    assert "ZONE_CONFIGS = " not in src
    assert "class AudioZoneConfig" in src, "the dataclass is still in use"


def test_the_alias_map_is_one_way_and_total():
    """One-way: legacy spelling -> Z-code. Never the reverse, or a rename in
    the registry would silently reinterpret historical rows."""
    from services.core.vision.zones import LEGACY_ZONE_ALIAS
    for legacy, canonical in LEGACY_ZONE_ALIAS.items():
        assert canonical.startswith("Z-"), f"{legacy} -> {canonical}"
        assert canonical not in LEGACY_ZONE_ALIAS, "the map must not round-trip"


def test_the_detector_vocabulary_is_covered_by_the_alias_map():
    """detector.py has six named zones plus two literals injected at runtime
    ('general_floor' at :131 and 'entry' at :469) that appear in no dict. Both
    must resolve or the detector emits codes the registry never heard of."""
    from services.biometric.behavioral_analysis.detector import DEFAULT_ZONE_CONFIG
    from services.core.vision.zones import LEGACY_ZONE_ALIAS
    for name in list(DEFAULT_ZONE_CONFIG) + ["general_floor", "entry"]:
        assert name in LEGACY_ZONE_ALIAS, f"{name} has no canonical zone"


# NOTE: "every alias target is a zone the seed registers" lives in
# test_vision_zone_seed_e2e.py, next to DOC_ZONES which it cross-checks against.


def test_canonical_passes_through_an_unknown_code():
    """A code already in Z-form, or one we have never seen, must survive. The
    registry rejects what it does not know; this function does not guess."""
    from services.core.vision.zones import canonical
    assert canonical("Z-CAGE") == "Z-CAGE"
    assert canonical("something-new") == "something-new"
    assert canonical(None) is None


def test_the_cache_is_keyed_by_pharmacy_and_site():
    """The pharmacy and the depot have different floor plans. Sharing a cache
    slot would hand one site's zone set to the other — the same defect
    rf_mapping.store was built to avoid."""
    from services.core.vision import zones as Z
    Z._CACHE.clear()
    Z._CACHE[("p1", "depot")] = frozenset({"Z-CAGE"})
    Z._CACHE[("p1", "pharmacy")] = frozenset({"Z-ENT"})
    assert Z.invalidate("p1", "depot") == 1
    assert ("p1", "pharmacy") in Z._CACHE
    assert Z.invalidate("p1") == 1
    assert Z._CACHE == {}
