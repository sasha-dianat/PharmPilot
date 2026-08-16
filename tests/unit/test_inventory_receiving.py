"""Closing the loop from a delivery back to its order.

`ordered_at` was stamped on submit; `received_at` and
`purchase_order_lines.quantity_received` were read in five places and written in
none. So lead time and fill rate — the two facts every supplier engine stands on
— had never once been recorded, which is why service ⑰ has never produced a real
number.

These pin the arithmetic before the engines that consume it exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.core.inventory import receiving as RCV

NOW = datetime(2026, 8, 16, 10, 0, tzinfo=timezone.utc)


def line(lid="L1", ndc="N1", ordered=100, received=0, status="ordered",
         created=None):
    return {"id": lid, "ndc11": ndc, "quantity_ordered": Decimal(str(ordered)),
            "quantity_received": Decimal(str(received)), "status": status,
            "created_at": created or NOW}


# ── the three shapes a delivery takes ─────────────────────────────────────
def test_a_complete_delivery_closes_the_line():
    m = RCV.apply_receipt(line(ordered=100), 100)
    assert (m.shape, m.status) == ("exact", "complete")
    assert m.total_received == Decimal("100.000")


def test_a_short_delivery_leaves_the_line_open_and_names_the_gap():
    """The supplier's failure, and the one that costs a sale."""
    m = RCV.apply_receipt(line(ordered=100), 60)
    assert (m.shape, m.status) == ("short", "partial")
    assert "40.000 still outstanding" in m.explanation


def test_an_over_delivery_is_reported_rather_than_absorbed():
    """More than asked for is not a favour: it is unbudgeted stock that may not
    sell before it expires."""
    m = RCV.apply_receipt(line(ordered=100), 150)
    assert (m.shape, m.status) == ("over", "over")
    assert "50.000 more than asked for" in m.explanation


def test_a_rounding_difference_is_not_a_supplier_failure():
    """Scoring a one-unit difference on a large line as a short delivery would
    make every reliable supplier look unreliable."""
    m = RCV.apply_receipt(line(ordered=1000), 999)
    assert m.shape == "exact"


def test_a_second_delivery_completes_a_partly_filled_line():
    m = RCV.apply_receipt(line(ordered=100, received=60, status="partial"), 40)
    assert m.status == "complete"
    assert m.total_received == Decimal("100.000")


def test_a_nonpositive_receipt_is_refused():
    with pytest.raises(RCV.ReceivingError):
        RCV.apply_receipt(line(), 0)


def test_a_line_ordering_nothing_cannot_be_over_delivered():
    m = RCV.apply_receipt(line(ordered=0), 10)
    assert m.shape == "exact"


# ── which line a delivery belongs to ──────────────────────────────────────
def test_the_oldest_open_line_is_matched_first():
    """Matching the newest would leave the older one outstanding forever, which
    reads as a supplier failure that never happened."""
    older = line("OLD", created=NOW - timedelta(days=10))
    newer = line("NEW", created=NOW)
    got = RCV.match_line([newer, older], "N1")
    assert got is not None and got["id"] == "OLD"


def test_a_completed_line_is_not_matched_again():
    done = line("DONE", status="complete")
    open_ = line("OPEN", status="ordered", created=NOW + timedelta(days=1))
    got = RCV.match_line([done, open_], "N1")
    assert got is not None and got["id"] == "OPEN"


def test_a_delivery_for_something_never_ordered_matches_nothing():
    assert RCV.match_line([line(ndc="N1")], "N2") is None


def test_a_cancelled_line_is_not_matched():
    assert RCV.match_line([line(status="cancelled")], "N1") is None


# ── the order's own status follows its lines ──────────────────────────────
def test_an_order_is_complete_only_when_every_line_is():
    assert RCV.order_status([line(status="complete"),
                             line("L2", status="over")]) == "complete"


def test_an_order_with_one_line_outstanding_is_partial():
    assert RCV.order_status([line(status="complete"),
                             line("L2", received=10, status="partial")]) == "partial"


def test_an_untouched_order_is_still_submitted():
    assert RCV.order_status([line(), line("L2")]) == "submitted"


def test_an_order_whose_lines_are_all_cancelled_is_cancelled():
    assert RCV.order_status([line(status="cancelled")]) == "cancelled"


# ── the timestamp lead time is computed from ──────────────────────────────
def test_received_at_is_stamped_only_when_the_order_is_fully_in():
    """Stamping on the first delivery would make a part-filled order look faster
    than it was, and lead time is what safety stock is computed from."""
    partial = [line(status="complete"), line("L2", received=10, status="partial")]
    assert RCV.completion(partial, now=NOW) is None

    full = [line(status="complete"), line("L2", status="complete")]
    assert RCV.completion(full, now=NOW) == NOW


# ── fill rate, and refusing to quote one too early ────────────────────────
def closed(n, ordered=100, received=100):
    return [line(f"L{i}", ordered=ordered, received=received, status="complete")
            for i in range(n)]


def test_too_few_lines_refuses_to_quote_a_rate():
    """A percentage from three lines is an anecdote, and quoting one to a
    supplier is worse than saying nothing."""
    fr = RCV.fill_rate(closed(3))
    assert fr.rate is None
    assert fr.basis == "insufficient_history"
    assert "worse than saying nothing" in fr.explanation


def test_a_perfect_supplier_scores_one():
    fr = RCV.fill_rate(closed(6))
    assert fr.rate == Decimal("1.000")
    assert fr.basis == "observed"


def test_short_deliveries_pull_the_rate_down():
    rows = closed(5) + [line("SHORT", ordered=100, received=50, status="partial")]
    fr = RCV.fill_rate(rows)
    assert fr.rate is not None and fr.rate < Decimal("1")


def test_over_delivery_cannot_offset_a_shortfall_elsewhere():
    """A supplier sending 200 against an order of 100 has not achieved a 200%
    fill rate, and letting the excess mask a real shortfall would hide exactly
    what the metric exists to find."""
    rows = closed(4) + [
        line("OVER", ordered=100, received=200, status="over"),
        line("SHORT", ordered=100, received=0, status="partial")]
    fr = RCV.fill_rate(rows)
    assert fr.rate is not None
    # 5 lines at 100 + 1 at 0, out of 600 ordered — the over-delivery is capped.
    assert fr.rate == Decimal("0.833")


def test_open_lines_are_not_counted_as_failures():
    """An order placed yesterday has not failed to arrive."""
    fr = RCV.fill_rate(closed(6) + [line("PENDING", status="ordered")])
    assert fr.lines == 6
    assert fr.rate == Decimal("1.000")
