"""⑳ — the brief, and the numbers it refuses to make up.

The failure mode here is not a wrong figure on a screen. It is the owner
repeating a fabricated "market rate" to a distributor who knows the real one, and
losing the conversation in the first minute. So the tests that matter most are
the ones where the engine declines: one supplier means no comparison, a
molecule with no sale price means no money figure, and a payment window nobody
has recorded a cost of capital for stays unpriced.
"""
from __future__ import annotations

from decimal import Decimal

from services.core.inventory import negotiation as NG


def line(supplier, ndc="N1", ordered=100, received=100, cost="10",
         status="complete"):
    return {"wholesaler": supplier, "ndc11": ndc,
            "quantity_ordered": Decimal(str(ordered)),
            "quantity_received": Decimal(str(received)),
            "unit_cost": None if cost is None else Decimal(str(cost)),
            "status": status}


def many(supplier, n=6, **kw):
    return [line(supplier, **kw) for _ in range(n)]


# ── leverage: which conversation is the owner in ──────────────────────────
def test_a_supplier_holding_most_of_the_spend_is_named_as_principal():
    """A supplier taking 40% of spend hears a different argument from one taking
    4%, and the owner should know which before they open their mouth."""
    lines = many("big", 8, cost="10") + many("small", 2, cost="10")
    lev = NG.leverage(lines, supplier="big")
    assert lev.standing == "principal"
    assert lev.share == Decimal("0.800")
    assert "visible in their own numbers" in lev.explanation


def test_a_small_supplier_is_told_the_leverage_is_the_volume_that_could_move():
    lines = many("big", 18) + many("small", 2)
    lev = NG.leverage(lines, supplier="small")
    assert lev.standing == "marginal"
    assert "volume that could move to them" in lev.explanation


def test_the_only_supplier_is_told_there_is_no_alternative_to_point_at():
    lev = NG.leverage(many("only", 8), supplier="only")
    assert lev.standing == "sole"
    assert "invites the obvious reply" in lev.explanation


def test_unpriced_lines_make_the_spend_a_floor_not_a_total():
    """Quoting the sum of the priced lines as the total opens the conversation
    with a number lower than the truth."""
    lines = many("acme", 4, cost="10") + many("acme", 2, cost=None)
    lev = NG.leverage(lines, supplier="acme")
    assert lev.unpriced_lines == 2
    assert "a floor rather than a total" in lev.explanation


def test_lines_still_in_flight_are_not_spend():
    lines = many("acme", 4) + [line("acme", status="partial", received=0)]
    assert NG.leverage(lines, supplier="acme").lines == 4


# ── the negotiable gap, and only from prices actually paid ────────────────
def test_a_molecule_cheaper_elsewhere_is_the_gap():
    lines = many("dear", 6, ndc="N1", cost="12") + many("cheap", 6, ndc="N1", cost="10")
    gaps = NG.term_gaps(lines, supplier="dear")
    assert len(gaps) == 1
    g = gaps[0]
    assert (g.best_supplier, g.gap_per_unit) == ("cheap", Decimal("2.000"))
    # 1,200 units bought across both suppliers × 2 per unit.
    assert g.annual_value == Decimal("2400.000")


def test_a_molecule_only_one_supplier_carries_has_no_gap():
    """There is no benchmark for it, and inventing one is the failure this whole
    module is arranged around."""
    lines = many("dear", 6, ndc="SOLO", cost="12")
    assert NG.term_gaps(lines, supplier="dear") == []


def test_being_the_cheapest_is_not_a_gap():
    lines = many("cheap", 6, cost="9") + many("dear", 6, cost="12")
    assert NG.term_gaps(lines, supplier="cheap") == []


def test_invoice_noise_is_not_worth_asking_about():
    """A 1% difference is freight or a rounding difference, and asking a
    distributor to match it spends credibility on nothing."""
    lines = many("a", 6, cost="10.10") + many("b", 6, cost="10.00")
    assert NG.term_gaps(lines, supplier="a") == []


def test_the_realised_price_is_weighted_by_units_not_by_invoice():
    """One small line at an odd price should not weigh the same as the standing
    order."""
    lines = ([line("a", received=1000, cost="10")]
             + [line("a", received=1, cost="99")])
    costs = NG.realised_costs(lines)
    assert costs["N1"]["a"] < Decimal("10.1")


def test_gaps_are_ordered_by_what_they_are_worth():
    lines = (many("dear", 4, ndc="SMALL", cost="12", received=10)
             + many("cheap", 4, ndc="SMALL", cost="10", received=10)
             + many("dear", 4, ndc="BIG", cost="12", received=1000)
             + many("cheap", 4, ndc="BIG", cost="10", received=1000))
    gaps = NG.term_gaps(lines, supplier="dear")
    assert [g.ndc11 for g in gaps] == ["BIG", "SMALL"]


