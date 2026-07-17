"""هوش‌یار دارو — spelling-proof keys, suggestion validation, reference plumbing."""
import asyncio
import os

import pytest

from services.core.drug_catalog.enrichment import enrich_key, validate_suggestion


def test_enrich_key_folds_spelling_variants():
    # Arabic yeh/kaf, ZWNJ, case, salt words must all collapse to one key
    a = enrich_key("ویتامین آ-تداژل")
    b = enrich_key("ويتامين آ‌-تداژل")          # Arabic yeh + ZWNJ variant
    assert a == b and a
    assert enrich_key("Metformin HCL") == enrich_key("metformin hydrochloride")


def test_enrich_key_empty_is_empty():
    assert enrich_key("") == "" and enrich_key(None) == ""


def test_validate_suggestion_cleans_and_flags():
    d = {"generic": "vitamin a", "brand": "A-Tedagel", "manufacturer": "Tehran Daru",
         "country": "Iran", "dosage_form": "SOFTGEL",
         "strengths": ["25000 IU", "50000 IU"], "confidence": 0.9,
         "sources": ["https://tehrandarou.com/x"], "junk_field": 1}
    clean, errors = validate_suggestion(d)
    assert errors == []
    assert clean["dosage_form"] == "SOFTGEL" and len(clean["strengths"]) == 2
    assert "junk_field" not in clean


def test_validate_suggestion_preserves_form_list_for_fanout():
    # A multi-form product (ANGIPARS capsule+ointment) keeps the LIST so the
    # expander can fan it out — never stringified into "['capsule', 'ointment']".
    clean, errors = validate_suggestion(
        {"generic": "melilotus officinalis", "dosage_form": ["capsule", "ointment"]})
    assert errors == []
    assert clean["dosage_form"] == ["capsule", "ointment"]


# ── variant fan-out (هر توان/شکل/برند = یک مدخل) ─────────────────────────────
def test_expand_variants_one_form_many_strengths():
    from services.core.drug_catalog.enrichment import expand_variants
    vs = expand_variants({"generic": "tolmetin sodium", "brand": "Tolectin",
                          "dosage_form": "capsule", "strengths": ["400 mg", "600 mg"]})
    assert [(v["dosage_form"], v["strength"]) for v in vs] == \
        [("capsule", "400 mg"), ("capsule", "600 mg")]
    assert all(v["brand_name"] == "Tolectin" for v in vs)


def test_expand_variants_many_forms_no_cross_attribution():
    from services.core.drug_catalog.enrichment import expand_variants
    vs = expand_variants({"generic": "salbutamol",
                          "dosage_form": ["inhalation spray", "syrup", "tablet"],
                          "strengths": ["100 mcg/dose", "2 mg/5 mL", "4 mg"]})
    # one variant per form; strengths NOT cross-attributed (that would fabricate products)
    assert [v["dosage_form"] for v in vs] == ["inhalation spray", "syrup", "tablet"]
    assert all(v["strength"] is None for v in vs)


def test_expand_variants_model_provided_passthrough():
    from services.core.drug_catalog.enrichment import expand_variants
    given = [{"dosage_form": "syrup", "strength": "2 mg/5 mL"},
             {"dosage_form": "tablet", "strength": "4 mg"}]
    vs = expand_variants({"variants": given, "dosage_form": ["x"], "strengths": ["9"]})
    assert [(v["dosage_form"], v["strength"]) for v in vs] == \
        [("syrup", "2 mg/5 mL"), ("tablet", "4 mg")]


def test_expand_variants_pack_sizes_fan_out_single_form():
    from services.core.drug_catalog.enrichment import expand_variants
    vs = expand_variants({"generic": "metronidazole", "dosage_form": "topical gel",
                          "strengths": ["0.75 %"], "pack_size": ["30 g", "70 g"]})
    assert [(v["dosage_form"], v["strength"], v["pack_size"]) for v in vs] == \
        [("topical gel", "0.75 %", "30 g"), ("topical gel", "0.75 %", "70 g")]


def test_expand_variants_packs_not_crossed_over_multiple_forms():
    from services.core.drug_catalog.enrichment import expand_variants
    vs = expand_variants({"generic": "metronidazole",
                          "dosage_form": ["topical gel", "vaginal gel"],
                          "pack_size": ["30 g", "70 g"]})
    # multi-form pack attribution must come from the model's variants[] —
    # never cartesian-fabricated
    assert [v["dosage_form"] for v in vs] == ["topical gel", "vaginal gel"]
    assert all(v["pack_size"] is None for v in vs)


def test_variant_passthrough_keeps_pack_size():
    from services.core.drug_catalog.enrichment import expand_variants
    given = [{"dosage_form": "topical gel", "strength": "0.75 %", "pack_size": "30 g"},
             {"dosage_form": "vaginal gel", "strength": "0.75 %", "pack_size": "70 g"}]
    vs = expand_variants({"variants": given})
    assert [v["pack_size"] for v in vs] == ["30 g", "70 g"]


