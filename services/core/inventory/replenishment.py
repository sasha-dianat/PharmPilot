"""Deterministic depot→shelf replenishment logic. Pure functions; no I/O.

Single auditable home for the regulated safety rules: FEFO ordering, the barcode
identity gate, AI-count verdict thresholds, cold-chain/capacity checks, pharmacist
attestation triggers, depot↔shelf reconciliation, split-pack math, and the shift
handover summary. Kept I/O-free so every rule is unit-testable in isolation.
"""
from __future__ import annotations

from datetime import date, timedelta

NEAR_EXPIRY_WARN_DAYS = 30
COLD_RANGES = {"REFRIGERATED": (2.0, 8.0), "FROZEN": (-25.0, -10.0)}


def fefo_order(lots: list[dict]) -> list[dict]:
    """Earliest expiry first (First-Expired-First-Out)."""
    return sorted(lots, key=lambda l: l["expiry_date"])


def depot_units(lot: dict) -> int:
    """Back-stock units = lot total on hand − units already placed on shelves."""
    return int(lot["quantity_on_hand"]) - int(lot.get("placed_units", 0))


def barcode_gate(staged: dict, scan: dict, *, today: date, seen_serials: set[str]) -> dict:
    """Identity gate at scan time. Returns {verdict: pass|warn|block, reason}."""
    def block(reason: str) -> dict:
        return {"verdict": "block", "reason": reason}

    if scan.get("ndc11") != staged.get("ndc11"):
        return block("NDC mismatch")
    if scan.get("lot_number") != staged.get("lot_number"):
        return block("Lot mismatch")
    if scan.get("expiry_date") != staged.get("expiry_date"):
        return block("Expiry mismatch")
    serial = scan.get("serial")
    if serial and serial in seen_serials:
        return block("Serial duplicate (already scanned/dispensed)")
    exp = scan.get("expiry_date")
    if exp is not None and exp < today:
        return block("Expired — never allow to shelf")
    if exp is not None and exp < today + timedelta(days=NEAR_EXPIRY_WARN_DAYS):
        return {"verdict": "warn", "reason": "Expires within 30 days — supervisor PIN required"}
    return {"verdict": "pass", "reason": ""}


def ai_count_verdict(delta: int) -> str:
    """0 → pass, ±1 → warn (manual recount), ±2+ → block (override)."""
    a = abs(int(delta))
    if a == 0:
        return "pass"
    if a == 1:
        return "warn"
    return "block"


def cold_chain_check(storage_condition: str | None, logged_c: float | None) -> dict:
    """Block cold-chain items with missing or out-of-range temperature."""
    rng = COLD_RANGES.get((storage_condition or "").upper())
    if rng is None:
        return {"verdict": "pass", "reason": ""}  # not a cold-chain item
    if logged_c is None:
        return {"verdict": "block", "reason": "Temperature required for cold-chain item"}
    lo, hi = rng
    if lo <= float(logged_c) <= hi:
        return {"verdict": "pass", "reason": ""}
    return {"verdict": "block", "reason": f"Temp {logged_c}C outside {lo}-{hi}C — cold chain breach"}


def capacity_check(*, staged: int, current: int, capacity: int) -> dict:
    """Warn (non-blocking) when placement would exceed shelf capacity."""
    if capacity and staged + current > capacity:
        pct = round((staged + current) / capacity * 100)
        return {"verdict": "warn", "reason": f"Shelf will be at {pct}% capacity"}
    return {"verdict": "pass", "reason": ""}


def requires_pharmacist_attestation(drug: dict) -> bool:
    """High-risk, controlled, or LASA drugs require pharmacist sign-off at shelf placement."""
    return bool(drug.get("high_risk_flag") or drug.get("is_controlled") or drug.get("lasa_group"))


def reconcile(*, depot_out: int, shelf_in: int) -> dict:
    """Compare depot-collected count vs shelf-placed count to catch transit loss."""
    delta = int(shelf_in) - int(depot_out)
    return {"match": delta == 0, "delta": delta, "depot_out": depot_out, "shelf_in": shelf_in}


def split_pack_remaining(*, blisters_per_box: int, blisters_taken: int) -> int:
    """Blisters left in a partially-opened box after a partial transfer."""
    return int(blisters_per_box) - int(blisters_taken)


def build_shift_summary(*, transfer_events: list[dict], open_split_packs: list[dict],
                        cold_chain_events: list[dict]) -> dict:
    """Aggregate a shift's transfers/overrides/anomalies for the handover report."""
    return {
        "transfers_completed": [
            {"ndc11": e["ndc11"], "quantity_delta": e["quantity_delta"], "shelf_id": e.get("shelf_id")}
            for e in transfer_events
        ],
        "fefo_overrides": [
            {"ndc11": e["ndc11"], "reason": e["override_reason"]}
            for e in transfer_events if e.get("override_reason")
        ],
        "anomaly_signals": [],
        "open_split_packs": open_split_packs,
        "cold_chain_events": cold_chain_events,
    }
