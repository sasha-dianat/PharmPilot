"""What an administrator may change directly, and what must go through the ledger.

The panel this backs is powerful on purpose: search, drill-down, single and bulk
edit. That power is only safe because the *quantity* is not part of it.

Three classes of field:

  DIRECT      — correcting a record that was typed wrong (location, cost, par
                levels, IRC binding). Audited, immediately applied.
  SENSITIVE   — a correction that could be used to hide something. Allowed, but
                it demands a reason and is recorded as such. Extending an expiry
                date is the canonical example: shortening it is conservative,
                lengthening it puts an out-of-date drug back on the shelf.
  LEDGER_ONLY — quantities. Never editable from an admin form at all. They move
                by receipt, dispense, count variance, or approved write-off, so
                that every unit that ever entered or left has a row explaining
                it. An "edit quantity" box would silently reopen every hole the
                ledger, the approval workflow and the hash chain exist to close.

That split is the whole design. Everything else here is detail.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# ── field policy ──────────────────────────────────────────────────────────
LOT_DIRECT_FIELDS = {
    "storage_location",      # moved to another shelf
    "unit_cost",             # invoice correction
    "irc",                   # formulary binding
    "serial_number",
    "lot_number",            # mis-keyed on receipt
}
LOT_SENSITIVE_FIELDS = {
    "expiry_date",           # only shortening is unconditional; see below
    "is_quarantined",
    "is_recalled",
    "cold_chain_breach",
}
LOT_LEDGER_ONLY_FIELDS = {
    "quantity_on_hand", "quantity_received", "quantity_reserved",
    "split_pack_remaining_blisters",
}
STOCK_DIRECT_FIELDS = {"par_level_min", "par_level_max", "irc"}

EDITABLE = LOT_DIRECT_FIELDS | LOT_SENSITIVE_FIELDS


class AdminEditError(ValueError):
    """An edit that is not permitted, or is permitted only with more evidence."""


@dataclass
class EditPlan:
    field: str
    old_value: object
    new_value: object
    sensitive: bool
    requires_approval: bool
    reason: str


def validate_lot_edit(field: str, old_value, new_value, *, reason: str,
                      is_controlled: bool = False) -> EditPlan:
    """Decide whether one field change is allowed, and on what terms."""
    if field in LOT_LEDGER_ONLY_FIELDS:
        raise AdminEditError(
            f"{field} cannot be edited directly — quantities move only through a "
            f"receipt, a dispense, a counted variance, or an approved write-off, "
            f"so that every unit has a movement explaining it")
    if field not in EDITABLE:
        raise AdminEditError(f"{field} is not an editable field")
    if not reason or not reason.strip():
        raise AdminEditError("every correction needs a reason")
    if old_value == new_value:
        raise AdminEditError(f"{field} is already {new_value!r}")

    sensitive = field in LOT_SENSITIVE_FIELDS
    requires_approval = False

    if field == "expiry_date":
        old_d, new_d = _as_date(old_value), _as_date(new_value)
        if new_d is None:
            raise AdminEditError("expiry_date cannot be cleared")
        # Asymmetric on purpose. Pulling an expiry earlier only ever removes
        # stock from use. Pushing it later returns expired stock to the shelf,
        # which is both a patient-safety event and the easiest way to make an
        # expiry write-off disappear.
        if old_d and new_d > old_d:
            requires_approval = True
    if field == "is_recalled" and new_value is False:
        # Un-recalling is releasing stock a manufacturer said not to use.
        requires_approval = True
    if field == "cold_chain_breach" and new_value is False:
        requires_approval = True
    if field == "is_quarantined" and new_value is False:
        requires_approval = True
    if is_controlled and sensitive:
        requires_approval = True

    return EditPlan(field=field, old_value=old_value, new_value=new_value,
                    sensitive=sensitive, requires_approval=requires_approval,
                    reason=reason.strip())


def _as_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        raise AdminEditError(f"not a date: {v!r}")


# ── bulk edits ────────────────────────────────────────────────────────────
# A bulk edit is a blast radius. These bounds exist so a mis-click cannot
# reprice or relocate the whole pharmacy in one request.
BULK_MAX_ROWS = 500
BULK_ALLOWED_FIELDS = {"storage_location", "par_level_min", "par_level_max",
                       "irc", "is_quarantined"}


def validate_bulk(field: str, value, target_ids: list, *, reason: str) -> None:
    if field not in BULK_ALLOWED_FIELDS:
        raise AdminEditError(
            f"{field} cannot be changed in bulk — it needs per-row judgement")
    if not target_ids:
        raise AdminEditError("no rows selected")
    if len(target_ids) > BULK_MAX_ROWS:
        raise AdminEditError(
            f"{len(target_ids)} rows exceeds the {BULK_MAX_ROWS}-row bulk limit; "
            f"narrow the filter or apply in batches")
    if len(set(map(str, target_ids))) != len(target_ids):
        raise AdminEditError("duplicate rows in selection")
    if not reason or not reason.strip():
        raise AdminEditError("every correction needs a reason")
    # Releasing many lots from quarantine at once is the one direction of this
    # field that must never be a bulk action.
    if field == "is_quarantined" and value is False:
        raise AdminEditError(
            "lots can be quarantined in bulk but must be released one at a time, "
            "each with its own justification")


# ── search / filter vocabulary ────────────────────────────────────────────
# Named views, so the panel offers the questions an administrator actually
# asks rather than a pile of raw column filters.
FILTERS = {
    "all":            "همه اقلام",
    "below_par":      "زیر حد سفارش",
    "out_of_stock":   "ناموجود",
    "expiring_soon":  "نزدیک انقضا (۹۰ روز)",
    "expired":        "منقضی‌شده",
    "quarantined":    "قرنطینه",
    "recalled":       "فراخوان‌شده",
    "controlled":     "تحت کنترل",
    "unbound":        "بدون اتصال به فهرست رسمی",
    "dead_stock":     "راکد (۱۸۰ روز بدون مصرف)",
    "cold_chain":     "زنجیره سرد شکسته",
    "overstocked":    "مازاد (بیش از ۳۶۵ روز موجودی)",
}

SORTS = {"name", "quantity", "expiry", "value", "days_supply", "last_dispensed"}


def normalize_query(q: str | None) -> dict:
    """Classify a search box entry so one field can serve every lookup an
    administrator does: name, IRC, NDC, barcode, or lot number."""
    s = (q or "").strip()
    if not s:
        return {"kind": "empty", "value": ""}
    digits = s.replace("-", "").replace(" ", "")
    if digits.isdigit():
        if len(digits) >= 14:
            return {"kind": "gtin", "value": digits}
        if len(digits) >= 11:
            return {"kind": "irc_or_ndc", "value": digits}
        return {"kind": "numeric", "value": digits}
    if any(ch.isdigit() for ch in s) and any(ch in "-/" for ch in s):
        return {"kind": "lot", "value": s.upper()}
    return {"kind": "name", "value": s}


RECEIPT_UOMS = ("each", "pack")


class ReceiptUnitError(ValueError):
    """A receipt whose quantity cannot be converted to units without guessing."""


def receipt_units(quantity, *, uom: str | None, units_per_pack=None) -> dict:
    """Convert what was counted at the bench into units on the shelf.

    A goods receipt recorded a bare number. "3" of a 30-count pack is 3 units or
    90 depending on what the person meant, and nothing recorded which — a 30x
    error that `check_unit_conversion` can only catch afterwards, once the shelf
    figure is already wrong and has already driven a reorder decision.

    `uom=None` is accepted and means "each", because every existing caller means
    that and silently reinterpreting their receipts as packs would rewrite the
    shelf. Declaring `pack` without a pack size is refused rather than assumed:
    that is exactly the guess this exists to prevent.
    """
    from decimal import Decimal
    from .ledger import q

    qty = q(quantity)
    if qty <= 0:
        raise ReceiptUnitError("received quantity must be positive")

    unit = (uom or "each").strip().lower()
    if unit not in RECEIPT_UOMS:
        raise ReceiptUnitError(
            f"unknown unit of measure {uom!r}; expected one of {RECEIPT_UOMS}")

    if unit == "each":
        return {"units": qty, "uom": "each", "packs": None,
                "units_per_pack": None if units_per_pack is None
                                  else q(units_per_pack),
                "explanation": f"{qty} unit(s) received."}

    if units_per_pack is None or q(units_per_pack) <= 0:
        raise ReceiptUnitError(
            "received in packs but the product has no pack size on file — "
            "record the units, or set the pack size first. Assuming one would "
            "misstate the shelf by the size of the pack.")

    per = q(units_per_pack)
    return {"units": q(qty * per), "uom": "pack", "packs": qty,
            "units_per_pack": per,
            "explanation": f"{qty} pack(s) x {per} = {q(qty * per)} units."}


def days_supply(on_hand, avg_daily_demand) -> float | None:
    try:
        d = float(avg_daily_demand or 0)
        return round(float(on_hand) / d, 1) if d > 0 else None
    except (TypeError, ValueError):
        return None
