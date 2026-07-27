"""دارونامه acquisition engine — pure parts: strategy sniffing, paginated
fetching (injected fetcher, no network), column-override precedence, and the
staged-vs-current coverage diff."""
import json

import pytest

from services.core.drug_catalog.coverage_harvest import (
    sniff_strategy, fetch_rows, resolve_roles, compute_diff,
)

CSV = "کد فرآورده,نام دارو,درصد تعهد\n123,استامینوفن,70\n".encode("utf-8")
HTML_TABLE = "<html><body><table><tr><th>نام دارو</th><th>درصد تعهد</th></tr><tr><td>استامینوفن</td><td>70</td></tr></table></body></html>".encode("utf-8")
JSON_BODY = json.dumps({"data": {"items": [{"name": "استامینوفن", "share": 70}]}}).encode("utf-8")
XLSX_MAGIC = b"PK\x03\x04" + b"\x00" * 32


# ── sniffer ───────────────────────────────────────────────────────────────────
def test_sniff_xlsx_magic():
    assert sniff_strategy(XLSX_MAGIC, "application/octet-stream", "https://x/y") == "file_url"

def test_sniff_json():
    assert sniff_strategy(JSON_BODY, "application/json", "https://x/api") == "json_api"

def test_sniff_html_table():
    assert sniff_strategy(HTML_TABLE, "text/html", "https://x/list") == "html_table"

def test_sniff_html_table_with_page_placeholder_is_paginated():
    assert sniff_strategy(HTML_TABLE, "text/html", "https://x/list?page={page}") == "paginated_html"

def test_sniff_csv_falls_to_file_url():
    assert sniff_strategy(CSV, "text/csv", "https://x/d.csv") == "file_url"


# ── strategies via injected fetcher ──────────────────────────────────────────
def _fake_fetcher(pages: dict[str, bytes], content_type="text/html"):
    def fetch(url: str):
        body = pages.get(url)
        return (200, body, content_type) if body is not None else (404, b"", content_type)
    return fetch


def test_file_url_strategy_parses_csv_rows():
    fetch = _fake_fetcher({"https://x/d.csv": CSV}, "text/csv")
    rows, pages = fetch_rows("https://x/d.csv", "file_url", {}, fetch)
    assert pages == 1 and len(rows) == 1
    assert rows[0]["نام_دارو"] == "استامینوفن"      # read_table normalizes headers


def test_paginated_html_stops_on_empty_page():
    p1 = HTML_TABLE
    p2 = HTML_TABLE.replace("استامینوفن".encode(), "متفورمین".encode())
    empty = "<html><body><table><tr><th>نام دارو</th></tr></table></body></html>".encode()
    fetch = _fake_fetcher({"https://x/l?page=1": p1, "https://x/l?page=2": p2,
                           "https://x/l?page=3": empty})
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 10, "delay_sec": 0}, fetch)
    assert pages == 3 and len(rows) == 2


def test_paginated_html_stops_on_duplicate_page():
    fetch = _fake_fetcher({f"https://x/l?page={n}": HTML_TABLE for n in (1, 2, 3, 4)})
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 10, "delay_sec": 0}, fetch)
    assert len(rows) == 1                            # page 2 duplicates page 1 → stop


def test_paginated_html_respects_max_pages():
    def infinite(url):
        n = int(url.rsplit("=", 1)[1])
        body = HTML_TABLE.replace(b"70", str(n).encode())
        return 200, body, "text/html"
    rows, pages = fetch_rows("https://x/l?page={page}", "paginated_html",
                             {"page_start": 1, "max_pages": 3, "delay_sec": 0}, infinite)
    assert pages == 3


def test_json_api_descends_record_path():
    fetch = _fake_fetcher({"https://x/api?page=1": JSON_BODY,
                           "https://x/api?page=2": json.dumps({"data": {"items": []}}).encode()},
                          "application/json")
    rows, pages = fetch_rows("https://x/api?page={page}", "json_api",
                             {"record_path": "data.items", "page_start": 1,
                              "max_pages": 5, "delay_sec": 0}, fetch)
    assert rows == [{"name": "استامینوفن", "share": 70}]


