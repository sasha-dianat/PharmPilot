"""E13 — a short fill is not a shortage, and the difference costs money.

Service ⑰ scored "every supplier is out of it" and "this one supplier is
rationing it" identically, and recommended buffer stock for both. Buffering
against a problem a phone call solves means paying to hold inventory that will
expire on the shelf. Most of what is pinned here is that distinction, and the
refusals around it.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from services.core.inventory import lead_time as LT
from services.core.inventory import shortage as SH

TODAY = date(2026, 8, 17)
T0 = datetime(2026, 5, 1, tzinfo=timezone.utc)


def line(supplier="acme", ordered=100, received=100, day=0, status="complete"):
    return {"wholesaler": supplier, "quantity_ordered": Decimal(str(ordered)),
            "quantity_received": Decimal(str(received)), "status": status,
            "ordered_at": T0 + timedelta(days=day)}


def item(lines, *, on_hand=1000, adq=10, basis="observed", ndc="N1"):
    return {"ndc11": ndc, "drug_name": "test drug", "lines": lines,
            "on_hand": Decimal(str(on_hand)),
            "avg_daily_demand": None if adq is None else Decimal(str(adq)),
            "demand_basis": basis}


def leads(**kw):
    """Observed lead times, so the horizon is measured rather than assumed."""
    return {name: LT.estimate(
        [{"ordered_at": T0, "received_at": T0 + timedelta(days=d)}] * 4,
        supplier=name) for name, d in kw.items()}


L = leads(acme=6, other=6)


def full(n, supplier="acme"):
    return [line(supplier, day=i) for i in range(n)]


def short(n, supplier="acme", received=50):
    return [line(supplier, received=received, day=i) for i in range(n)]


# ── the distinction the whole engine exists for ───────────────────────────
def test_short_from_every_supplier_is_the_market():
    s = SH.assess_item(item(short(4) + short(4, "other")), leads=L)
    assert s.verdict == "market_shortage"
    assert "this is the market, not the relationship" in s.explanation


def test_short_from_one_while_another_delivers_is_that_supplier():
    """The molecule is available. Moving the order costs nothing; buying cover
    costs money and shelf life."""
    s = SH.assess_item(item(short(4) + full(4, "other")), leads=L)
    assert s.verdict == "supplier_shortage"
    assert s.action == "switch_supplier"
    assert s.short_suppliers == ["acme"] and s.filling_suppliers == ["other"]


def test_the_two_cases_do_not_get_the_same_action():
    market = SH.assess_item(item(short(4) + short(4, "other")), leads=L)
    one = SH.assess_item(item(short(4) + full(4, "other")), leads=L)
    assert market.action != one.action
    assert one.suggested_buffer is None or market.action == "alert_prescribers"


def test_a_market_shortage_with_thin_cover_escalates_to_the_prescribers():
    """Nothing on the shelf and nothing coming is not a purchasing problem any
    more — somebody has to stop writing the prescription."""
    s = SH.assess_item(item(short(4) + short(4, "other"), on_hand=20, adq=10),
                       leads=L)
    assert (s.verdict, s.action, s.severity) == (
        "market_shortage", "alert_prescribers", "critical")


# ── absence of evidence ───────────────────────────────────────────────────
def test_a_molecule_never_ordered_has_no_fill_rate():
    """⑰ returned 1.0 here, reporting a flawless supply record for every drug
    the pharmacy had never bought."""
    assert SH._rate(Decimal("0"), Decimal("0")) is None
    s = SH.assess_item(item([]), leads=L)
    assert s.verdict == "unknown"
    assert s.fill_rate is None


def test_too_few_settled_lines_scores_nothing():
    s = SH.assess_item(item(short(2)), leads=L)
    assert s.verdict == "unknown"
    assert s.basis == "insufficient_history"
    assert "guess wearing a decimal point" in s.explanation


def test_lines_still_in_flight_are_not_counted_as_short_fills():
    """An order placed yesterday has not failed to arrive. ⑰ counted it."""
    s = SH.assess_item(item(full(4) + [line(received=0, status="partial")] * 6),
                       leads=L)
    assert s.settled_lines == 4
    assert s.fill_rate == Decimal("1.000")


def test_no_measured_demand_leaves_cover_unknown_not_infinite():
    """`COALESCE(avg_daily_demand, 0)` made an unmeasured item look like it would
    last for ever, and it dropped off the report entirely."""
    s = SH.assess_item(item(full(4), adq=None, basis="no_history"), leads=L)
    assert s.days_of_cover is None
    assert any("unknown rather than as plenty" in c for c in s.concerns)


def test_an_unmeasured_horizon_says_so():
    """No delivered orders means the horizon is the declared default, and a
    horizon presented as measured is what phase 3 removed from two modules."""
    s = SH.assess_item(item(short(4) + short(4, "other")), leads={})
    assert s.lead_basis == "declared_default"
    assert s.lead_days == LT.DECLARED_DEFAULT_DAYS
    assert any("declared assumption" in c for c in s.concerns)


# ── the slow trim a trailing average hides ────────────────────────────────
def test_orders_being_quietly_trimmed_are_caught_before_they_bite():
    """A supplier rationing a molecule rarely refuses an order — it shaves each
    one. Twenty clean deliveries then four at 85% leaves the aggregate at 97.5%,
    comfortably above the short-fill threshold, precisely *because* the past was
    good. The aggregate is the wrong statistic and this is the case that proves
    it."""
    creeping = ([line(day=i) for i in range(20)]
                + [line(received=85, day=20 + i) for i in range(4)])
    s = SH.assess_item(item(creeping), leads=L)
    assert s.fill_rate is not None and s.fill_rate > SH.SHORT_FILL
    assert s.short_suppliers == []          # the average says everything is fine
    assert s.creep is not None and s.creep >= SH.CUSUM_ALARM
    assert s.verdict == "watch"
    assert "A trailing average hides this" in s.explanation


def test_a_sustained_trim_shows_up_as_a_short_fill_instead():
    """Where the decline has run long enough to drag the average down, the
    short-fill verdict is the right one and the creep detector is redundant."""
    s = SH.assess_item(item([line(received=r, day=i) for i, r in
                             enumerate([100, 92, 90, 88, 86, 85])]), leads=L)
    assert s.verdict == "market_shortage"


def test_a_healthy_sequence_raises_nothing():
    s = SH.assess_item(item(full(6)), leads=L)
    assert s.verdict == "no_signal"
    assert s.creep == Decimal("0.000")


def test_two_deliveries_are_not_a_trend():
    assert SH.downward_creep([Decimal("0.5"), Decimal("0.5")]) is None


def test_a_two_percent_gap_is_arithmetic_not_a_shortage():
    """Whole packs do not always split evenly. Flagging that would fire on every
    ordinary delivery, and a detector that fires always gets switched off."""
    s = SH.assess_item(item([line(received=98, day=i) for i in range(5)]),
                       leads=L)
    assert s.short_suppliers == []
    assert s.verdict == "no_signal"


# ── thin cover, which is not a shortage either ────────────────────────────
def test_arriving_fine_but_not_enough_of_it_is_a_reorder():
    s = SH.assess_item(item(full(4), on_hand=60, adq=10), leads=L)
    assert (s.verdict, s.action) == ("thin_cover", "reorder")
    assert s.suggested_buffer == Decimal("140.000")   # 10/day × 20 days − 60


def test_cover_shorter_than_the_lead_time_itself_is_worse():
    s = SH.assess_item(item(full(4), on_hand=30, adq=10), leads=L)
    assert s.severity == "high"


def test_plenty_of_cover_suggests_no_buffer():
    s = SH.assess_item(item(full(4), on_hand=1000, adq=1), leads=L)
    assert s.suggested_buffer is None


# ── what reaches a person ─────────────────────────────────────────────────
def test_an_unjudgeable_item_is_not_filed():
    assert SH.worth_raising(SH.assess_item(item(short(2)), leads=L)) is False


def test_a_healthy_item_is_not_filed():
    assert SH.worth_raising(SH.assess_item(item(full(6)), leads=L)) is False


def test_both_kinds_of_shortage_are_filed():
    assert SH.worth_raising(
        SH.assess_item(item(short(4) + short(4, "other")), leads=L)) is True
    assert SH.worth_raising(
        SH.assess_item(item(short(4) + full(4, "other")), leads=L)) is True


def test_the_report_counts_the_two_causes_separately():
    rep = SH.assess([
        item(short(4) + short(4, "other"), ndc="MARKET"),
        item(short(4) + full(4, "other"), ndc="ONESUP"),
        item(short(2), ndc="THIN"),
        item(full(6), ndc="FINE"),
    ], leads=L)
    assert (rep.market, rep.supplier_side, rep.unknown) == (1, 1, 1)
    assert rep.signals[0].verdict == "market_shortage"    # worst first
    assert "short from every supplier" in rep.explanation


# ── the vocabulary matches what the engine can actually produce ───────────
def test_every_action_the_engine_emits_is_a_declared_one():
    cases = [item(short(4) + short(4, "other")),
             item(short(4) + full(4, "other")),
             item(full(4), on_hand=60, adq=10),
             item(full(6)),
             item(short(2)),
             item([line(received=r, day=i) for i, r in
                   enumerate([100] * 20 + [85] * 4)])]
    seen = {SH.assess_item(c, leads=L).action for c in cases}
    assert seen <= set(SH.ACTIONS)
    assert "seek_alternative" not in SH.ACTIONS   # nothing could ever produce it


def test_a_caller_can_add_a_concern_the_engine_could_not_know():
    """The endpoint knows things the pure function cannot — such as that the
    stored demand signal was never refreshed and the cover here was computed from
    the fills instead."""
    s = SH.assess_item({**item(full(6)),
                        "extra_concerns": ["computed from the fill record"]},
                       leads=L)
    assert "computed from the fill record" in s.concerns