# ── what unreliability costs, and when it cannot be priced ────────────────
def test_undelivered_units_become_forgone_margin():
    """The strongest card in the conversation, and nobody computed it before."""
    lines = many("acme", 5, ordered=100, received=60)
    rel = NG.reliability_cost(lines, supplier="acme",
                              margins={"N1": Decimal("3")})
    assert rel.undelivered_units == Decimal("200.000")
    assert rel.forgone_margin == Decimal("600.000")
    assert rel.basis == "observed"


def test_without_a_sale_price_the_units_are_reported_and_the_money_is_not():
    """A guessed margin quoted to someone who sells these for a living is the
    same mistake as a guessed benchmark."""
    rel = NG.reliability_cost(many("acme", 5, ordered=100, received=60),
                              supplier="acme", margins={})
    assert rel.undelivered_units == Decimal("200.000")
    assert rel.forgone_margin is None
    assert rel.basis == "unpriced"
    assert "not worth saying out loud" in rel.explanation


def test_a_partly_priced_loss_is_labelled_a_floor():
    lines = (many("acme", 4, ndc="PRICED", ordered=100, received=50)
             + many("acme", 4, ndc="BARE", ordered=100, received=50))
    rel = NG.reliability_cost(lines, supplier="acme",
                              margins={"PRICED": Decimal("2")})
    assert rel.basis == "partly_priced"
    assert "this is a floor" in rel.explanation


def test_a_supplier_that_delivered_everything_gives_no_service_argument():
    rel = NG.reliability_cost(many("acme", 5), supplier="acme",
                              margins={"N1": Decimal("3")})
    assert rel.forgone_margin == Decimal("0.000")
    assert "no service-level argument" in rel.explanation


def test_another_suppliers_failures_are_not_charged_to_this_one():
    lines = many("acme", 5) + many("other", 5, ordered=100, received=0)
    rel = NG.reliability_cost(lines, supplier="acme",
                              margins={"N1": Decimal("3")})
    assert rel.undelivered_units == Decimal("0.000")


# ── the brief as a whole ──────────────────────────────────────────────────
def two_suppliers():
    return (many("dear", 6, ndc="N1", cost="12", ordered=100, received=80)
            + many("cheap", 6, ndc="N1", cost="10"))


def test_the_brief_leads_with_the_strongest_ask():
    b = NG.brief(two_suppliers(), supplier="dear",
                 margins={"N1": Decimal("4")}, months_of_history=12)
    assert b.asks[0].ask.startswith("match cheap's price")
    assert b.asks[0].worth == b.negotiable_annual
    assert any("service-level" in a.ask for a in b.asks)


def test_every_ask_carries_a_number_or_says_it_cannot():
    b = NG.brief(two_suppliers(), supplier="dear", margins={},
                 months_of_history=12)
    for a in b.asks:
        assert a.worth is not None or a.basis == "unpriced"


def test_one_supplier_means_the_brief_says_terms_cannot_be_compared():
    b = NG.brief(many("only", 8), supplier="only", months_of_history=12)
    assert b.gaps == []
    assert any("terms cannot be compared" in c for c in b.cannot_say)
    assert any("none is invented" in c for c in b.cannot_say)


def test_the_payment_window_is_never_priced():
    """Often the cheapest thing to trade, and this system records neither payment
    terms nor a cost of capital."""
    b = NG.brief(two_suppliers(), supplier="dear", months_of_history=12)
    window = [c for c in b.concessions if "payment window" in c.ask][0]
    assert window.worth is None and window.basis == "unpriced"
    assert any("payment terms" in c for c in b.cannot_say)


def test_a_thin_history_is_declared_rather_than_extrapolated_silently():
    b = NG.brief(many("dear", 2) + many("cheap", 6), supplier="dear",
                 months_of_history=2)
    assert b.basis == "volume_only"
    assert any("one invoice with a rounding error" in c for c in b.cannot_say)
    assert any("month(s) of purchase history" in c for c in b.cannot_say)


def test_the_cloud_tier_declares_itself_inert():
    """A cross-pharmacy benchmark needs more than one pharmacy on the platform."""
    b = NG.brief(two_suppliers(), supplier="dear", months_of_history=12)
    assert b.cloud["available"] is False
    assert any("cross-pharmacy benchmark" in c for c in b.cannot_say)


def test_the_safety_stock_concession_is_priced_from_real_carried_stock():
    b = NG.brief(two_suppliers(), supplier="dear",
                 safety_stock={"N1": Decimal("120")}, months_of_history=12)
    fewer = [c for c in b.concessions if "fewer" in c.ask][0]
    assert fewer.worth == Decimal("120.000")
    assert "pair it with the service-level ask" in fewer.explanation