# ── column overrides ─────────────────────────────────────────────────────────
def test_infer_columns_survives_read_table_underscored_headers():
    """read_table normalizes 'درصد تعهد' → 'درصد_تعهد'. The exact-alias pass must
    still beat the 'تعهد' substring (covered) — requires _norm_header to fold
    underscores back to spaces. Regression companion to commit cfaf3c7."""
    from services.core.drug_catalog.coverage_import import infer_columns
    rows = [{"نام_دارو": "METFORMIN 500MG TAB", "درصد_تعهد": "70",
             "قیمت_مورد_تعهد": "8000", "تعهد_بیمه": "دارد"}]
    roles = infer_columns(rows)
    assert roles["درصد_تعهد"] == "share_pct"
    assert roles["تعهد_بیمه"] == "covered"
    assert roles["قیمت_مورد_تعهد"] == "reference_price"


def test_resolve_roles_overrides_beat_inference_and_ignore_drops():
    rows = [{"c1": "ACETAMINOPHEN 500MG TAB", "c2": "70", "c3": "junk"}]
    roles = resolve_roles(rows, {"c2": "share_pct", "c3": "ignore"})
    assert roles["c1"] == "drug_name"        # inferred
    assert roles["c2"] == "share_pct"        # override wins
    assert "c3" not in roles                 # ignored


# ── diff ─────────────────────────────────────────────────────────────────────
def _staged(irc, **entry):
    return {irc: {"tamin": {"covered": True, **entry}}}


def test_diff_added_changed_removed():
    staged = {**_staged("111", share_pct=70), **_staged("222", share_pct=90)}
    current = {"222": {"covered": True, "share_pct": 70},          # changed 70→90
               "333": {"covered": True, "share_pct": 50}}          # removed
    d = compute_diff(staged, current, insurer="tamin")
    assert d["added"] == 1 and d["changed"] == 1 and d["removed"] == 1
    ch = d["samples"]["changed"][0]
    assert ch["irc"] == "222" and ch["fields"][0] == {"field": "share_pct", "old": 70, "new": 90}
    assert d["samples"]["removed"] == ["333"]


def test_diff_ignores_match_metadata_fields():
    staged = {"111": {"tamin": {"covered": True, "share_pct": 70,
                                "match_confidence": 0.9, "match_method": "irc"}}}
    current = {"111": {"covered": True, "share_pct": 70,
                       "match_confidence": 0.6, "match_method": "ingredient"}}
    d = compute_diff(staged, current, insurer="tamin")
    assert d["changed"] == 0                 # only covered/share_pct/reference_price/ceiling count


def test_diff_samples_capped_at_50():
    staged = {str(i): {"tamin": {"covered": True}} for i in range(80)}
    d = compute_diff(staged, {}, insurer="tamin")
    assert d["added"] == 80 and len(d["samples"]["added"]) == 50


# ── service-layer pure pieces ────────────────────────────────────────────────
from services.core.drug_catalog import harvest_lock
from services.core.drug_catalog.coverage_harvest import (
    probe_payload, stage_run_payload, CoverageHarvestState,
)


def test_probe_payload_detects_and_samples():
    fetch = _fake_fetcher({"https://x/l": HTML_TABLE})
    p = probe_payload("https://x/l", settings={}, fetch=fetch)
    assert p["detected_strategy"] == "html_table"
    assert p["sample_rows"] and p["inferred_columns"]
    assert p["row_count_sampled"] == 1


def test_probe_payload_unreachable_raises():
    fetch = _fake_fetcher({})
    with pytest.raises(RuntimeError):
        probe_payload("https://x/nope", settings={}, fetch=fetch)


