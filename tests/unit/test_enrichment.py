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
