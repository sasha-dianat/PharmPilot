"""Loading the access-point registry and the surveyed radio map.

Both are read on every RF ingest and written only during a survey, so they are
cached with explicit invalidation — the same shape as
`identity_resolution.repository._CACHE`, so the two behave alike.

The cache is keyed by (pharmacy, site) because the pharmacy and the depot have
different floor plans and different access points. Sharing a slot would hand one
site's radio map to the other and produce fixes that look plausible and are
somewhere else entirely.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from . import AccessPoint, Fingerprint, RadioMap

# A survey point that heard fewer than this cannot constrain a position.
MIN_FINGERPRINT_APS = 3
# A radio map ages as the building changes. Flagged, not blocked: a stale map
# still beats trilateration, and the operator needs to know rather than be
# locked out of positioning.
STALE_AFTER_DAYS = 180

_CACHE: dict[tuple[str, str], tuple[RadioMap, datetime | None]] = {}


def invalidate(pharmacy_id, site: str | None = None) -> int:
    """Drop cached radio maps after a survey write. Returns how many."""
    keys = [k for k in _CACHE
            if k[0] == str(pharmacy_id) and (site is None or k[1] == site)]
    for k in keys:
        _CACHE.pop(k, None)
    return len(keys)


def age_days(surveyed_at: datetime | None) -> float | None:
    if surveyed_at is None:
        return None
    now = datetime.now(timezone.utc)
    if surveyed_at.tzinfo is None:
        surveyed_at = surveyed_at.replace(tzinfo=timezone.utc)
    return (now - surveyed_at).total_seconds() / 86400.0


def is_stale(surveyed_at: datetime | None) -> bool:
    """Never surveyed counts as stale — there is nothing to trust."""
    age = age_days(surveyed_at)
    return True if age is None else age > STALE_AFTER_DAYS


def rows_to_fingerprints(rows, min_aps: int = MIN_FINGERPRINT_APS
                         ) -> list[Fingerprint]:
    """Database rows to fingerprints, dropping the ones too thin to help.

    Filtered rather than down-weighted: a point that heard one AP does not
    merely contribute little, it drags the inverse-distance weighted average
    toward wherever it happened to be.
    """
    out: list[Fingerprint] = []
    for r in rows:
        rssi = dict(r["rssi"] or {})
        if len(rssi) < min_aps:
            continue
        out.append(Fingerprint(x=float(r["x"]), y=float(r["y"]),
                               rssi={k: float(v) for k, v in rssi.items()}))
    return out


async def load_access_points(db: AsyncSession, pharmacy_id,
                             site: str) -> dict[str, AccessPoint]:
    """Active access points for a site, keyed by ap_id."""
    rows = (await db.execute(text("""
        SELECT ap_id, x, y, tx_power_dbm FROM rf_access_points
        WHERE pharmacy_id = :pid AND site = :s AND active = true"""),
        {"pid": pharmacy_id, "s": site})).mappings().all()
    return {r["ap_id"]: AccessPoint(ap_id=r["ap_id"], x=float(r["x"]),
                                    y=float(r["y"]),
                                    tx_power_dbm=float(r["tx_power_dbm"]))
            for r in rows}


async def load_radio_map(db: AsyncSession, pharmacy_id, site: str, *,
                         min_aps: int = MIN_FINGERPRINT_APS,
                         force: bool = False) -> RadioMap:
    """The surveyed radio map for a site, from cache when possible."""
    key = (str(pharmacy_id), site)
    if not force and key in _CACHE:
        return _CACHE[key][0]

    rows = (await db.execute(text("""
        SELECT x, y, rssi, surveyed_at FROM rf_fingerprints
        WHERE pharmacy_id = :pid AND site = :s
        ORDER BY surveyed_at DESC"""),
        {"pid": pharmacy_id, "s": site})).mappings().all()

    rmap = RadioMap(rows_to_fingerprints(rows, min_aps=min_aps))
    newest = rows[0]["surveyed_at"] if rows else None
    _CACHE[key] = (rmap, newest)
    return rmap


async def map_age_days(db: AsyncSession, pharmacy_id,
                       site: str) -> float | None:
    """How old the newest fingerprint for this site is, in days."""
    newest = (await db.execute(text("""
        SELECT max(surveyed_at) FROM rf_fingerprints
        WHERE pharmacy_id = :pid AND site = :s"""),
        {"pid": pharmacy_id, "s": site})).scalar()
    return age_days(newest)