def test_linker_pack_size_disambiguates_same_form():
    # Two vaginal-gel variants differing only in pack: «…70 g GEL» → the 70 g one.
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord
    catalog = [
        CatalogRecord(irc="G30", name_fa="ژ ت", generic_name="metronidazole",
                      dosage_form="GEL", strength="0.75 % 30 g",
                      announced_price=Decimal("1")),
        CatalogRecord(irc="G70", name_fa="ژ و", generic_name="metronidazole",
                      dosage_form="GEL", strength="0.75 % 70 g",
                      announced_price=Decimal("1")),
    ]
    enr = {enrich_key("METRONIDAZOLE GELX"): {
        "generic_name": "metronidazole", "dosage_form": None, "strengths": None,
        "irc": None, "variants": [
            {"dosage_form": "gel", "strength": "0.75 %", "pack_size": "30 g"},
            {"dosage_form": "gel", "strength": "0.75 %", "pack_size": "70 g"},
        ]}}
    link = link_rows([{"drug_name": "METRONIDAZOLE GELX 70 g"}], catalog,
                     enrichments=enr)[0]
    assert link.matched and link.record.irc == "G70"


def test_variant_passthrough_keeps_injectable_detail():
    # Carboplatin: bare "injection" is ambiguous — route, concentration (per mL),
    # total strength, pack volume, and container are each identity dimensions.
    from services.core.drug_catalog.enrichment import expand_variants, validate_suggestion
    clean, errors = validate_suggestion({"generic": "carboplatin", "variants": [
        {"dosage_form": "injection, solution, concentrate", "route": "intravenous",
         "strength": "150 mg", "concentration": "10 mg/mL",
         "pack_size": "15 mL", "container": "vial"}]})
    assert errors == []
    v = expand_variants(clean)[0]
    assert (v["route"], v["concentration"], v["container"]) == \
        ("intravenous", "10 mg/mL", "vial")
    assert (v["strength"], v["pack_size"]) == ("150 mg", "15 mL")


def test_linker_uses_concentration_numbers_too():
    # Row text carries 10 mg/1mL; the variant's concentration must feed matching
    # even when its total strength (150 mg) is what the catalog row shows.
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord
    catalog = [CatalogRecord(irc="C1", name_fa="ک پ", generic_name="carboplatin",
                             dosage_form="INJECTION", strength="150 mg/15 mL",
                             announced_price=Decimal("1"))]
    enr = {enrich_key("CARBOPLATINX INJ"): {
        "generic_name": "carboplatin", "dosage_form": None, "strengths": None,
        "irc": None, "variants": [
            {"dosage_form": "injection", "route": "intravenous",
             "strength": "150 mg", "concentration": "10 mg/mL",
             "pack_size": "15 mL", "container": "vial"}]}}
    link = link_rows([{"drug_name": "CARBOPLATINX INJ 10 mg/1mL"}], catalog,
                     enrichments=enr)[0]
    assert link.matched and link.record.irc == "C1"


def test_infer_item_kind_bottle_is_supply():
    from services.core.drug_catalog.enrichment import infer_item_kind
    assert infer_item_kind("BOTTLE 240 CC", {}) == "supply"
    assert infer_item_kind("بطری 120 میلی لیتر", {}) == "supply"
    assert infer_item_kind("SALBUTAMOL", {"generic": "salbutamol"}) == "drug"
    # model says supply but a generic exists → regex guard doesn't fire; model kind kept
    assert infer_item_kind("weird item", {"item_kind": "supply"}) == "supply"


def test_linker_variant_picks_matching_form_strength():
    # «سالبوتامول شربت» must use the SYRUP variant's strength, not the tablet's.
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord
    catalog = [
        CatalogRecord(irc="S1", name_fa="محلول خوراکی س", generic_name="salbutamol",
                      dosage_form="SYRUP", strength="2 mg/5 mL",
                      announced_price=Decimal("1")),
        CatalogRecord(irc="T1", name_fa="قرص س", generic_name="salbutamol",
                      dosage_form="TABLET", strength="4 mg",
                      announced_price=Decimal("1")),
    ]
    enr = {enrich_key("SALBUTAMOL SYRUP X"): {
        "generic_name": "salbutamol", "dosage_form": None, "strengths": None,
        "irc": None, "variants": [
            {"dosage_form": "syrup", "strength": "2 mg/5 mL"},
            {"dosage_form": "tablet", "strength": "4 mg"},
        ]}}
    link = link_rows([{"drug_name": "SALBUTAMOL SYRUP X"}], catalog, enrichments=enr)[0]
    assert link.matched and link.record.irc == "S1"


def test_validate_suggestion_rejects_bad_shapes():
    clean, errors = validate_suggestion({"confidence": "high", "sources": "not-a-list"})
    assert errors                                   # confidence not float, sources not list
    clean2, errors2 = validate_suggestion({"generic": "x", "confidence": 1.5, "sources": []})
    assert any("confidence" in e for e in errors2)  # out of [0,1]