def test_stage_run_payload_builds_stats_review_ids_and_diff():
    from services.core.drug_catalog.schema import CatalogRecord
    from decimal import Decimal
    catalog = [CatalogRecord(irc="123", name_fa="استامینوفن", generic_name="acetaminophen",
                             dosage_form="TABLET", strength="500 mg",
                             announced_price=Decimal("8000"))]
    rows = [{"کد فرآورده": "123", "نام دارو": "استامینوفن", "درصد تعهد": "70"}]
    payload = stage_run_payload(rows, catalog, insurer="tamin",
                                overrides=None, current={},
                                min_confidence=0.75)
    assert payload["stats"]["rows"] == 1 and payload["stats"]["applied"] == 1
    assert payload["staged"]["123"]["tamin"]["share_pct"] == 70
    assert payload["diff"]["added"] == 1
    assert all("id" in item for item in payload["review"])


def test_stage_run_payload_surfaces_ingredient_groups_and_bulk():
    from services.core.drug_catalog.schema import CatalogRecord
    from decimal import Decimal
    catalog = [CatalogRecord(irc="M5", name_fa="مت ۵۰۰", generic_name="metformin",
                             dosage_form="TABLET", strength="500 mg",
                             announced_price=Decimal("1"))]
    rows = [{"نام دارو": "METFORMIN 500 mg TABLET"},
            {"نام دارو": "METFORMIN 1000 mg TABLET"},
            {"نام دارو": "METFORMIN"}]        # bare sibling → bulk candidate
    payload = stage_run_payload(rows, catalog, insurer="tamin",
                                overrides=None, current={}, min_confidence=0.75)
    assert "METFORMIN" in payload["groups"]["bulk_candidates"]
    assert payload["stats"]["bulk_candidates"] >= 1
    assert payload["stats"]["ingredient_groups"] >= 1


def test_harvest_state_snapshot_has_lock_holder():
    harvest_lock.force_release()
    st = CoverageHarvestState(running=True, insurer="tamin", phase="fetching")
    snap = st.snapshot()
    assert snap["insurer"] == "tamin" and "lock_holder" in snap


# ── price_conflict (reference-vs-announced divergence) ───────────────────────
from decimal import Decimal
from services.core.drug_catalog.coverage_harvest import price_conflict


def test_price_conflict_below_threshold_is_none():
    # 10% gap, threshold 25 → no conflict
    assert price_conflict("1", "دارو", 100_000, 110_000, 25) is None


def test_price_conflict_above_threshold_returns_signed_rounded_dict():
    # reference above announced → positive gap
    c = price_conflict("1", "دارو", 30_000, 40_000, 25)
    assert c == {"irc": "1", "name_fa": "دارو", "announced_price": 30_000,
                 "reference_price": 40_000, "gap_pct": 33.3}


def test_price_conflict_negative_gap_when_reference_below_announced():
    # reference below announced → signed negative gap; abs(-30) >= 25 → conflict
    c = price_conflict("2", "دارو", 100_000, 70_000, 25)
    assert c["gap_pct"] == -30.0
    assert c["announced_price"] == 100_000 and c["reference_price"] == 70_000


def test_price_conflict_at_threshold_boundary_is_conflict():
    # exactly 25% → included (>=)
    assert price_conflict("3", "د", 100_000, 125_000, 25)["gap_pct"] == 25.0


def test_price_conflict_announced_zero_or_falsy_is_none():
    assert price_conflict("4", "د", 0, 40_000, 25) is None
    assert price_conflict("4", "د", None, 40_000, 25) is None
    assert price_conflict("4", "د", Decimal("0"), 40_000, 25) is None


def test_price_conflict_negative_announced_is_none():
    assert price_conflict("5", "د", -100, 40_000, 25) is None


def test_price_conflict_reference_none_is_none():
    assert price_conflict("6", "د", 100_000, None, 25) is None


def test_price_conflict_accepts_decimal_and_string_numerics():
    c = price_conflict("7", "د", Decimal("30000"), "40000", 25)
    assert c["announced_price"] == 30_000 and c["reference_price"] == 40_000
    assert c["gap_pct"] == 33.3


def test_read_table_strips_nul_bytes(tmp_path):
    # legacy .xls exports pad strings with NULs; Postgres JSONB rejects \x00
    p = tmp_path / "nul.csv"
    p.write_bytes("drug_name,share\nACETAMIN\x00OPHEN,70\x00\n".encode("utf-8"))
    from services.core.drug_catalog.excel_import import read_table
    rows = read_table(p)
    assert rows and "\x00" not in rows[0]["drug_name"]
    assert all("\x00" not in v for r in rows for v in r.values())


