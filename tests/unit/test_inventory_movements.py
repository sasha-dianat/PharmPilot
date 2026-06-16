import pytest

from services.core.inventory import movements as M


def test_absolute_adjustment_up_and_down():
    assert M.plan_movement(movement_type="ADJUSTMENT", before=10, new_quantity=15) == {
        "quantity_before": 10.0, "quantity_after": 15.0, "quantity_delta": 5.0}
    assert M.plan_movement(movement_type="CORRECTION", before=10, new_quantity=4) == {
        "quantity_before": 10.0, "quantity_after": 4.0, "quantity_delta": -6.0}


def test_removal_types_decrement():
    for t in ("RETURN", "DAMAGE", "EXPIRY_REMOVAL", "RECALL_REMOVAL"):
        plan = M.plan_movement(movement_type=t, before=20, quantity=8)
        assert plan == {"quantity_before": 20.0, "quantity_after": 12.0, "quantity_delta": -8.0}


def test_cannot_remove_more_than_on_hand():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="RETURN", before=5, quantity=6)


def test_removal_must_be_positive():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="DAMAGE", before=5, quantity=0)
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="DAMAGE", before=5, quantity=-2)


def test_absolute_rejects_negative_target():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="ADJUSTMENT", before=5, new_quantity=-1)


def test_unknown_type_rejected():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="TELEPORT", before=5, quantity=1)


def test_missing_required_quantity_field():
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="ADJUSTMENT", before=5)        # needs new_quantity
    with pytest.raises(M.MovementError):
        M.plan_movement(movement_type="RETURN", before=5)            # needs quantity