def _db_or_skip():
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async def ping():
            async with eng.connect() as c:
                await c.close()
            await eng.dispose()
        asyncio.get_event_loop().run_until_complete(ping())
    except Exception:
        pytest.skip("dev DB unreachable")
    return url


def test_reference_roundtrip_and_load(tmp_path):
    url = _db_or_skip()
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.enrichment import DrugEnrichment
    from services.core.drug_catalog.enrichment import (
        enrich_key, load_approved, export_reference, import_reference)

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        key = enrich_key("__test_vitamin_a_tedagel__")
        async with S() as db:
            db.add(DrugEnrichment(key=key, raw_name="__test_vitamin_a_tedagel__",
                                  generic_name="vitamin a", brand_name="A-Tedagel",
                                  manufacturer="Tehran Daru", country="Iran",
                                  dosage_form="SOFTGEL", strengths=["25000 IU"],
                                  sources=["https://x"], researched_by="manual",
                                  confidence=0.9, status="approved"))
            await db.commit()
            ref = await load_approved(db)
            assert key in ref and ref[key]["manufacturer"] == "Tehran Daru"
            p = tmp_path / "ref.json"
            n = await export_reference(db, p)
            assert n >= 1 and p.exists()
            # delete, re-import, still approved
            obj = (await db.execute(
                select(DrugEnrichment).where(DrugEnrichment.key == key))).scalar_one()
            await db.delete(obj); await db.commit()
            m = await import_reference(db, p)
            assert m >= 1
            ref2 = await load_approved(db)
            assert key in ref2
            # cleanup
            row = (await db.execute(select(DrugEnrichment)
                    .where(DrugEnrichment.key == key))).scalar_one()
            await db.delete(row); await db.commit()
        await eng.dispose()
    asyncio.get_event_loop().run_until_complete(run())


# ── E3: application seams (the "forever" wiring) ──────────────────────────────
def test_linker_uses_enrichment_to_match_ambiguous_row():
    # «ویتامین آ-تداژل» carries no form/strength/generic — unmatchable today.
    # With an approved enrichment the row augments and links to the catalog softgel.
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord

    # catalog Persian name deliberately dissimilar so the ONLY route to a match
    # is the latin generic the enrichment supplies (not a fuzzy Persian-name hit)
    catalog = [CatalogRecord(irc="T1", name_fa="رتینول کپسول", generic_name="vitamin a",
                             dosage_form="SOFTGEL", strength="25000 IU",
                             announced_price=Decimal("50000"))]
    row = {"drug_name": "ویتامین آ-تداژل"}
    assert not link_rows([row], catalog)[0].matched          # baseline: no match
    enr = {enrich_key("ویتامین آ-تداژل"): {
        "generic_name": "vitamin a", "dosage_form": "SOFTGEL",
        "strengths": ["25000 IU", "50000 IU"], "brand_name": "A-Tedagel",
        "manufacturer": "Tehran Daru", "country": "Iran", "irc": None}}
    link = link_rows([row], catalog, enrichments=enr)[0]
    assert link.matched and link.record.irc == "T1"


def test_linker_enrichment_irc_pin_wins():
    from decimal import Decimal
    from services.core.drug_catalog.coverage_import import link_rows
    from services.core.drug_catalog.enrichment import enrich_key
    from services.core.drug_catalog.schema import CatalogRecord
    catalog = [CatalogRecord(irc="P9", name_fa="یک نام کاملا متفاوت", generic_name="foobarium",
                             dosage_form="TABLET", strength="10 mg", announced_price=Decimal("1"))]
    enr = {enrich_key("SOME BRAND XYZ"): {"irc": "P9", "generic_name": None,
           "dosage_form": None, "strengths": None}}
    link = link_rows([{"drug_name": "SOME BRAND XYZ"}], catalog, enrichments=enr)[0]
    assert link.matched and link.record.irc == "P9" and link.method == "enrichment"


def test_ingest_gapfill_never_overwrites_nfi_values():
    from services.core.drug_catalog.importer import build_records, apply_enrichment_gaps
    from services.core.drug_catalog.enrichment import enrich_key
    rec = build_records([{"irc": "1", "name_fa": "ویتامین آ-تداژل",
                          "generic_name": "vitamin a", "country": "ایران"}])[0]
    enr = {enrich_key("ویتامین آ-تداژل"): {
        "country": "France", "manufacturer": "Tehran Daru", "dosage_form": "SOFTGEL"}}
    filled = apply_enrichment_gaps(rec, enr)
    assert filled.country == "ایران"                 # NFI value KEPT (never overwritten)
    assert filled.manufacturer == "Tehran Daru"      # gap filled
    assert filled.dosage_form == "SOFTGEL"           # gap filled