# ── diagnose_discrepancy (Persian root-cause hints) ─────────────────────────
from services.core.drug_catalog.coverage_harvest import diagnose_discrepancy


def test_diagnose_cross_strength_sibling():
    # octreotide 50mcg: reference 66M inherited from the 30mg sibling → cross-strength
    catalog = {"announced_price": 1_000_000, "strength": "50mcg/ml",
               "country": "ایران", "generic_name": "octreotide", "atc": "H01CB02"}
    entry = {"reference_price": 66_000_000, "match_confidence": 1.0,
             "match_method": "exact"}
    siblings = [{"name_fa": "ساندوستاتین لار", "strength": "30mg",
                 "announced_price": 60_000_000, "reference_price": 66_000_000}]
    hints = diagnose_discrepancy(catalog, entry, siblings)
    assert any("بین‌قدرتی" in h for h in hints)       # names the cross-strength cause
    assert any("30mg" in h for h in hints)             # names the sibling strength
    assert not any("قیمت اعلامی قدیمی" in h for h in hints)  # not the stale hint


def test_diagnose_stale_price_exact_match_no_matching_sibling():
    # big ratio, exact/high-confidence match, no sibling with a matching reference
    catalog = {"announced_price": 1_000_000, "strength": "50mcg/ml",
               "country": "ایران", "generic_name": "octreotide", "atc": "H01CB02"}
    entry = {"reference_price": 66_000_000, "match_confidence": 1.0,
             "match_method": "exact"}
    siblings = [{"name_fa": "قلم دیگر", "strength": "100mcg",
                 "announced_price": 2_000_000, "reference_price": 2_100_000}]
    hints = diagnose_discrepancy(catalog, entry, siblings)
    assert any("قیمت اعلامی قدیمی" in h for h in hints)
    assert any("66,000,000" in h for h in hints)       # money formatted with commas
    assert not any("بین‌قدرتی" in h for h in hints)


def test_diagnose_low_confidence_match():
    # conflict with a low-confidence match and no cross-strength sibling
    catalog = {"announced_price": 1_000_000, "strength": "50mcg/ml",
               "country": "ایران", "generic_name": "octreotide", "atc": "H01CB02"}
    entry = {"reference_price": 66_000_000, "match_confidence": 0.6,
             "match_method": "fuzzy"}
    hints = diagnose_discrepancy(catalog, entry, [])
    assert any("کم‌اطمینان" in h and "0.6" in h and "fuzzy" in h for h in hints)


def test_diagnose_clean_entry_returns_empty():
    catalog = {"announced_price": 1_000_000, "strength": "500mg",
               "country": "ایران", "generic_name": "metformin", "atc": "A10BA02"}
    entry = {"reference_price": 950_000, "match_confidence": 1.0,
             "match_method": "exact"}
    assert diagnose_discrepancy(catalog, entry, []) == []


def test_diagnose_catalog_gaps_appended():
    # ratio clean, but missing country/generic/atc each add a hint
    catalog = {"announced_price": 1_000_000, "strength": "500mg",
               "country": None, "generic_name": "  ", "atc": None}
    entry = {"reference_price": 950_000}
    hints = diagnose_discrepancy(catalog, entry, [])
    assert any("کشور نامشخص" in h for h in hints)
    assert any("ژنریک نامشخص" in h for h in hints)
    assert any("کد ATC" in h for h in hints)


# ── staged manual upload: extraction + multi-file merge ──────────────────────
from services.core.drug_catalog.coverage_harvest import merge_row_sets, rows_from_upload


def test_rows_from_upload_json_list_and_wrapped():
    rows = [{"drug_name": "A", "drug_code": "1"}, {"drug_name": "B"}]
    assert rows_from_upload("x.json", json.dumps(rows).encode()) == rows
    # container key is unknown → the LARGEST list-of-dicts wins
    doc = {"meta": {"n": 2}, "small": [{"k": 1}], "records": rows}
    assert rows_from_upload("x.json", json.dumps(doc, ensure_ascii=False).encode()) == rows
    assert rows_from_upload("x.json", b"not json {") == []


