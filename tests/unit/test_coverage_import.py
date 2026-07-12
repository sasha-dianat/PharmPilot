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


def test_infer_columns_exact_header_wins_over_substring():
    # "درصد تعهد" (org share %) contains "تعهد" — must NOT be grabbed by `covered`,
    # even when it appears BEFORE the "تعهد بیمه" covered flag. The exact share_pct
    # alias has to win so the flag column still resolves to `covered`.
    rows = [{"نام دارو": "METFORMIN 500MG TAB", "درصد تعهد": "70",
             "قیمت مورد تعهد": "8000", "تعهد بیمه": "دارد"}]
    roles = infer_columns(rows)
    assert roles["درصد تعهد"] == "share_pct"
    assert roles["تعهد بیمه"] == "covered"
    assert roles["قیمت مورد تعهد"] == "reference_price"


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


def test_infer_columns_folds_arabic_yeh_in_headers():
    # the real salamat .xls uses Arabic ي: 'کد ژنريک' must still match 'کد ژنریک'
    rows = [{"کد ژنريک": "14083", "عنوان": "ABCIXIMAB VIAL 10MG",
             "درصد سهم سازمان": "71.693", "قيمت": "3,100,000"}]
    roles = infer_columns(rows)
    assert roles["کد ژنريک"] == "generic_code"
    assert roles["عنوان"] == "drug_name"
    assert roles["درصد سهم سازمان"] == "share_pct"
    assert roles["قيمت"] == "reference_price"


def test_share_pct_with_percent_sign_and_float():
    rows = [{"drug_name": "METFORMIN HCL 500 MG TABLET", "share_pct": "70%",
             "reference_price": "8,000"}]
    links = link_rows(rows, CATALOG)
    cov = build_coverage(links, insurer="salamat", min_confidence=0.6)
    assert cov.applied["111"]["salamat"]["share_pct"] == 70


def test_empty_covered_cell_means_covered():
    rows = [{"drug_name": "METFORMIN HCL 500 MG TABLET", "covered": ""}]
    links = link_rows(rows, CATALOG)
    cov = build_coverage(links, insurer="salamat", min_confidence=0.6)
    assert cov.applied["111"]["salamat"]["covered"] is True


def test_blocking_still_links_typos_within_prefix():
    # blocking keys on the first 3 latin chars — "ATORVASTATINE" (typo) and
    # "atorvastatin" share "ato", so fuzzy linkage must still find it
    rows = [{"drug_name": "ATORVASTATINE 20MG TAB"}]
    links = link_rows(rows, CATALOG)
    assert links[0].matched and links[0].record.irc == "222"


def test_blocking_scales_linearly_not_quadratically():
    # 1000 rows × 5000 products must finish in seconds, not minutes. Names get
    # DIVERSE 3-letter prefixes (like real ingredients) so the blocking index
    # actually partitions the catalog — a single shared prefix would collapse
    # everything into one bucket and measure brute force instead.
    import time

    def name(i: int) -> str:
        p = chr(97 + (i // 676) % 26) + chr(97 + (i // 26) % 26) + chr(97 + i % 26)
        return f"{p}statin{i:05d}"

    big_catalog = [
        _cat(f"C{i:05d}", name(i), "10 mg", "TABLET", 1000 + i)
        for i in range(5000)
    ]
    rows = [{"drug_name": f"{name(i % 5000).upper()} 10 MG TABLET"} for i in range(1000)]
    t0 = time.time()
    links = link_rows(rows, big_catalog)
    elapsed = time.time() - t0
    assert elapsed < 20, f"link_rows took {elapsed:.1f}s — blocking not effective"
    assert sum(1 for l in links if l.matched) >= 900   # same-prefix rows still link


def test_strength_mg_normalizes_units():
    # 0.05 mg and 50 microgram are the SAME dose in different units
    from services.core.drug_catalog.coverage_import import _strength_mg, _mg_agree
    assert _strength_mg("OCTREOTIDE 0.05 mg") == {0.05}
    assert _strength_mg("50 microgram") == {0.05}
    assert _strength_mg("OCTREOTIDE 30MG") == {30.0}
    assert _strength_mg("6 mg/1mL") == {6.0}          # concentration → the mg part
    assert _strength_mg("500IU") == set()             # non-mass units ignored
    assert _mg_agree({0.05}, {0.05}) and not _mg_agree({30.0}, {0.05})


# same-generic products of DIFFERENT strength must not silently share coverage
_OCTREO = [
    _cat("O50", "octreotide", "50 microgram", "INJECTION", 32600, "اکتروتاید ۵۰"),
    _cat("O30", "octreotide", "30 mg", "INJECTION", 66000000, "اکتروتاید ۳۰"),
]


def test_cross_strength_row_not_autoapplied():
    # a formulary row for octreotide 30MG must NOT auto-apply onto the 50mcg
    # product; with no 30mg-token confusion it links to O30, and even if it
    # reaches O50 the confidence stays below the 0.75 auto-apply line.
    links = link_rows([{"drug_name": "OCTREOTIDE 30 mg", "reference_price": "66000000"}], _OCTREO)
    l = links[0]
    assert l.record is not None and l.record.irc == "O30"     # picks the right strength
    cov = build_coverage(links, insurer="salamat", min_confidence=0.75)
    # the cheap 50mcg product must NOT receive the 66M reference
    assert "O50" not in cov.applied


def test_microgram_milligram_equivalent_still_matches():
    # 0.05 mg row ↔ 50 microgram catalog: same dose, must match with the
    # strength bonus (not demoted by the conflict rule)
    links = link_rows([{"drug_name": "OCTREOTIDE 0.05 mg"}], _OCTREO)
    l = links[0]
    assert l.matched and l.record.irc == "O50" and l.confidence >= 0.8


def test_real_salamat_headers_map_price_not_brand_code():
    # the REAL salamat .xls headers (post read_table normalization): the Arabic-yeh
    # 'قيمت' column must claim reference_price via the folded alias, so the numeric
    # brand-code column can't steal it through value inference.
    rows = [
        {"رديف": "1", "کد_ژنريک": "00001", "کد_برند": "14083",
         "عنوان": "ABCIXIMAB VIAL 10MG", "شرايط_تعهد": "",
         "سهم_سازمان": "70%", "قيمت": "3,100,000"},
        {"رديف": "2", "کد_ژنريک": "00522", "کد_برند": "20991",
         "عنوان": "ACETAMINOPHEN TAB 500MG", "شرايط_تعهد": "بيمارستاني",
         "سهم_سازمان": "70%", "قيمت": "29,250"},
    ]
    roles = infer_columns(rows)
    assert roles["قيمت"] == "reference_price"
    assert roles.get("کد_برند") != "reference_price"
    assert roles["سهم_سازمان"] == "share_pct"
    assert roles["عنوان"] == "drug_name"
    assert roles["کد_ژنريک"] == "generic_code"
