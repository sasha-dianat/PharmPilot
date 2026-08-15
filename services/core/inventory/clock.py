"""One definition of "today", and it is the pharmacy's.

Three different clocks were in play, and they disagreed:

  * `CURRENT_DATE` in SQL — the *database session's* timezone. Measured on this
    installation that is `Asia/Kabul` (+04:30).
  * `date.today()` in Python — the *process's* timezone, wherever the API
    happens to run.
  * `pharmacies.timezone` — the only one that describes the shop. On this
    installation, `America/New_York` (−04:00).

Between the first and the third is an 8.5-hour spread, so for a third of every
day the "today" a query filters on names a different day than the pharmacy is
living in. A dispense at 20:00 on the 14th in New York is stamped `2026-08-15`
by `created_at::date`, and lands in the wrong demand window, on the wrong side
of an expiry check, and a day out in the cycle-count interval.

`created_at` is `timestamptz`, so the instant is stored correctly. The bug is
never in the instant; it is always in the *cast to a day*, which silently uses
whatever timezone the reader happens to be in.

The rule: a day boundary in the inventory section is the pharmacy's local
midnight. `as_of` is derived once, per request, from the pharmacy's own
timezone and passed down — never taken from the session or the process.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Used when a pharmacy has no timezone recorded. UTC rather than the server's
# zone: the server's is an accident of deployment, and picking it would make the
# same data read differently after a migration to another region.
FALLBACK_TZ = "UTC"


def zone(tz_name: str | None) -> ZoneInfo:
    """The pharmacy's zone, falling back to UTC rather than to the server's."""
    if not tz_name:
        return ZoneInfo(FALLBACK_TZ)
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo(FALLBACK_TZ)


def pharmacy_today(tz_name: str | None, *, now: datetime | None = None) -> date:
    """The date it currently is *in the shop*.

    Every window, expiry comparison and count interval in the inventory section
    should start here, so that "the last 28 days" means the same 28 days to the
    pharmacist and to the query.
    """
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(zone(tz_name)).date()


def local_date(moment: datetime | None, tz_name: str | None) -> date | None:
    """The pharmacy-local day an instant fell on.

    This is the cast that was going wrong. `moment.date()` gives the day in
    whatever zone the datetime is carrying, which for a `timestamptz` read back
    from Postgres is UTC — not the day the pharmacy filed it under.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(zone(tz_name)).date()


# SQL that casts a timestamptz column to the pharmacy's day. Used instead of
# `col::date`, which silently casts in the session's timezone.
def local_date_sql(column: str, param: str = "tz") -> str:
    return f"(({column}) AT TIME ZONE :{param})::date"
