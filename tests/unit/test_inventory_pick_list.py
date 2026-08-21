"""E10 — a shelf stocked to the mean runs out half the time.

Half of all days is not a service level anybody would choose out loud, and it is
what a par level set to average demand delivers. The target here is the ninetieth
percentile — except for items whose demand arrives whole, where a percentile of a
daily total is meaningless and the shelf needs enough to serve one event.

The two hard refusals are physical rather than statistical: a refrigerated drug is
never proposed for a room-temperature shelf, and nothing quarantined, recalled or
expired is ever picked. Both are drops, not warnings, because a warning on a
picking list is read at speed by somebody holding a crate.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from services.core.inventory import pick_list as PL

TODAY = date(2026, 8, 18)
FAR = TODAY + timedelta(days=400)


def fills(pattern: dict[int, float]):
    return [{"fill_date": TODAY - timedelta(days=d), "quantity": Decimal(str(u))}
            for d, u in pattern.items()]


# Real demand, which varies. A perfectly even series has zero variance and
# correctly needs only the mean — that degenerate case is pinned separately.
STEADY = fills({i: 8 + (i % 5) for i in range(84)})   # ~10/day, varying
FLAT = fills({i: 10 for i in range(84)})              # exactly 10, every day
BURSTY = fills({i * 7: 70 for i in range(12)})        # 70 once a week


def lot(units=500, days=400, lot_number="L1", **kw):
    return {"id": f"lot-{lot_number}", "lot_number": lot_number,
            "expiry_date": TODAY + timedelta(days=days),
            "available": Decimal(str(units)), **kw}


def shelf(label="A-01", cond="ROOM_TEMP", cap=1000, cur=0):
    return {"id": "s1", "label": label, "storage_condition": cond,
            "capacity_units": cap, "current_units": cur}


def item(**kw):
    base = dict(ndc11="N1", drug_name="test drug", requires_refrigeration=False,
                on_shelf=Decimal("0"), shelf=shelf(), depot_lots=[lot()],
                fills=STEADY)
    base.update(kw)
    return base


def go(items, **kw):
    return PL.build(items, as_of=TODAY, **kw)


# ── the target, which is the whole design ─────────────────────────────────
def test_the_shelf_is_stocked_above_the_average_not_to_it():
    p = go([item()])
    assert len(p.lines) == 1
    assert p.lines[0].target > Decimal("10")     # more than a day's mean
    assert "runs out half the time" in p.explanation


def test_an_item_that_never_varies_needs_only_its_mean():
    """The degenerate case, and the target is right rather than padded: demand
    that is exactly ten every single day is served exactly by ten."""
    p = go([item(fills=FLAT)])
    assert p.lines[0].target == Decimal("10.000")


def test_a_more_variable_item_gets_a_bigger_target_at_the_same_average():
    even = go([item(fills=FLAT)]).lines[0]
    swingy = go([item(fills=fills({i: (2 if i % 2 else 18) for i in range(84)}))]).lines[0]
    assert float(swingy.target) > float(even.target)


def test_an_item_whose_demand_arrives_whole_is_stocked_for_one_event():
    """A percentile of a daily total is meaningless when most days are zero, and
    half an event on the shelf serves nobody."""
    p = go([item(fills=BURSTY)])
    line = p.lines[0]
    assert line.demand_class in PL.EVENT_SHAPED
    assert line.target == Decimal("70.000")
    assert "one event is" in line.explanation


def test_what_is_already_on_the_shelf_is_not_fetched_again():
    full = go([item(on_shelf=Decimal("1000"))])
    assert full.lines == []


def test_a_trivial_top_up_is_not_worth_the_walk():
    p = go([item(on_shelf=Decimal("22.9"), fills=fills({i: 10 for i in range(84)}))])
    assert all(l.pull >= PL.MIN_WORTH_PULLING for l in p.lines)


# ── FEFO, and what must never be picked ───────────────────────────────────
def test_the_oldest_stock_comes_forward_first():
    p = go([item(depot_lots=[lot(100, 300, "NEW"), lot(100, 30, "OLD")])])
    assert p.lines[0].lots[0]["lot_number"] == "OLD"


def test_quarantined_and_recalled_stock_is_never_picked():
    p = go([item(depot_lots=[lot(100, 300, "BAD", is_quarantined=True),
                             lot(100, 300, "ALSOBAD", is_recalled=True),
                             lot(100, 300, "GOOD")])])
    assert [l["lot_number"] for l in p.lines[0].lots] == ["GOOD"]


def test_expired_stock_is_never_picked():
    p = go([item(depot_lots=[{"id": "x", "lot_number": "GONE",
                              "expiry_date": TODAY - timedelta(days=1),
                              "available": Decimal("500")},
                             lot(100, 300, "GOOD")])])
    assert [l["lot_number"] for l in p.lines[0].lots] == ["GOOD"]


def test_a_refrigerated_drug_is_dropped_not_warned_about():
    """A warning on a picking list is read at speed by somebody holding a crate."""
    p = go([item(requires_refrigeration=True, shelf=shelf(cond="ROOM_TEMP"))])
    assert p.lines == []
    assert p.skipped[0]["reason"] == "storage_mismatch"
    assert "read at speed" in p.skipped[0]["explanation"]


def test_a_refrigerated_drug_on_a_refrigerated_shelf_is_fine():
    p = go([item(requires_refrigeration=True, shelf=shelf(cond="REFRIGERATED"))])
    assert len(p.lines) == 1


# ── physical limits ───────────────────────────────────────────────────────
def test_the_shelf_cannot_be_asked_to_hold_more_than_it_holds():
    p = go([item(fills=BURSTY, shelf=shelf(cap=100, cur=95))])
    assert p.lines[0].pull <= Decimal("5")


def test_a_full_shelf_is_reported_rather_than_overfilled():
    p = go([item(fills=BURSTY, shelf=shelf(cap=100, cur=100))])
    assert p.lines == []
    assert p.skipped[0]["reason"] == "shelf_full"


def test_an_empty_depot_is_named_as_a_purchasing_problem():
    p = go([item(depot_lots=[])])
    assert p.lines == []
    assert p.skipped[0]["reason"] == "depot_empty"
    assert "not a picking one" in p.skipped[0]["explanation"]


def test_a_partly_stocked_depot_brings_what_it_can_and_says_so():
    p = go([item(fills=BURSTY, depot_lots=[lot(20, 300, "THIN")])])
    line = p.lines[0]
    assert line.pull == Decimal("20.000")
    assert line.depot_short is True
    assert "has to be ordered" in line.explanation
    assert p.depot_shortfalls == 1
    assert any("purchasing gap" in c for c in p.concerns)


# ── refusing to invent a target ───────────────────────────────────────────
def test_an_item_nobody_dispenses_is_left_off_rather_than_given_a_figure():
    """Shelf space is the one thing in a pharmacy that cannot be ordered more
    of."""
    p = go([item(fills=[])])
    assert p.lines == []
    assert p.skipped[0]["reason"] == "no_measured_demand"
    assert "cannot be ordered more of" in p.skipped[0]["explanation"]
    assert any("invented target" in c for c in p.concerns)


def test_no_target_is_computed_without_a_rate():
    from services.core.inventory import intermittent as IM
    bare = IM.assess("N1", [], window_days=84, as_of=TODAY)
    assert PL.shelf_target(bare) is None


# ── the walk ──────────────────────────────────────────────────────────────
def test_the_round_is_ordered_by_shelf_so_it_is_one_pass():
    items = [item(ndc11="N3", shelf=shelf(label="C-01")),
             item(ndc11="N1", shelf=shelf(label="A-01")),
             item(ndc11="N2", shelf=shelf(label="B-01"))]
    p = go(items)
    assert [l.shelf_label for l in p.lines] == ["A-01", "B-01", "C-01"]


def test_the_total_is_what_the_person_actually_carries():
    p = go([item(ndc11="N1"), item(ndc11="N2", fills=BURSTY)])
    assert p.units == sum((l.pull for l in p.lines), Decimal("0"))


def test_a_longer_gap_between_rounds_needs_more_cover():
    one = go([item()], cover_days=1).lines[0]
    three = go([item()], cover_days=3).lines[0]
    assert three.target > one.target


def test_the_declared_skip_reasons_are_the_ones_the_code_emits():
    """This listed three values no path produced and omitted the four it did.
    A vocabulary the code cannot produce is a promise the UI may render a case
    for and the engine can never reach."""
    emitted = set()
    for it in [item(fills=[]),                                    # no demand
               item(requires_refrigeration=True),                 # mismatch
               item(fills=BURSTY, shelf=shelf(cap=100, cur=100)),  # full
               item(depot_lots=[])]:                              # empty depot
        emitted |= {s["reason"] for s in go([it]).skipped}
    assert emitted == set(PL.SKIP_REASONS)
