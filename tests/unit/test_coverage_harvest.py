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


def test_harvest_state_snapshot_has_lock_holder():
    harvest_lock.force_release()
    st = CoverageHarvestState(running=True, insurer="tamin", phase="fetching")
    snap = st.snapshot()
    assert snap["insurer"] == "tamin" and "lock_holder" in snap


def test_read_table_strips_nul_bytes(tmp_path):
    # legacy .xls exports pad strings with NULs; Postgres JSONB rejects \x00
    p = tmp_path / "nul.csv"
    p.write_bytes("drug_name,share\nACETAMIN\x00OPHEN,70\x00\n".encode("utf-8"))
    from services.core.drug_catalog.excel_import import read_table
    rows = read_table(p)
    assert rows and "\x00" not in rows[0]["drug_name"]
    assert all("\x00" not in v for r in rows for v in r.values())
