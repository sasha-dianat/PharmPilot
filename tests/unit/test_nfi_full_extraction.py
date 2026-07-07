"""NFI detail-page parser must extract EVERYTHING the page offers — including
the brands/similar-products table, where کشور (country) is a flag <img> whose
title attribute carries the country name and whose gif filename carries the
ISO code (e.g. CountriesFlag/DK.gif title="دانمارک")."""
from pathlib import Path

from services.core.drug_catalog.nfi import parse_detail

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "nfi" / "nfi_detail_17248.html"


def _parsed():
    return parse_detail(FIXTURE.read_text(encoding="utf-8"), page_id=17248)


def test_existing_fields_still_extracted():
    out = _parsed()
    assert out["irc"]
    assert out["brand_name"].upper().startswith("VICTOZA")
    assert out["generic_name"] == "liraglutide"
    assert out["nfi_id"] == 17248


def test_new_scalar_fields():
    out = _parsed()
    assert out["brand_owner"]                       # صاحب برند — own key now
    assert out["license_owner"]                     # صاحب پروانه
    assert out["license_valid_until"]               # تاریخ اعتبار پروانه (Jalali string)
    assert "LIRAGLUTIDE" in out["composition"].upper()   # raw ترکیبات text kept
    # NOTE: فارماکوکینتیک is wired (see test_pharmacokinetics_label_is_mapped),
    # but this Victoza page legitimately has an empty PK section, so no assert here.


def test_pharmacokinetics_label_is_mapped():
    from services.core.drug_catalog.nfi import _PAIR_LABELS
    assert _PAIR_LABELS.get("فارماکوکینتیک") == "pharmacokinetics"


def test_brands_table_extracted_with_country():
    out = _parsed()
    brands = out["brands"]
    assert isinstance(brands, list) and len(brands) >= 2   # page says 18 similar
    first = brands[0]
    assert set(first) == {"name", "trade_owner", "country", "country_code",
                          "licensee", "status", "nfi_id"}
    assert any(b["country"] == "دانمارک" for b in brands)
    assert any(b["country_code"] == "DK" for b in brands)


def test_top_level_country_comes_from_own_row():
    out = _parsed()
    # the row linking to /NFI/Detail/17248 is this product's own registration
    assert out["country"] == "دانمارک"


def test_country_falls_back_to_first_row_without_page_id():
    html = FIXTURE.read_text(encoding="utf-8")
    out = parse_detail(html, page_id=None)
    assert out["country"]                           # still populated
