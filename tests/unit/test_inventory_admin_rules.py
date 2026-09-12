"""Admin edit policy — what the panel may change, and on what terms.

The panel is the most powerful surface in the product. These tests pin the two
properties that keep it safe: quantities are unreachable from it, and the edits
that could hide something demand approval.
"""
from __future__ import annotations

from datetime import date

import pytest

from services.core.inventory import admin_rules as A


def _edit(field, old, new, **kw):
    kw.setdefault("reason", "typo on receipt")
    return A.validate_lot_edit(field, old, new, **kw)


# ── the boundary that matters ─────────────────────────────────────────────
@pytest.mark.parametrize("field", sorted(A.LOT_LEDGER_ONLY_FIELDS))
def test_quantities_can_never_be_edited_from_the_admin_panel(field):
    """An 'edit quantity' box would reopen every hole the ledger, the approval
    workflow and the hash chain exist to close."""
    with pytest.raises(A.AdminEditError, match="only through"):
        _edit(field, 10, 999)


def test_an_unknown_field_is_not_editable_by_default():
    with pytest.raises(A.AdminEditError, match="not an editable field"):
        _edit("pharmacy_id", "a", "b")


def test_every_correction_needs_a_reason():
    with pytest.raises(A.AdminEditError, match="reason"):
        _edit("storage_location", "A1", "B2", reason="   ")


def test_a_no_op_edit_is_rejected():
    """Otherwise the audit trail fills with rows that changed nothing."""
    with pytest.raises(A.AdminEditError, match="already"):
        _edit("storage_location", "A1", "A1")


# ── direct edits ──────────────────────────────────────────────────────────
def test_relocating_a_lot_is_a_plain_audited_edit():
    p = _edit("storage_location", "SHELF-A1", "SHELF-B2")
    assert (p.sensitive, p.requires_approval) == (False, False)
    assert p.reason == "typo on receipt"


def test_binding_to_the_formulary_is_a_plain_edit():
    assert _edit("irc", None, "1234567890").requires_approval is False


def test_correcting_unit_cost_is_a_plain_edit():
    assert _edit("unit_cost", 100, 120).sensitive is False


# ── expiry: asymmetric on purpose ─────────────────────────────────────────
def test_shortening_an_expiry_needs_no_approval():
    """Pulling an expiry earlier only ever removes stock from use."""
    p = _edit("expiry_date", date(2027, 1, 1), date(2026, 9, 1))
    assert p.sensitive is True and p.requires_approval is False


def test_extending_an_expiry_requires_approval():
    """Pushing it later puts out-of-date stock back on the shelf, and is the
    easiest way to make an expiry write-off disappear."""
    p = _edit("expiry_date", date(2026, 1, 1), date(2027, 1, 1))
    assert p.requires_approval is True


def test_expiry_accepts_iso_strings_from_the_form():
    assert _edit("expiry_date", "2026-01-01", "2027-01-01").requires_approval is True


def test_expiry_cannot_be_cleared():
    with pytest.raises(A.AdminEditError, match="cannot be cleared"):
        _edit("expiry_date", date(2026, 1, 1), None)


def test_a_malformed_date_is_rejected_not_coerced():
    with pytest.raises(A.AdminEditError, match="not a date"):
        _edit("expiry_date", date(2026, 1, 1), "next tuesday")


# ── releasing blocked stock always needs a second person ──────────────────
@pytest.mark.parametrize("field", ["is_recalled", "is_quarantined", "cold_chain_breach"])
def test_releasing_blocked_stock_requires_approval(field):
    assert _edit(field, True, False).requires_approval is True


@pytest.mark.parametrize("field", ["is_recalled", "is_quarantined", "cold_chain_breach"])
def test_blocking_stock_is_always_immediately_allowed(field):
    """Taking stock out of use must never wait for a signature."""
    p = _edit(field, False, True)
    assert p.requires_approval is False and p.sensitive is True


def test_any_sensitive_change_to_a_controlled_drug_requires_approval():
    p = _edit("expiry_date", date(2027, 1, 1), date(2026, 9, 1), is_controlled=True)
    assert p.requires_approval is True


# ── bulk edits ────────────────────────────────────────────────────────────
def test_a_normal_bulk_relocation_is_allowed():
    assert A.validate_bulk("storage_location", "B2", ["1", "2", "3"],
                           reason="shelf reorganised") is None


def test_bulk_is_capped_so_a_misclick_cannot_reprice_the_pharmacy():
    ids = [str(i) for i in range(A.BULK_MAX_ROWS + 1)]
    with pytest.raises(A.AdminEditError, match="bulk limit"):
        A.validate_bulk("storage_location", "B2", ids, reason="x")


def test_fields_needing_judgement_are_not_bulk_editable():
    for field in ("expiry_date", "lot_number", "unit_cost", "is_recalled"):
        with pytest.raises(A.AdminEditError, match="per-row judgement"):
            A.validate_bulk(field, "x", ["1"], reason="x")


def test_lots_can_be_quarantined_in_bulk_but_never_released_in_bulk():
    assert A.validate_bulk("is_quarantined", True, ["1", "2"], reason="recall sweep") is None
    with pytest.raises(A.AdminEditError, match="one at a time"):
        A.validate_bulk("is_quarantined", False, ["1", "2"], reason="cleared")


def test_bulk_rejects_an_empty_or_duplicated_selection():
    with pytest.raises(A.AdminEditError, match="no rows"):
        A.validate_bulk("storage_location", "B", [], reason="x")
    with pytest.raises(A.AdminEditError, match="duplicate"):
        A.validate_bulk("storage_location", "B", ["1", "1"], reason="x")


# ── search box ────────────────────────────────────────────────────────────
def test_one_search_box_serves_every_lookup_an_admin_does():
    assert A.normalize_query("متفورمین")["kind"] == "name"
    assert A.normalize_query("Metformin")["kind"] == "name"
    assert A.normalize_query("06221234567890")["kind"] == "gtin"
    assert A.normalize_query("00093721256")["kind"] == "irc_or_ndc"
    assert A.normalize_query("LOT-2026/04")["kind"] == "lot"
    assert A.normalize_query("")["kind"] == "empty"


def test_a_scanned_barcode_with_separators_still_reads_as_a_gtin():
    assert A.normalize_query("0622-1234-567890")["kind"] == "gtin"


def test_days_supply_never_divides_by_zero():
    assert A.days_supply(100, 0) is None
    assert A.days_supply(100, None) is None
    assert A.days_supply(100, 4) == 25.0


def test_every_named_filter_has_a_persian_label():
    assert all(isinstance(v, str) and v for v in A.FILTERS.values())
