"""The clock on an approval, and the unit of measure on a receipt.

Both defects are the same shape: something the system needed to know was never
recorded, so nothing could act on it. An approval had no deadline, so a stalled
write-off was indistinguishable from a fresh one. A receipt had no unit, so
"3" was indistinguishable from 90.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.core.inventory import admin_rules as A
from services.core.inventory import approvals_sla as S

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def appr(hours_ago: float, *, mtype="WASTE", controlled=False, status="pending",
         level=0, aid="a1") -> dict:
    created = NOW - timedelta(hours=hours_ago)
    return {"id": aid, "movement_type": mtype, "is_controlled": controlled,
            "status": status, "created_at": created, "escalation_level": level,
            "due_at": S.deadline(mtype, requested_at=created,
                                 is_controlled=controlled).due_at}


# ── deadlines follow the value at risk ────────────────────────────────────
def test_a_controlled_write_off_is_a_same_day_matter():
    hours, why = S.sla_hours("WASTE", is_controlled=True)
    assert hours == 8
    assert "diversion signal" in why


def test_a_recall_removal_is_urgent_because_the_stock_is_still_on_the_shelf():
    assert S.sla_hours("RECALL_REMOVAL")[0] == 8


def test_a_supplier_credit_can_wait_because_it_is_money_not_safety():
    hours, why = S.sla_hours("SUPPLIER_CREDIT")
    assert hours == 72
    assert "money, not safety" in why


def test_an_unknown_movement_type_still_gets_a_deadline():
    """A type nobody anticipated must not be exempt from the clock."""
    assert S.sla_hours("SOMETHING_NEW")[0] == S.DEFAULT_SLA_HOURS


def test_controlled_outranks_the_movement_type():
    assert S.sla_hours("SUPPLIER_CREDIT", is_controlled=True)[0] == 8


def test_a_naive_request_timestamp_is_read_as_utc():
    d = S.deadline("WASTE", requested_at=datetime(2026, 8, 9, 12))
    assert d.due_at == NOW + timedelta(hours=48)


# ── overdue ───────────────────────────────────────────────────────────────
def test_an_approval_inside_its_window_is_not_overdue():
    assert S.overdue([appr(4)], now=NOW) == []


def test_an_approval_past_its_window_is_reported_with_what_it_holds_up():
    out = S.overdue([appr(50)], now=NOW)
    assert len(out) == 1
    assert out[0].hours_late == pytest.approx(2.0, abs=0.01)
    assert "still on the books" in out[0].explanation


def test_a_decided_approval_is_never_overdue():
    assert S.overdue([appr(500, status="approved")], now=NOW) == []


def test_an_approval_raised_before_the_clock_existed_is_still_measured():
    """Rows with no due_at must not be silently exempt from the deadline."""
    row = appr(100)
    row["due_at"] = None
    assert len(S.overdue([row], now=NOW)) == 1


def test_escalation_is_measured_against_the_items_own_window():
    """8 hours late on a controlled substance is a full window overdue; 8 hours
    late on a supplier credit is barely anything."""
    ctrl = S.overdue([appr(16, mtype="WASTE", controlled=True)], now=NOW)[0]
    credit = S.overdue([appr(80, mtype="SUPPLIER_CREDIT")], now=NOW)[0]
    assert ctrl.level >= 1
    assert credit.level == 0


def test_escalation_climbs_and_names_who_is_told():
    late = S.overdue([appr(48 + 48 * 4)], now=NOW)[0]
    assert late.level == S.MAX_ESCALATION
    assert "owner" in late.audience


def test_escalation_never_exceeds_the_top_level():
    assert S.overdue([appr(48 + 48 * 99)], now=NOW)[0].level == S.MAX_ESCALATION


def test_the_worst_cases_sort_first():
    rows = [appr(50, aid="mild"), appr(48 + 48 * 4, aid="severe")]
    assert [o.approval_id for o in S.overdue(rows, now=NOW)] == ["severe", "mild"]


def test_being_slightly_late_tells_nobody_new():
    """Level 0 is the assigned approver, and it is already their queue. Paging
    the pharmacist in charge two hours into a 48-hour window is how an alert
    becomes noise."""
    row = appr(50)
    assert S.overdue([row], now=NOW)[0].level == 0
    assert S.needs_escalating([row], now=NOW) == []


def test_escalating_stops_repeating_what_was_already_announced():
    """A nightly sweep that re-announces the same twelve approvals every night
    is the same failure as never alerting, arrived at differently."""
    row = appr(48 + 50)                       # a full window past due
    assert len(S.needs_escalating([row], now=NOW)) == 1
    row["escalation_level"] = S.overdue([row], now=NOW)[0].level
    assert S.needs_escalating([row], now=NOW) == []


def test_a_further_slip_escalates_again():
    row = appr(50, level=1)
    row["created_at"] = NOW - timedelta(hours=48 + 48 * 4)
    row["due_at"] = S.deadline("WASTE", requested_at=row["created_at"]).due_at
    assert len(S.needs_escalating([row], now=NOW)) == 1


def test_expiring_an_approval_never_approves_it():
    """Auto-approving on a timer would defeat the entire point of requiring a
    second person."""
    assert not hasattr(S, "auto_approve")
    out = S.overdue([appr(999)], now=NOW)
    assert out[0].level == S.MAX_ESCALATION      # escalated, still pending


# ── unit of measure at receipt ────────────────────────────────────────────
def test_a_bare_quantity_is_read_as_units_so_existing_callers_are_unchanged():
    """Reinterpreting existing receipts as packs would rewrite the shelf by a
    factor of the pack size."""
    out = A.receipt_units(30, uom=None, units_per_pack=30)
    assert out["units"] == Decimal("30.000")
    assert out["uom"] == "each"


def test_packs_are_multiplied_by_the_pack_size():
    out = A.receipt_units(3, uom="pack", units_per_pack=30)
    assert out["units"] == Decimal("90.000")
    assert out["packs"] == Decimal("3.000")
    assert out["explanation"] == "3.000 pack(s) x 30.000 = 90.000 units."


def test_packs_without_a_pack_size_are_refused_rather_than_assumed():
    """Assuming 1 would misstate the shelf by the size of the pack — the exact
    error this exists to prevent."""
    with pytest.raises(A.ReceiptUnitError) as e:
        A.receipt_units(3, uom="pack", units_per_pack=None)
    assert "no pack size on file" in str(e.value)


def test_a_zero_pack_size_is_treated_as_no_pack_size():
    with pytest.raises(A.ReceiptUnitError):
        A.receipt_units(3, uom="pack", units_per_pack=0)


def test_an_unknown_unit_is_refused():
    with pytest.raises(A.ReceiptUnitError):
        A.receipt_units(3, uom="crates", units_per_pack=30)


def test_a_nonpositive_receipt_is_refused():
    with pytest.raises(A.ReceiptUnitError):
        A.receipt_units(0, uom="each")


def test_the_30x_error_the_conversion_check_could_only_find_afterwards():
    """check_unit_conversion flags on-hand wildly out of scale with pack size —
    but only once the shelf figure is already wrong and has already driven a
    reorder. Declaring the unit stops it being entered."""
    as_packs = A.receipt_units(100, uom="pack", units_per_pack=30)
    as_each = A.receipt_units(100, uom="each", units_per_pack=30)
    assert as_packs["units"] == Decimal("3000.000")
    assert as_each["units"] == Decimal("100.000")


# ── the check that surfaces a stalled queue ───────────────────────────────
from services.core.inventory import reconciliation as R  # noqa: E402


def test_no_overdue_approvals_is_silent():
    f = R.check_overdue_approvals([])
    assert (f.count, f.severity) == (0, "info")


def test_an_overdue_approval_is_reported_and_refuses_to_age_into_approval():
    f = R.check_overdue_approvals(S.overdue([appr(50)], now=NOW))
    assert (f.count, f.severity) == (1, "medium")
    assert "cannot be aged out into an approval" in f.remediation


def test_an_overdue_controlled_approval_is_high_severity():
    f = R.check_overdue_approvals(
        S.overdue([appr(20, mtype="WASTE", controlled=True)], now=NOW))
    assert f.severity == "high"
    assert "on controlled stock" in f.detail


def test_an_approval_escalated_to_the_owner_is_high_severity():
    f = R.check_overdue_approvals(S.overdue([appr(48 + 48 * 2)], now=NOW))
    assert f.severity == "high"
    assert "escalated to the owner" in f.detail


def test_the_overdue_check_is_in_the_registry():
    assert "approval_overdue" in R.ALL_CHECKS
