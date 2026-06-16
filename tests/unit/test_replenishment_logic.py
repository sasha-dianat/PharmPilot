from datetime import date, timedelta

from services.core.inventory import replenishment as R


def _lot(lot_id, expiry, qty, ndc="00000000001", placed=0):
    return {"id": lot_id, "expiry_date": expiry, "quantity_on_hand": qty, "ndc11": ndc, "placed_units": placed}


def test_fefo_orders_earliest_expiry_first():
    today = date(2026, 6, 14)
    lots = [_lot("b", today + timedelta(days=90), 50), _lot("a", today + timedelta(days=10), 50)]
    ordered = R.fefo_order(lots)
    assert [l["id"] for l in ordered] == ["a", "b"]


def test_depot_qty_is_lot_minus_placements():
    assert R.depot_units(_lot("a", date(2026, 12, 1), 100, placed=30)) == 70


def test_barcode_gate_blocks_ndc_mismatch():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1)}
    scan = {"ndc11": "222", "lot_number": "L1", "expiry_date": date(2027, 1, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "block" and "ndc" in v["reason"].lower()


def test_barcode_gate_blocks_expired():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 1)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "block" and "expir" in v["reason"].lower()


def test_barcode_gate_warns_near_expiry():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 30)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2026, 6, 30), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials=set())
    assert v["verdict"] == "warn"


def test_barcode_gate_blocks_duplicate_serial():
    staged = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1)}
    scan = {"ndc11": "111", "lot_number": "L1", "expiry_date": date(2027, 1, 1), "serial": "S1"}
    v = R.barcode_gate(staged, scan, today=date(2026, 6, 14), seen_serials={"S1"})
    assert v["verdict"] == "block" and "serial" in v["reason"].lower()


def test_ai_count_verdict_thresholds():
    assert R.ai_count_verdict(0) == "pass"
    assert R.ai_count_verdict(1) == "warn"
    assert R.ai_count_verdict(-1) == "warn"
    assert R.ai_count_verdict(2) == "block"


def test_cold_chain_block_out_of_range():
    assert R.cold_chain_check("REFRIGERATED", logged_c=12.0)["verdict"] == "block"
    assert R.cold_chain_check("REFRIGERATED", logged_c=5.0)["verdict"] == "pass"
    assert R.cold_chain_check("REFRIGERATED", logged_c=None)["verdict"] == "block"
    assert R.cold_chain_check(None, logged_c=None)["verdict"] == "pass"  # room temp, not cold-chain


def test_capacity_warns_over_capacity():
    assert R.capacity_check(staged=40, current=70, capacity=100)["verdict"] == "warn"
    assert R.capacity_check(staged=10, current=10, capacity=100)["verdict"] == "pass"


def test_attestation_required_for_high_risk():
    assert R.requires_pharmacist_attestation({"high_risk_flag": True, "is_controlled": False, "lasa_group": None}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": True, "lasa_group": None}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": False, "lasa_group": "INSULIN"}) is True
    assert R.requires_pharmacist_attestation({"high_risk_flag": False, "is_controlled": False, "lasa_group": None}) is False


def test_reconcile_flags_discrepancy():
    assert R.reconcile(depot_out=10, shelf_in=10)["match"] is True
    bad = R.reconcile(depot_out=10, shelf_in=8)
    assert bad["match"] is False and bad["delta"] == -2


def test_split_pack_decrement():
    assert R.split_pack_remaining(blisters_per_box=10, blisters_taken=4) == 6


def test_build_shift_summary_groups_sections():
    events = [{"ndc11": "111", "quantity_delta": 10, "shelf_id": "s1", "override_reason": "FEFO skip: damaged"}]
    splits = [{"lot": "L1", "remaining": 6}]
    cold = [{"ndc11": "222", "temp": 12.0}]
    s = R.build_shift_summary(transfer_events=events, open_split_packs=splits, cold_chain_events=cold)
    assert s["transfers_completed"][0]["ndc11"] == "111"
    assert s["fefo_overrides"] and "FEFO skip" in s["fefo_overrides"][0]["reason"]
    assert s["open_split_packs"] == splits
    assert s["cold_chain_events"] == cold
