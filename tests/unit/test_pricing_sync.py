"""Daily price-sync proposal computation (pure).

A sync feed (updated announced prices and/or distributor invoices) does NOT
overwrite catalog prices directly — it produces price-change PROPOSALS that a
pharmacy manager approves, mirroring the Iranian apps' 'proposed new price'
workflow. Effective price = max(announced, invoice).
"""
from decimal import Decimal

from services.core.drug_catalog.schema import CatalogRecord
from services.core.drug_catalog.pricing_sync import compute_proposals


def _rec(irc, announced, invoice=None):
    return CatalogRecord(irc=irc, name_fa=irc, generic_name="x", dosage_form="tablet",
                         strength="1", announced_price=Decimal(str(announced)),
                         last_invoice_price=None if invoice is None else Decimal(str(invoice)))


def test_increase_when_announced_rises():
    current = [_rec("A", 10000)]
    props = compute_proposals(current, [{"irc": "A", "announced_price": "13000"}])
    assert len(props) == 1
    p = props[0]
    assert p.kind == "increase" and p.proposed_effective == Decimal("13000")
    assert p.current_effective == Decimal("10000") and p.delta == Decimal("3000")


def test_invoice_above_announced_drives_proposal():
    # announced unchanged (10000) but a new invoice of 12000 → sell at 12000
    current = [_rec("A", 10000)]
    props = compute_proposals(current, [{"irc": "A", "last_invoice_price": "12000"}])
    assert props[0].proposed_effective == Decimal("12000") and props[0].kind == "increase"


def test_unchanged_is_not_proposed():
    current = [_rec("A", 10000, invoice=8000)]   # effective 10000
    props = compute_proposals(current, [{"irc": "A", "announced_price": "10000"}])
    assert props == []


def test_new_item_proposed_as_new():
    props = compute_proposals([], [{"irc": "Z", "name_fa": "داروی نو", "announced_price": "5000"}])
    assert len(props) == 1 and props[0].kind == "new"
    assert props[0].current_effective == Decimal("0") and props[0].proposed_effective == Decimal("5000")


def test_decrease_detected_and_pct_computed():
    current = [_rec("A", 20000)]
    props = compute_proposals(current, [{"irc": "A", "announced_price": "15000"}])
    p = props[0]
    assert p.kind == "decrease" and p.delta == Decimal("-5000")
    assert round(p.pct_change, 1) == -25.0
