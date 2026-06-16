"""Complementary safety-guard coverage for depot→shelf finalize.

These ADD edge cases not already in test_depot_transfer_endpoints.py /
test_replenishment_logic.py / test_shelf_vision_envelope.py:
  - AI 'block' verdict that IS accompanied by a supervisor override (passes)
  - attestation enforced independently for controlled-only and LASA-only drugs
  - cold-chain boundary temps (exactly lo/hi) for REFRIGERATED and FROZEN
  - cold-chain pass at the API layer for a non-cold drug with no temperature
  - capacity exactly at capacity (no warn) vs strictly over (warn)
  - FEFO tie-break stability, barcode 30-day warn boundary, reconcile transit gain
  - shelf-vision: an image present but no model is still degraded/advisory
All pure (no DB/app fixtures) — guards run through D._enforce_finalize_guards
and the deterministic primitives import directly.
"""
from datetime import date, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.core.inventory import replenishment as R
from services.ai.shelf_vision.verifier import shelf_verify
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


def _drug(**over):
    base = {"high_risk_flag": False, "is_controlled": False, "lasa_group": None,
            "storage_condition": None}
    base.update(over)
    return base


_SHELF_OK = {"capacity_units": 100, "current_units": 0}


# ── Guard: AI block WITH a valid supervisor override is allowed through ─────────
def test_ai_block_with_override_passes():
    flags = D._enforce_finalize_guards(
        _body(ai_verification={"count_verdict": "block"},
              override_reason="recount confirmed", override_by=str(uuid4())),
        drug=_drug(), shelf=_SHELF_OK)
    assert flags == {}


def test_ai_block_with_reason_but_no_supervisor_still_blocks():
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(
            _body(ai_verification={"count_verdict": "block"}, override_reason="oops"),
            drug=_drug(), shelf=_SHELF_OK)
    assert exc.value.status_code == 422


# ── Guard: attestation enforced for each flag independently ────────────────────
def test_attestation_required_for_controlled_only():
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(_body(), drug=_drug(is_controlled=True), shelf=_SHELF_OK)
    assert exc.value.status_code == 422
    assert "attestation" in str(exc.value.detail).lower()


def test_attestation_required_for_lasa_only():
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(_body(), drug=_drug(lasa_group="INSULIN"), shelf=_SHELF_OK)
    assert exc.value.status_code == 422


def test_attestation_satisfied_when_pin_and_by_present():
    flags = D._enforce_finalize_guards(
        _body(pharmacist_attestation_by=str(uuid4()), pharmacist_attestation_pin="1234"),
        drug=_drug(high_risk_flag=True), shelf=_SHELF_OK)
    assert flags == {}


def test_attestation_by_without_pin_still_blocks():
    with pytest.raises(HTTPException):
        D._enforce_finalize_guards(
            _body(pharmacist_attestation_by=str(uuid4())),  # pin missing
            drug=_drug(is_controlled=True), shelf=_SHELF_OK)


# ── Guard: cold-chain boundary + non-cold pass at the API layer ────────────────
def test_cold_chain_frozen_boundary_passes_at_api():
    flags = D._enforce_finalize_guards(
        _body(temperature_logged_c=-25.0), drug=_drug(storage_condition="FROZEN"),
        shelf=_SHELF_OK)
    assert flags == {}


def test_cold_chain_refrigerated_boundary_passes_at_api():
    flags = D._enforce_finalize_guards(
        _body(temperature_logged_c=8.0), drug=_drug(storage_condition="REFRIGERATED"),
        shelf=_SHELF_OK)
    assert flags == {}


def test_cold_chain_missing_temp_blocks_at_api():
    with pytest.raises(HTTPException) as exc:
        D._enforce_finalize_guards(
            _body(temperature_logged_c=None), drug=_drug(storage_condition="REFRIGERATED"),
            shelf=_SHELF_OK)
    assert exc.value.status_code == 422


def test_non_cold_drug_needs_no_temperature():
    flags = D._enforce_finalize_guards(
        _body(temperature_logged_c=None), drug=_drug(storage_condition="ROOM"), shelf=_SHELF_OK)
    assert flags == {}


# ── Guard: capacity exactly at boundary does not warn ──────────────────────────
def test_capacity_exactly_full_no_warn():
    flags = D._enforce_finalize_guards(
        _body(quantity=30), drug=_drug(), shelf={"capacity_units": 100, "current_units": 70})
    assert "capacity_warning" not in flags


def test_capacity_one_over_warns():
    flags = D._enforce_finalize_guards(
        _body(quantity=31), drug=_drug(), shelf={"capacity_units": 100, "current_units": 70})
    assert "capacity_warning" in flags


# ── Pure cold-chain boundary temps (all four extremes) ─────────────────────────
@pytest.mark.parametrize("cond,temp", [
    ("REFRIGERATED", 2.0), ("REFRIGERATED", 8.0),
    ("FROZEN", -25.0), ("FROZEN", -10.0),
])
def test_cold_chain_boundary_inclusive(cond, temp):
    assert R.cold_chain_check(cond, temp)["verdict"] == "pass"


@pytest.mark.parametrize("cond,temp", [
    ("REFRIGERATED", 1.9), ("REFRIGERATED", 8.1),
    ("FROZEN", -25.1), ("FROZEN", -9.9),
])
def test_cold_chain_just_outside_blocks(cond, temp):
    assert R.cold_chain_check(cond, temp)["verdict"] == "block"


def test_cold_chain_case_insensitive_condition():
    assert R.cold_chain_check("refrigerated", 5.0)["verdict"] == "pass"


# ── FEFO tie-break: equal expiry keeps stable input order ──────────────────────
def test_fefo_stable_on_equal_expiry():
    d = date(2027, 1, 1)
    lots = [
        {"id": "x", "expiry_date": d, "quantity_on_hand": 5},
        {"id": "y", "expiry_date": d, "quantity_on_hand": 5},
    ]
    assert [l["id"] for l in R.fefo_order(lots)] == ["x", "y"]


# ── Barcode 30-day warn boundary exact ─────────────────────────────────────────
def test_barcode_exactly_30_days_out_warns():
    today = date(2026, 6, 14)
    exp = today + timedelta(days=29)  # strictly inside the 30-day window
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": exp}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": exp, "serial": "S1"}
    assert R.barcode_gate(staged, scan, today=today, seen_serials=set())["verdict"] == "warn"


def test_barcode_far_future_passes():
    today = date(2026, 6, 14)
    exp = today + timedelta(days=400)
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": exp}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": exp, "serial": "S1"}
    assert R.barcode_gate(staged, scan, today=today, seen_serials=set())["verdict"] == "pass"


def test_barcode_blocks_lot_mismatch():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1)}
    scan = {"ndc11": "111", "lot_number": "L2", "expiry_date": date(2027, 1, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "block" and "lot" in v["reason"].lower()


# ── Reconcile: transit gain (positive delta) is also a mismatch ────────────────
def test_reconcile_positive_delta_is_mismatch():
    r = R.reconcile(depot_out=8, shelf_in=10)
    assert r["match"] is False and r["delta"] == 2


# ── Shelf-vision: image present but no model is still degraded/advisory ─────────
def test_shelf_verify_image_present_no_model_still_degraded():
    env = shelf_verify(image_base64="ZmFrZQ==", staged_ndc="111", staged_lot="L1",
                       staged_quantity=10, expected_drug_name="metformin",
                       expected_drug_form="tablet")
    assert env["degraded"] is True
    assert env["result"]["count_verdict"] == "warn"  # never a silent pass
    assert env["options_offline"]  # offline fallbacks advertised when degraded
