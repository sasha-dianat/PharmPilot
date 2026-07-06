"""Smart insurance-coverage extractor — column inference + fuzzy record linkage.

The insurer دارونامه arrives in arbitrary shapes (Excel/CSV/HTML, Persian or
English headers, sometimes meaningless headers). The extractor must (1) infer
what each column means, from headers when possible and from VALUE DISTRIBUTIONS
when not, and (2) link each row to catalog products by a cascade of exact and
fuzzy matches, tiered by confidence.
"""
from decimal import Decimal

from services.core.drug_catalog.coverage_import import (
    infer_columns, link_rows, build_coverage,
)
from services.core.drug_catalog.schema import CatalogRecord


def _cat(irc, generic, strength, form, price, name_fa=None):
    return CatalogRecord(irc=irc, name_fa=name_fa or generic, generic_name=generic,
                         dosage_form=form, strength=strength,
                         announced_price=Decimal(str(price)))


CATALOG = [
    _cat("111", "metformin hydrochloride", "500 mg", "TABLET", 8000, "متفورمین ۵۰۰"),
    _cat("222", "atorvastatin", "20 mg", "TABLET", 92000, "آتورواستاتین ۲۰"),
    _cat("333", "liraglutide", "6 mg/1mL", "INJECTION, SOLUTION", 25000000, "ویکتوزا"),
    _cat("444", "amoxicillin", "500 mg", "CAPSULE", 15000, "آموکسی‌سیلین ۵۰۰"),
]


# ── column inference ──────────────────────────────────────────────────────────
def test_infer_columns_from_persian_headers():
    rows = [{"نام ژنریک": "METFORMIN HCL 500MG TAB", "تعهد بیمه": "دارد",
             "درصد سازمان": "70", "قیمت مورد تعهد": "8000", "سقف تجویز": "100"}]
    roles = infer_columns(rows)
    assert roles["نام ژنریک"] == "drug_name"
    assert roles["تعهد بیمه"] == "covered"
    assert roles["درصد سازمان"] == "share_pct"
    assert roles["قیمت مورد تعهد"] == "reference_price"
    assert roles["سقف تجویز"] == "ceiling"


def test_infer_columns_from_values_when_headers_are_junk():
    # headers carry no signal → infer from value distributions
    rows = [
        {"c1": "ATORVASTATIN 20MG TAB", "c2": "1", "c3": "90", "c4": "92000"},
        {"c1": "METFORMIN 500MG TAB",   "c2": "0", "c3": "70", "c4": "8000"},
        {"c1": "AMOXICILLIN 500MG CAP", "c2": "1", "c3": "70", "c4": "15000"},
    ]
    roles = infer_columns(rows)
    assert roles["c1"] == "drug_name"       # mostly-alpha long strings
    assert roles["c2"] == "covered"         # binary 0/1
    assert roles["c3"] == "share_pct"       # ints in (0,100]
    assert roles["c4"] == "reference_price" # large money-like ints


# ── record linkage ────────────────────────────────────────────────────────────
def test_link_exact_and_fuzzy():
    rows = [
        {"drug_name": "METFORMIN HCL 500 MG TABLET", "covered": "1", "share_pct": "70", "reference_price": "8000"},
        {"drug_name": "ATORVASTATINE 20MG TAB", "covered": "1", "share_pct": "70", "reference_price": "90000"},  # misspelt
        {"drug_name": "XYZ UNKNOWN DRUG", "covered": "1", "share_pct": "70", "reference_price": "5"},
    ]
    links = link_rows(rows, CATALOG)
    by_name = {l.row["drug_name"]: l for l in links}
    m = by_name["METFORMIN HCL 500 MG TABLET"]
    assert m.matched and m.record.irc == "111" and m.confidence >= 0.85
    a = by_name["ATORVASTATINE 20MG TAB"]
    assert a.matched and a.record.irc == "222" and a.confidence >= 0.6
    x = by_name["XYZ UNKNOWN DRUG"]
    assert not x.matched


def test_link_by_persian_name():
    rows = [{"drug_name": "ویکتوزا", "covered": "1", "share_pct": "90", "reference_price": "20000000"}]
    links = link_rows(rows, CATALOG)
    assert links[0].matched and links[0].record.irc == "333"


def test_link_by_irc_wins_over_name():
    rows = [{"irc": "444", "drug_name": "totally wrong name", "covered": "1"}]
    links = link_rows(rows, CATALOG)
    assert links[0].matched and links[0].record.irc == "444" and links[0].confidence == 1.0


# ── coverage building ─────────────────────────────────────────────────────────
def test_build_coverage_json():
    rows = [{"drug_name": "METFORMIN HCL 500 MG TABLET", "covered": "دارد",
             "share_pct": "70", "reference_price": "8,000", "ceiling": "۱۰۰"}]
    links = link_rows(rows, CATALOG)
    cov = build_coverage(links, insurer="tamin", min_confidence=0.6)
    assert "111" in cov.applied
    entry = cov.applied["111"]["tamin"]
    assert entry["covered"] is True
    assert entry["share_pct"] == 70
    assert entry["reference_price"] == 8000
    assert entry["ceiling"] == 100
    assert cov.stats["applied"] == 1


def test_build_coverage_not_covered_row():
    rows = [{"drug_name": "AMOXICILLIN 500MG CAP", "covered": "ندارد"}]
    links = link_rows(rows, CATALOG)
    cov = build_coverage(links, insurer="salamat", min_confidence=0.6)
    assert cov.applied["444"]["salamat"]["covered"] is False
