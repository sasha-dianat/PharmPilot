import pytest
from fastapi import HTTPException

from services.platform.routers import depot_transfer as D

_SID = "00000000-0000-0000-0000-000000000001"
_SHELF = "00000000-0000-0000-0000-000000000002"
_LOT = "00000000-0000-0000-0000-000000000003"


def _body(**over):
    base = dict(
        session_id=_SID, shelf_id=_SHELF, inventory_lot_id=_LOT, ndc11="111", quantity=10,
        barcode_scans=[], ai_verification={"count_verdict": "pass"},
        temperature_logged_c=None, pharmacist_attestation_by=None, pharmacist_attestation_pin=None,
    )
    base.update(over)
    return D.ShelfPlaceRequest(**base)


def test_shelf_place_rejects_high_risk_without_attestation():
    drug = {"high_risk_flag": True, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(_body(), drug=drug, shelf={"capacity_units": 100, "current_units": 0})
    assert exc.value.status_code == 422
    assert "attestation" in str(exc.value.detail).lower()


def test_finalize_guards_block_cold_chain_out_of_range():
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": "REFRIGERATED"}
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(_body(temperature_logged_c=12.0), drug=drug,
                                   shelf={"capacity_units": 100, "current_units": 0})
    assert exc.value.status_code == 422
    assert "cold chain" in str(exc.value.detail).lower()


def test_finalize_guards_block_ai_block_without_override():
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(_body(ai_verification={"count_verdict": "block"}), drug=drug,
                                   shelf={"capacity_units": 100, "current_units": 0})
    assert exc.value.status_code == 422


def test_finalize_guards_capacity_warn_is_nonblocking():
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    flags = D._enforce_finalize_guards(_body(quantity=40), drug=drug,
                                       shelf={"capacity_units": 100, "current_units": 70})
    assert "capacity_warning" in flags


def test_finalize_guards_pass_when_clean():
    drug = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None, "storage_condition": None}
    flags = D._enforce_finalize_guards(_body(), drug=drug, shelf={"capacity_units": 100, "current_units": 0})
    assert flags == {}
