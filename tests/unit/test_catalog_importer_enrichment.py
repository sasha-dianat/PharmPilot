"""build_records must map the enriched NFI parser output (country, owners,
monograph) and the upsert must never wipe existing enrichment/coverage with
NULLs when a source doesn't supply them (Excel imports carry no monograph)."""
from services.core.drug_catalog.importer import build_records, upsert_values

ROW = {
    "irc": "1234567890123456", "name_fa": "ویکتوزا", "generic_name": "liraglutide",
    "dosage_form": "INJECTION", "strength": "6 mg/1mL", "announced_price": "2500000",
    "manufacturer": "Novo Nordisk", "country": "دانمارک",
    "license_owner": "نوو نوردیسک پارس", "brand_owner": "Novo Nordisk",
    "license_valid_until": "1405/04/16",
    "composition": "LIRAGLUTIDE 6 mg/1mL",
    "indications": "دیابت نوع ۲", "mechanism": "آگونیست GLP-1",
    "pharmacokinetics": "نیمه‌عمر ۱۳ ساعت", "warnings": "پانکراتیت",
    "side_effects": "تهوع", "interactions_text": "انسولین", "advice": "تزریق روزانه",
    "brands": [{"name": "ویکتوزا", "trade_owner": "Novo Nordisk", "country": "دانمارک",
                "country_code": "DK", "licensee": "x", "status": None, "nfi_id": 17248}],
}


def test_build_records_maps_enrichment_fields():
    rec = build_records([ROW])[0]
    assert rec.country == "دانمارک"
    assert rec.license_owner == "نوو نوردیسک پارس"
    assert rec.brand_owner == "Novo Nordisk"
    assert rec.license_valid_until == "1405/04/16"
    mono = rec.monograph
    assert mono["composition"] == "LIRAGLUTIDE 6 mg/1mL"
    assert mono["pharmacokinetics"] == "نیمه‌عمر ۱۳ ساعت"
    assert mono["brands"][0]["country_code"] == "DK"
    for k in ("indications", "mechanism", "warnings", "side_effects",
              "interactions_text", "advice"):
        assert mono[k]


def test_build_records_without_enrichment_gives_none_monograph():
    bare = {"irc": "111", "name_fa": "x", "generic_name": "y"}
    rec = build_records([bare])[0]
    assert rec.monograph is None and rec.country is None


def test_upsert_values_excludes_sticky_none_fields_from_update():
    rec = build_records([{"irc": "111", "name_fa": "x", "generic_name": "y"}])[0]
    values, update_cols = upsert_values(rec, source="excel-import")
    # a source that carries no coverage/monograph/country must not NULL them out
    for sticky in ("coverage", "monograph", "country", "license_owner",
                   "brand_owner", "license_valid_until"):
        assert sticky not in update_cols
    assert "name_fa" in update_cols and "irc" not in update_cols


def test_upsert_values_includes_sticky_fields_when_present():
    rec = build_records([ROW])[0]
    values, update_cols = upsert_values(rec, source="nfi-harvest")
    assert update_cols["country"] == "دانمارک"
    assert update_cols["monograph"]["composition"]
