"""Zone codes: the registry lookup, and what to do with the nine that predate it.

A census of this tree found NINE zone vocabularies plus a tenth in the design
doc, and not one of them validated anything. This module is where they
converge.

The alias map is deliberately ONE-WAY — legacy spelling to canonical Z-code,
never the reverse. A reverse map would let a rename in the registry silently
reinterpret rows written years earlier.

`canonical()` does not guess. A code it has never seen passes through unchanged
and the registry refuses it downstream, which is the honest outcome: inventing
a mapping here would make an unregistered zone look registered.
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Legacy spelling -> canonical code from design doc 3.1.
#
# 'general_floor' (detector.py:131, the get_zone fallback) and 'entry'
# (detector.py:469, hard-coded at the call site) are included because the
# detector emits them at runtime and they appear in no zone config at all.
LEGACY_ZONE_ALIAS: dict[str, str] = {
    # services/biometric/behavioral_analysis/detector.py:137-144
    "waiting_area": "Z-WAIT",
    "dispensing_counter_public": "Z-COUNTER-1",
    "dispensing_counter_interior": "Z-COUNTER-1",
    "vault_room": "Z-CDS",
    "pharmacist_only": "Z-BACKOFFICE",
    "otc_shelves": "Z-OTC",
    "general_floor": "Z-WAIT",
    "entry": "Z-ENT",
    # security_events.event_metadata->>'camera_zone' — the only vocabulary
    # with live rows, seeded by scripts/seed_security_events.py:28-38.
    "parking_lot": "Z-DOCK",
    "staff_door": "Z-STAFFDOOR",
    "front_entrance": "Z-ENT",
    "stockroom": "Z-AISLE-A",
    "dispensing_1": "Z-COUNTER-1",
    "rear_door": "Z-STAFFDOOR",
    "back_entrance": "Z-STAFFDOOR",
    # pharmacy_shelves.zone — one writer in the tree, a test fixture
    # writing the literal 'main'.
    "main": "Z-AISLE-A",
    # services/audio/transcription/pipeline.py:ZONE_CONFIGS, deleted by this
    # task. Kept here so an old config or a stored row still resolves.
    "counter": "Z-COUNTER-1",
    "counseling_room": "Z-CONSULT",
    "drive_through": "Z-COUNTER-1",
}

_CACHE: dict[tuple[str, str], frozenset[str]] = {}


def canonical(legacy: str | None) -> str | None:
    """Map a legacy zone spelling to its canonical code, or pass it through."""
    if legacy is None:
        return None
    return LEGACY_ZONE_ALIAS.get(legacy, legacy)


def invalidate(pharmacy_id, site: str | None = None) -> int:
    """Drop cached zone sets after a registry write. Returns how many."""
    keys = [k for k in _CACHE
            if k[0] == str(pharmacy_id) and (site is None or k[1] == site)]
    for k in keys:
        _CACHE.pop(k, None)
    return len(keys)


async def load_zone_codes(db: AsyncSession, pharmacy_id, site: str, *,
                          force: bool = False) -> frozenset[str]:
    """Active zone codes for a site, from cache when possible.

    Keyed by (pharmacy, site) because the pharmacy and the depot have different
    floor plans. Sharing a slot would hand one site's zone set to the other and
    accept observations for zones that do not exist there — the same defect
    rf_mapping.store is keyed this way to avoid.
    """
    key = (str(pharmacy_id), site)
    if not force and key in _CACHE:
        return _CACHE[key]
    rows = (await db.execute(text("""
        SELECT code FROM vision_zone
        WHERE pharmacy_id = :pid AND site = :s
          AND active = true AND is_deleted = false"""),
        {"pid": pharmacy_id, "s": site})).scalars().all()
    codes = frozenset(rows)
    _CACHE[key] = codes
    return codes
