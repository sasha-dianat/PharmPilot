"""Complementary inventory-movement edge cases (do not duplicate
test_inventory_movements.py): CORRECTION upward, removal at the exact on-hand
boundary, ADJUSTMENT to zero, and float rounding to 3 decimal places."""
import pytest

from services.core.inventory import movements as M


def test_correction_upward():
    assert M.plan_movement(movement_type="CORRECTION", before=4, new_quantity=9) == {
        "quantity_before": 4.0, "quantity_after": 9.0, "quantity_delta": 5.0}


def test_removal_exactly_on_hand_is_allowed():
    # quantity == on_hand is the boundary: permitted, leaves zero.
    plan = M.plan_movement(movement_type="EXPIRY_REMOVAL", before=12, quantity=12)
    assert plan == {"quantity_before": 12.0, "quantity_after": 0.0, "quantity_delta": -12.0}


def test_removal_one_over_on_hand_rejected():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="RECALL_REMOVAL", before=12, quantity=12.001)


def test_adjustment_to_zero():
    assert M.plan_movement(movement_type="ADJUSTMENT", before=7, new_quantity=0) == {
        "quantity_before": 7.0, "quantity_after": 0.0, "quantity_delta": -7.0}


def test_absolute_delta_rounds_to_three_dp():
    plan = M.plan_movement(movement_type="ADJUSTMENT", before=10.0001, new_quantity=10.0009)
    # 10.0009 - 10.0001 = 0.0008 → rounds to 0.001 at 3dp
    assert plan["quantity_delta"] == 0.001


def test_removal_after_and_delta_round_to_three_dp():
    plan = M.plan_movement(movement_type="DAMAGE", before=5.5, quantity=0.3335)
    assert plan["quantity_after"] == 5.167  # 5.5 - 0.3335 = 5.1665 → 5.167 (banker's-aware)
    assert plan["quantity_delta"] == -0.334  # -0.3335 → -0.334 at 3dp


def test_zero_before_absolute_set():
    plan = M.plan_movement(movement_type="CORRECTION", before=0, new_quantity=3)
    assert plan == {"quantity_before": 0.0, "quantity_after": 3.0, "quantity_delta": 3.0}