def test_rows_from_upload_csv_still_works():
    body = "drug_code,drug_name\n01211,METFORMIN 500\n".encode()
    rows = rows_from_upload("tamin.csv", body)
    assert rows and rows[0]["drug_name"] == "METFORMIN 500"


def test_merge_row_sets_joins_by_code_and_fills_gaps():
    # tamin .json has prices; .csv has share% — one merged row per code,
    # earlier file wins conflicts
    json_rows = [{"drug_code": "1", "drug_name": "METFORMIN 500",
                  "price": "21000", "share": ""}]
    csv_rows = [{"drug_code": "1", "drug_name": "metformin-500-alt",
                 "share": "70"},
                {"drug_code": "2", "drug_name": "OTHER"}]
    merged = merge_row_sets([json_rows, csv_rows])
    by_code = {r["drug_code"]: r for r in merged}
    assert len(merged) == 2
    assert by_code["1"]["drug_name"] == "METFORMIN 500"   # first file wins
    assert by_code["1"]["price"] == "21000"
    assert by_code["1"]["share"] == "70"                  # empty filled from csv
    assert by_code["2"]["drug_name"] == "OTHER"


def test_merge_row_sets_codeless_deduped_by_name():
    a = [{"drug_name": "HERBAL X"}]
    b = [{"drug_name": "HERBAL X"}, {"drug_name": "HERBAL Y"}]
    names = sorted(r["drug_name"] for r in merge_row_sets([a, b]))
    assert names == ["HERBAL X", "HERBAL Y"]
    # single set passes through untouched
    assert merge_row_sets([a]) == a
    assert merge_row_sets([]) == []


# ── column-role inference: the insurer's own code must not steal the name ────
def test_roles_resolve_for_tamin_style_headers():
    """Regression: the loose "drug" alias let drug_code claim the drug_name role,
    dropping the real name column — every tamin upload scored 0 applied /
    all-unmatched. Also: "covered" wasn't a true-word, so the HOSPITAL column
    was elected the covered flag instead of insurance_status."""
    rows = [{"drug_code": "01211", "drug_name": "METFORMIN 500 mg TABLET ORAL",
             "insurance_status_normalized": "covered",
             "hospital_status_normalized": "False",
             "max_prescription": "30",
             "organization_share_percent_without_subsidy": "70.0",
             "price_without_subsidy": "21000", "accepted_total_price": "18000"}] * 3
    roles = resolve_roles(rows, None)
    assert roles["drug_code"] == "generic_code"          # code → code, not name
    assert roles["drug_name"] == "drug_name"             # the real name survives
    assert roles["insurance_status_normalized"] == "covered"
    assert roles["hospital_status_normalized"] == "inpatient"
    assert roles["max_prescription"] == "ceiling"
    assert roles["accepted_total_price"] == "reference_price"


def test_insurer_mismatch_guard():
    from services.core.drug_catalog.coverage_harvest import detect_insurer_mismatch
    salamat_names = {f"DRUG {i}" for i in range(40)}
    tamin_names = {f"OTHER {i} 100 mg TABLET ORAL" for i in range(40)}
    known = {"salamat": salamat_names, "tamin": tamin_names}
    # a salamat file staged as tamin is caught
    bad = detect_insurer_mismatch(sorted(salamat_names), known, "tamin")
    assert bad and bad["looks_like"] == "salamat" and bad["match_pct"] == 100
    # the same file under its own insurer passes
    assert detect_insurer_mismatch(sorted(salamat_names), known, "salamat") is None
    # a brand-new list (no overlap with anyone) is never blocked
    assert detect_insurer_mismatch([f"NEW ITEM {i}" for i in range(40)], known, "tamin") is None
    # too few rows to judge
    assert detect_insurer_mismatch(sorted(salamat_names)[:5], known, "tamin") is None
