"""Drug catalog core — ingredient grouping + cheaper-alternative lookup.

Pure (no DB): pins the logic that powers the reception 'switch to a cheaper
brand/generic with the same active ingredient' lever.
"""
from decimal import Decimal

from services.core.drug_catalog.schema import CatalogRecord, ingredient_key
from services.core.drug_catalog.alternatives import find_alternatives
from services.core.pricing_ir.engine import ItemCategory


def _rec(irc, generic, strength, form, price, brand=None, invoice=None, generic_flag=True):
    return CatalogRecord(
        irc=irc, name_fa=brand or generic, generic_name=generic, dosage_form=form,
        strength=strength, announced_price=Decimal(str(price)),
        last_invoice_price=None if invoice is None else Decimal(str(invoice)),
        brand_name=brand, is_generic=generic_flag,
    )


def test_ingredient_key_groups_same_active_strength_form():
    brand = _rec("B1", "atorvastatin", "20mg", "tablet", 250000, brand="Lipitor", generic_flag=False)
    gen   = _rec("G1", "Atorvastatin", "20 MG", "Tablet", 90000)
    other = _rec("O1", "atorvastatin", "40mg", "tablet", 120000)
    assert brand.ingredient_key == gen.ingredient_key      # brand & generic group together
    assert brand.ingredient_key != other.ingredient_key    # different strength ⇒ different group


def test_effective_price_uses_higher_of_announced_and_invoice():
    r = _rec("X", "metformin", "500mg", "tablet", 8000, invoice=11000)
    assert r.effective_price == Decimal("11000")


def test_find_alternatives_returns_cheaper_same_ingredient_sorted():
    records = [
        _rec("CUR", "atorvastatin", "20mg", "tablet", 250000, brand="Lipitor", generic_flag=False),
        _rec("A",   "atorvastatin", "20mg", "tablet", 90000),       # cheaper generic
        _rec("B",   "atorvastatin", "20mg", "tablet", 150000, brand="Atorva"),  # cheaper brand
        _rec("C",   "atorvastatin", "20mg", "tablet", 300000),      # pricier → excluded by default
        _rec("D",   "atorvastatin", "40mg", "tablet", 50000),       # different strength → excluded
        _rec("E",   "rosuvastatin", "20mg", "tablet", 40000),       # different ingredient → excluded
    ]
    alts = find_alternatives(records, "CUR")
    assert [a.record.irc for a in alts] == ["A", "B"]              # cheaper-first
    assert alts[0].savings_vs_current == Decimal("160000")          # 250000 - 90000
    assert alts[1].savings_vs_current == Decimal("100000")


def test_find_alternatives_can_include_pricier_when_requested():
    records = [
        _rec("CUR", "metformin", "500mg", "tablet", 80000),
        _rec("A",   "metformin", "500mg", "tablet", 120000),
    ]
    assert find_alternatives(records, "CUR") == []                  # none cheaper
    incl = find_alternatives(records, "CUR", only_cheaper=False)
    assert [a.record.irc for a in incl] == ["A"]


def test_find_alternatives_unknown_irc_returns_empty():
    assert find_alternatives([], "NOPE") == []


def test_ingredient_key_function_normalizes_casing_and_spacing():
    assert ingredient_key("Metformin", "500 MG", "Tablet") == ingredient_key("metformin", "500mg", "tablet")


def test_build_records_handles_aliases_and_skips_bad_rows():
    from services.core.drug_catalog.importer import build_records
    rows = [
        {"کد": "IRC9", "نام": "متفورمین", "ماده_موثره": "metformin", "دوز": "500mg",
         "شکل": "tablet", "قیمت": "8,000", "قیمت_خرید": "11000", "نوع": "دارو"},
        {"name": "missing irc"},          # skipped (no irc)
        {"irc": "X", "generic": "x"},     # skipped (no name)
    ]
    recs = build_records(rows)
    assert len(recs) == 1
    r = recs[0]
    assert r.irc == "IRC9" and r.generic_name == "metformin"
    assert r.announced_price == Decimal("8000") and r.last_invoice_price == Decimal("11000")
    assert r.effective_price == Decimal("11000")          # max rule


def test_seed_loads_and_alternatives_resolve():
    from services.core.drug_catalog.importer import load_seed
    recs = load_seed()
    assert len(recs) >= 10
    # Lipitor 20mg (effective = max(260k, 275k) = 275k) → cheaper generics exist
    alts = find_alternatives(recs, "1228000000000002")
    ircs = [a.record.irc for a in alts]
    assert "1228000000000001" in ircs and "1228000000000003" in ircs   # both generics
    assert alts[0].record.irc == "1228000000000001"                    # cheapest first (92k)
    assert alts[0].savings_vs_current == Decimal("183000")             # 275000 - 92000
