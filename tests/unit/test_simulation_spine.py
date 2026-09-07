"""The spine that lets one run check several domains at once.

The existing simulator proved an architecture on one domain: a seeded world, an
oracle written from the specification rather than the code, invariants comparing
the two, escalating phases. A whole-pharmacy pilot needs that architecture with
the inventory assumptions taken out — because the failures worth finding are the
ones *between* domains, where stock says the units left and money says nothing
was owed.

Two properties carry the design and both are pinned here: one clock the whole run
reads, and one account of events that every domain hears identically.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

from tests.simulation.spine.clock import SimClock
from tests.simulation.spine.domain import Domain
from tests.simulation.spine.facts import Fact, KINDS, UnknownFact
from tests.simulation.spine.harness import Harness

START = date(2026, 1, 1)


class Recorder:
    """A domain that remembers what it was told, for testing the fan-out."""

    def __init__(self, name="recorder", *, fails=(), broken=()):
        self.name = name
        self.heard: list[Fact] = []
        self._fails = list(fails)
        self._broken = list(broken)

    def observe(self, fact):
        self.heard.append(fact)

    async def check(self, ctx):
        return list(self._fails)

    def violations(self):
        return list(self._broken)


class Exploding:
    name = "exploding"

    def observe(self, fact):
        pass

    async def check(self, ctx):
        raise RuntimeError("the database moved")

    def violations(self):
        return []


# ── the clock ─────────────────────────────────────────────────────────────
def test_a_run_can_span_months():
    c = SimClock(start=START)
    c.advance(400)
    assert c.today == date(2027, 2, 5)
    assert c.days_since_start() == 400


def test_two_events_on_one_day_do_not_share_an_instant():
    """Any ordering derived from `created_at` becomes arbitrary if they do — the
    bug that made one shelf reconciliation return different answers on two
    consecutive runs."""
    c = SimClock(start=START)
    assert c.now() < c.now() < c.now()


def test_stamps_restart_each_day_but_the_day_still_moves_forward():
    c = SimClock(start=START)
    first = c.now()
    c.advance()
    assert c.now() > first


def test_a_simulation_runs_forwards():
    with pytest.raises(ValueError):
        SimClock(start=START).advance(-1)


# ── the account of events ─────────────────────────────────────────────────
def test_a_fact_kind_nothing_emits_is_refused():
    """A vocabulary listing events no scenario produces is the same
    promise-with-no-code this project has removed twice."""
    with pytest.raises(UnknownFact):
        Fact(seq=1, at=SimClock(start=START).now(), day=0,
             kind="teleported", subject="N1")


def test_every_declared_kind_is_constructible():
    c = SimClock(start=START)
    for kind in KINDS:
        assert Fact(seq=1, at=c.now(), day=0, kind=kind, subject="N1").kind == kind


# ── the fan-out, which is the whole point ─────────────────────────────────
@pytest.mark.asyncio
async def test_one_action_reaches_every_domain_identically():
    """Four listeners each re-deriving "what just happened" from four different
    queries is how domains come to disagree about the same event."""
    a, b = Recorder("stock"), Recorder("money")
    h = Harness(db=None, start=START, domains=[a, b])
    h.fact("dispensed", subject="N1", quantity=Decimal("30"), actor="alice")
    assert len(a.heard) == len(b.heard) == 1
    assert a.heard[0] is b.heard[0]


@pytest.mark.asyncio
async def test_facts_are_stamped_with_the_simulated_day_not_the_real_one():
    h = Harness(db=None, start=START, domains=[Recorder()])
    h.advance(90)
    f = h.fact("dispensed", subject="N1")
    assert f.day == 90 and f.at.date() == START + timedelta(days=90)


@pytest.mark.asyncio
async def test_a_domain_may_register_after_the_run_starts():
    h = Harness(db=None, start=START)
    late = Recorder("late")
    h.register(late)
    h.fact("received", subject="N1")
    assert len(late.heard) == 1 and "late" in h.report.domains


# ── checking every domain in one pass ─────────────────────────────────────
@pytest.mark.asyncio
async def test_all_domains_are_checked_and_all_findings_reported():
    """A run that halts on the first finding has hidden the other three."""
    h = Harness(db=None, start=START,
                domains=[Recorder("stock", fails=["lot 1 disagrees"]),
                         Recorder("money", fails=["line 2 unpriced",
                                                  "vat wrong"])])
    found = await h.check("a scenario")
    assert len(found) == 3
    assert h.report.by_domain() == {"stock": 1, "money": 2}
    assert h.report.ok is False


@pytest.mark.asyncio
async def test_one_domain_failing_to_check_does_not_stop_the_others():
    h = Harness(db=None, start=START,
                domains=[Exploding(), Recorder("money", fails=["vat wrong"])])
    found = await h.check()
    assert {f.domain for f in found} == {"exploding", "money"}
    assert any("check raised RuntimeError" in f.detail for f in found)


@pytest.mark.asyncio
async def test_an_oracle_that_broke_its_own_rules_says_so_first():
    """If this fires the simulation is at fault, not the platform — and reporting
    it as a platform defect would be worse than useless."""
    h = Harness(db=None, start=START,
                domains=[Recorder("stock", broken=["negative on-hand in the model"])])
    found = await h.check()
    assert found[0].scenario == "oracle self-check"


@pytest.mark.asyncio
async def test_a_clean_run_says_what_it_actually_covered():
    h = Harness(db=None, start=START, domains=[Recorder("stock")])
    h.fact("received", subject="N1")
    h.advance(3)
    await h.check()
    assert h.report.ok
    s = h.report.summary()
    assert "1 facts over 3 simulated day(s)" in s
    assert "no invariant violated" in s


# ── the first citizen ─────────────────────────────────────────────────────
def test_the_existing_inventory_oracle_is_a_domain_without_being_rewritten():
    """It has found real defects across thousands of adversarial events.
    Rewriting it to fit a new interface would risk all of that to gain
    nothing."""
    from tests.simulation.domains.inventory import InventoryDomain
    from tests.simulation.oracle import Oracle

    d = InventoryDomain(Oracle(), app_state=None, app_totals=None,
                        app_valuation=None)
    assert isinstance(d, Domain)
    assert d.name == "inventory"


def test_inventory_does_not_double_count_facts_the_driver_already_recorded():
    """The driver writes to the oracle as it acts, because it alone knows which
    lot the application chose."""
    from tests.simulation.domains.inventory import InventoryDomain
    from tests.simulation.oracle import Oracle

    o = Oracle()
    d = InventoryDomain(o, app_state=None, app_totals=None, app_valuation=None)
    before = len(o.events) if hasattr(o, "events") else None
    d.observe(Fact(seq=1, at=SimClock(start=START).now(), day=0,
                   kind="dispensed", subject="N1", quantity=Decimal("5")))
    after = len(o.events) if hasattr(o, "events") else None
    assert before == after
