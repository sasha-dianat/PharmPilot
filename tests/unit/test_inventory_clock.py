"""Day boundaries — whose midnight counts.

Three clocks were in play and they disagreed. Measured on this installation:
the database session runs `Asia/Kabul` (+04:30), the pharmacy is
`America/New_York` (−04:00), and the API process is wherever it happens to be.
An 8.5-hour spread means that for a third of every day, "today" in a query names
a different day than the pharmacy is living in.

The instant was never wrong — `created_at` is `timestamptz`. The bug is always
in the *cast to a day*, which silently uses the reader's zone.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from services.core.inventory import clock as CLK

# 20:00 on 14 August in New York. In UTC that is already the 15th.
EVENING_IN_NY = datetime(2026, 8, 15, 0, 0, tzinfo=timezone.utc)


def test_the_pharmacys_day_is_not_the_utc_day():
    """The exact case the demand window was getting wrong: an evening dispense
    filed under tomorrow."""
    assert EVENING_IN_NY.date() == date(2026, 8, 15)          # naive reading
    assert CLK.local_date(EVENING_IN_NY, "America/New_York") == date(2026, 8, 14)


def test_a_morning_east_of_utc_is_also_a_different_day():
    """The mirror case: 02:00 in Tehran is still yesterday in UTC."""
    moment = datetime(2026, 8, 14, 22, 30, tzinfo=timezone.utc)
    assert moment.date() == date(2026, 8, 14)
    assert CLK.local_date(moment, "Asia/Tehran") == date(2026, 8, 15)


def test_today_is_the_shops_today():
    assert CLK.pharmacy_today("America/New_York", now=EVENING_IN_NY) == date(2026, 8, 14)
    assert CLK.pharmacy_today("Asia/Tehran", now=EVENING_IN_NY) == date(2026, 8, 15)
    assert CLK.pharmacy_today("UTC", now=EVENING_IN_NY) == date(2026, 8, 15)


def test_a_naive_timestamp_is_read_as_utc_rather_than_local():
    """Guessing the process's zone here would make the same row read
    differently on a developer's laptop and on the server."""
    naive = datetime(2026, 8, 15, 0, 0)
    assert CLK.local_date(naive, "America/New_York") == date(2026, 8, 14)


def test_a_missing_timezone_falls_back_to_utc_not_the_servers_zone():
    """The server's zone is an accident of deployment. Falling back to it would
    make the same data read differently after a move to another region."""
    assert CLK.zone(None).key == "UTC"
    assert CLK.pharmacy_today(None, now=EVENING_IN_NY) == date(2026, 8, 15)


def test_an_unknown_timezone_does_not_crash_the_report():
    """A typo in a pharmacy record must not take the reconciliation down."""
    assert CLK.zone("Mars/Olympus").key == "UTC"
    assert CLK.pharmacy_today("not a zone", now=EVENING_IN_NY) == date(2026, 8, 15)


def test_no_moment_has_no_day():
    assert CLK.local_date(None, "America/New_York") is None


# ── daylight saving ───────────────────────────────────────────────────────
def test_the_day_is_right_across_a_spring_forward():
    """New York loses an hour on 8 March 2026. The date must not skip."""
    before = datetime(2026, 3, 8, 6, 30, tzinfo=timezone.utc)   # 01:30 EST
    after = datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)    # 03:30 EDT
    assert CLK.local_date(before, "America/New_York") == date(2026, 3, 8)
    assert CLK.local_date(after, "America/New_York") == date(2026, 3, 8)


def test_the_day_is_right_across_a_fall_back():
    """01:30 happens twice on 1 November 2026; both are still that day."""
    first = datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc)   # 01:30 EDT
    second = datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc)  # 01:30 EST
    assert CLK.local_date(first, "America/New_York") == date(2026, 11, 1)
    assert CLK.local_date(second, "America/New_York") == date(2026, 11, 1)


def test_a_zone_without_daylight_saving_is_handled_too():
    """Iran abolished DST in 2022; the offset is a flat +03:30 all year."""
    midsummer = datetime(2026, 7, 1, 21, 0, tzinfo=timezone.utc)
    midwinter = datetime(2026, 1, 1, 21, 0, tzinfo=timezone.utc)
    assert CLK.local_date(midsummer, "Asia/Tehran") == date(2026, 7, 2)
    assert CLK.local_date(midwinter, "Asia/Tehran") == date(2026, 1, 2)


# ── the SQL side ──────────────────────────────────────────────────────────
def test_the_sql_helper_casts_in_the_pharmacys_zone():
    """`col::date` casts in the session's timezone, which is why it was wrong."""
    sql = CLK.local_date_sql("pf.created_at")
    assert "AT TIME ZONE :tz" in sql
    assert "::date" in sql
    assert "CURRENT_DATE" not in sql


def test_the_demand_window_no_longer_uses_the_session_clock():
    """A guard on the query itself: CURRENT_DATE and a bare ::date cast are the
    two ways this defect comes back.

    The docstring is stripped before matching. The first version of this test
    failed against correct code because the function's own docstring explains
    the bug using the very strings it forbids — the same prose-matching trap the
    anomaly-bridge guard fell into.
    """
    import ast
    import inspect

    from services.platform.routers import inventory_integrity as IG

    tree = ast.parse(inspect.getsource(IG._demand_inputs).lstrip())
    fn = tree.body[0]
    if (fn.body and isinstance(fn.body[0], ast.Expr)
            and isinstance(fn.body[0].value, ast.Constant)):
        fn.body = fn.body[1:]                      # drop the docstring
    code = ast.unparse(fn)

    assert "CURRENT_DATE" not in code, "the session's clock is back"
    assert "created_at::date" not in code, "a bare cast is back"
    # The zone-aware cast itself lives in `local_date_sql`, so what this asserts
    # is the delegation: the query asks the helper for the day expression and
    # binds both the pharmacy's zone and its today.
    assert "local_date_sql" in code
    assert "_pharmacy_clock" in code
    assert "'tz': tz" in code and "'today': today" in code
