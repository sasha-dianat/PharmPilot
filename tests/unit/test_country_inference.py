"""Country proposals from manufacturer nationality — evidence, not a guess."""
import asyncio
import os

import pytest

from services.core.drug_catalog import country_inference as ci


def _db_or_skip():
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
    try:
        from sqlalchemy.ext.asyncio import create_async_engine
        eng = create_async_engine(url)
        async def ping():
            async with eng.connect():
                pass
            await eng.dispose()
        asyncio.get_event_loop().run_until_complete(ping())
    except Exception:
        pytest.skip("dev DB unreachable")
    return url


def test_importers_excluded_evidence_attached_and_real_country_never_overwritten():
    """The rule must not become "Persian name ⇒ ایران": Iranian factories also
    import (دارو پخش is 926 Iranian / 23 foreign), and some Persian-named firms
    only import. A firm that never ships Iranian product is excluded outright;
    everyone else gets a confidence and the split as visible evidence."""
    url = _db_or_skip()
    from decimal import Decimal
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.drug_catalog import DrugCatalogItem

    DOM, IMP, MIX = "کارخانهٔ آزمون", "واردکنندهٔ آزمون", "کارخانهٔ مختلط آزمون"
    SRC = "ci-itest"

    def item(irc, firm, country):
        return DrugCatalogItem(irc=irc, name_fa="ق", generic_name="g",
            ingredient_key=f"g|{irc}|t", dosage_form="TABLET", strength="1 mg",
            manufacturer=firm, country=country, announced_price=Decimal("1"),
            source=SRC)

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(DrugCatalogItem).where(DrugCatalogItem.source == SRC))
            # evidence rows
            for i in range(4):
                db.add(item(f"__CI_D{i}__", DOM, "ایران"))
            for i in range(4):
                db.add(item(f"__CI_I{i}__", IMP, "هند"))
            db.add(item("__CI_M0__", MIX, "ایران"))
            db.add(item("__CI_M1__", MIX, "ایران"))
            db.add(item("__CI_M2__", MIX, "ایران"))
            db.add(item("__CI_M3__", MIX, "هند"))
            # country-less targets, one per firm
            for irc, firm in (("__CI_TD__", DOM), ("__CI_TI__", IMP), ("__CI_TM__", MIX)):
                db.add(item(irc, firm, None))
            await db.commit()

            res = await ci.propose(db)
            mine = {p["irc"]: p for p in res["proposals"] if p["irc"].startswith("__CI_T")}

            assert "__CI_TI__" not in mine, "an importer-only firm must never propose ایران"
            d = mine["__CI_TD__"]
            assert d["proposed_country"] == "ایران" and d["confidence"] == 1.0
            assert "4 ایرانی / 0 خارجی" in d["evidence"]
            m = mine["__CI_TM__"]
            assert m["confidence"] == 0.75, "a mixed firm keeps its true confidence"
            assert "3 ایرانی / 1 خارجی" in m["evidence"]

            # a confidence floor filters the mixed firm out
            hi = await ci.propose(db, min_confidence=0.9)
            assert "__CI_TM__" not in {p["irc"] for p in hi["proposals"]}
            assert "__CI_TD__" in {p["irc"] for p in hi["proposals"]}

            # apply writes country + provenance, and never touches a real value
            out = await ci.apply(db, ["__CI_TD__", "__CI_D0__"])
            assert out["applied"] == 1, "a row that already has a country is skipped"
            row = (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc == "__CI_TD__"))).scalar_one()
            assert row.country == "ایران"
            prov = row.monograph["country_provenance"]
            assert prov["country"] == "inferred" and prov["manufacturer"] == DOM
            assert prov["confidence"] == 1.0

            await db.execute(delete(DrugCatalogItem).where(DrugCatalogItem.source == SRC))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())
