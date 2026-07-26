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


def test_recrawl_never_erases_a_price_or_its_provenance():
    """The 2026-07-25 re-crawl wiped 728 prices and 1,843 provenance stamps: an
    NFI page that omits a price wrote NULL over a good value, and monograph is
    rebuilt per crawl so price_provenance was discarded. A re-import may FILL,
    never ERASE — but a crawl that DOES supply a price legitimately replaces the
    insurer-derived value and drops the now-obsolete stamp."""
    import asyncio, os
    from decimal import Decimal
    import pytest
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
    eng = create_async_engine(url)
    try:
        async def ping():
            async with eng.connect():
                pass
        asyncio.get_event_loop().run_until_complete(ping())
    except Exception:
        pytest.skip("dev DB unreachable")

    from sqlalchemy import delete, select
    from shared.models.drug_catalog import DrugCatalogItem
    from services.core.drug_catalog.importer import upsert_catalog
    from services.core.drug_catalog.schema import CatalogRecord

    KEEP, ZERO, REPL = "__RC_KEEP__", "__RC_ZERO__", "__RC_REPL__"
    PROV = {"announced": "insurer-derived", "source": "insurer-refresh:tamin",
            "kind": "gap_fill", "previous": None, "at": "x", "note": "n"}

    def crawled(irc, price):
        return CatalogRecord(irc=irc, name_fa="د", generic_name="g",
                             dosage_form="TABLET", strength="1 mg",
                             announced_price=price,
                             monograph={"indications": "fresh from the crawl"})

    async def run():
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([KEEP, ZERO, REPL])))
            for irc in (KEEP, ZERO, REPL):
                db.add(DrugCatalogItem(irc=irc, name_fa="د", generic_name="g",
                    ingredient_key="g|1 mg|tablet", dosage_form="TABLET",
                    strength="1 mg", announced_price=Decimal("50000"),
                    source="insurer", monograph={"price_provenance": PROV}))
            await db.commit()

            # KEEP:  crawl has NO price          → must not erase, stamp survives
            # ZERO:  crawl reports قیمت 0          → also "no price", must not erase
            # REPL:  crawl HAS a real price        → replaces it, stamp is dropped
            await upsert_catalog(db, [crawled(KEEP, None), crawled(ZERO, Decimal("0")),
                                      crawled(REPL, Decimal("77000"))],
                                 source="nfi-harvest")
            rows = {r.irc: r for r in (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([KEEP, ZERO, REPL])))).scalars().all()}

            assert int(rows[KEEP].announced_price) == 50000, "price was erased"
            assert rows[KEEP].monograph["price_provenance"]["kind"] == "gap_fill"
            assert rows[KEEP].monograph["indications"] == "fresh from the crawl"

            assert int(rows[ZERO].announced_price) == 50000, "قیمت 0 must not erase"
            assert rows[ZERO].monograph["price_provenance"]["kind"] == "gap_fill"
            assert int(rows[REPL].announced_price) == 77000, "real NFI price must win"
            assert "price_provenance" not in (rows[REPL].monograph or {}), \
                "an NFI-verified price is no longer insurer-derived"

            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([KEEP, ZERO, REPL])))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())
