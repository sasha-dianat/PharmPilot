"""Price-refresh: run_sync divergence filter + high-confidence proposal gating."""
from decimal import Decimal

from services.core.drug_catalog.pricing_sync import compute_proposals
from services.core.drug_catalog.schema import CatalogRecord


def _rec(irc, ann):
    return CatalogRecord(irc=irc, name_fa=f"د{irc}", generic_name="x",
                         dosage_form="TABLET", strength="1 mg",
                         announced_price=Decimal(ann))


def test_compute_proposals_flags_divergence_direction():
    cat = [_rec("A", "10000"), _rec("B", "100000")]
    incoming = [{"irc": "A", "announced_price": "50000"},   # 5x up (stale catalog)
                {"irc": "B", "announced_price": "100000"}]  # unchanged → no proposal
    props = compute_proposals(cat, incoming)
    assert len(props) == 1 and props[0].irc == "A"
    assert props[0].kind == "increase" and props[0].pct_change == 400.0


def test_min_pct_noise_floor():
    from services.core.drug_catalog import pricing_sync
    cat = [_rec("A", "10000"), _rec("B", "100000")]
    incoming = [{"irc": "A", "announced_price": "50000"},    # +400%
                {"irc": "B", "announced_price": "105000"}]   # +5% (noise)
    props = compute_proposals(cat, incoming)
    kept = [p for p in props if abs(float(p.pct_change)) >= 25.0]  # what run_sync(min_pct) does
    assert {p.irc for p in kept} == {"A"}                    # +5% dropped, +400% kept


def test_insurer_refresh_only_proposes_upward_by_default():
    """The insurer figure is «قیمت مورد تعهد» — the amount the organization
    ACCEPTS, capped below retail for most products. Measured on the real data it
    is LOWER than the catalog price in 8,382 rows and higher in 12,121; only the
    second group means the NFI price is stale. Proposing the first would pull
    قیمت مصرف‌کننده down to a reimbursement cap, so the refresh is upward-only
    unless the caller explicitly opts out."""
    import asyncio, os, uuid
    from decimal import Decimal
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    except Exception:
        import pytest; pytest.skip("sqlalchemy async unavailable")
    import pytest
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
    from shared.models.coverage import CoverageRun, CoverageSource
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.drug_price_proposal import DrugPriceProposal
    from services.core.drug_catalog import sync_service

    STALE, CAPPED = "__PR_STALE__", "__PR_CAPPED__"

    async def run():
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(DrugPriceProposal).where(
                DrugPriceProposal.irc.in_([STALE, CAPPED])))
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([STALE, CAPPED])))
            src = CoverageSource(insurer="prtest", name="pr", strategy="manual",
                                 enabled=False, settings={})
            db.add(src)
            await db.commit(); await db.refresh(src)
            # STALE: catalog 1,000 vs insurer 40,000 → the real refresh case
            # CAPPED: catalog 500,000 vs insurer 200,000 → a reimbursement cap
            db.add(DrugCatalogItem(irc=STALE, name_fa="کهنه", generic_name="a",
                ingredient_key="a||", dosage_form="TABLET", strength="1 mg",
                announced_price=Decimal("1000"), source="prtest"))
            db.add(DrugCatalogItem(irc=CAPPED, name_fa="سقف", generic_name="b",
                ingredient_key="b||", dosage_form="TABLET", strength="1 mg",
                announced_price=Decimal("500000"), source="prtest"))
            run_row = CoverageRun(source_id=src.id, insurer="prtest", status="parsed",
                staged={STALE: {"prtest": {"reference_price": 40000,
                                           "match_confidence": 0.95, "match_method": "structural"}},
                        CAPPED: {"prtest": {"reference_price": 200000,
                                            "match_confidence": 0.95, "match_method": "structural"}}})
            db.add(run_row)
            await db.commit(); await db.refresh(run_row)

            res = await sync_service.propose_prices_from_run(db, run_row.id)
            assert res["qualified"] == 1, res            # only the stale one
            assert res["skipped_decreases"] == 1, res    # the cap was refused
            rows = (await db.execute(select(DrugPriceProposal).where(
                DrugPriceProposal.irc.in_([STALE, CAPPED])))).scalars().all()
            assert {r.irc for r in rows} == {STALE}
            assert int(rows[0].proposed_announced) == 40000
            assert int(rows[0].current_announced) == 1000

            # opting out lets both directions through, for a true consumer-price feed
            res2 = await sync_service.propose_prices_from_run(
                db, run_row.id, only_increases=False)
            assert res2["qualified"] == 2 and res2["skipped_decreases"] == 0

            await db.execute(delete(DrugPriceProposal).where(
                DrugPriceProposal.irc.in_([STALE, CAPPED])))
            await db.execute(delete(CoverageRun).where(CoverageRun.id == run_row.id))
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([STALE, CAPPED])))
            await db.execute(delete(CoverageSource).where(CoverageSource.id == src.id))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())


def test_insurer_derived_price_is_stamped_with_provenance():
    """An insurer figure is an ACCEPTANCE amount, not an NFI-verified consumer
    price. Applying one must record where it came from and whether it FILLED an
    empty price or refreshed a stale one — in monograph JSONB, deliberately NOT
    as a field_override, which would make it permanent policy and stop the next
    NFI crawl from replacing it with a real price."""
    import asyncio, os, uuid
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
    from shared.models.drug_price_proposal import DrugPriceProposal
    from services.core.drug_catalog import sync_service

    FILL, REFR = "__PV_FILL__", "__PV_REFRESH__"

    async def run():
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            await db.execute(delete(DrugPriceProposal).where(
                DrugPriceProposal.irc.in_([FILL, REFR])))
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([FILL, REFR])))
            db.add(DrugCatalogItem(irc=FILL, name_fa="بی‌قیمت", generic_name="x",
                ingredient_key="x||", dosage_form="VIAL", strength="1 mg",
                announced_price=None, source="t"))
            db.add(DrugCatalogItem(irc=REFR, name_fa="کهنه", generic_name="y",
                ingredient_key="y||", dosage_form="TABLET", strength="1 mg",
                announced_price=Decimal("1000"), source="t"))
            for irc, cur, prop in ((FILL, None, 5_000_000), (REFR, 1000, 40000)):
                db.add(DrugPriceProposal(irc=irc, name_fa="n", kind="increase",
                    status="pending", source="insurer-refresh:tamin",
                    current_announced=cur, proposed_announced=prop,
                    current_effective=cur or 0, proposed_effective=prop,
                    delta=prop - (cur or 0), pct_change=100.0))
            await db.commit()
            ids = (await db.execute(select(DrugPriceProposal.id).where(
                DrugPriceProposal.irc.in_([FILL, REFR])))).scalars().all()
            await sync_service.decide_proposals(db, list(ids), approve=True,
                                                staff_id=uuid.uuid4())
            rows = {r.irc: r for r in (await db.execute(select(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([FILL, REFR])))).scalars().all()}
            fill = rows[FILL].monograph["price_provenance"]
            refr = rows[REFR].monograph["price_provenance"]
            assert fill["announced"] == "insurer-derived" and fill["kind"] == "gap_fill"
            assert fill["previous"] is None and fill["source"] == "insurer-refresh:tamin"
            assert refr["kind"] == "refresh" and refr["previous"] == 1000
            assert int(rows[FILL].announced_price) == 5_000_000
            assert int(rows[REFR].announced_price) == 40000

            await db.execute(delete(DrugPriceProposal).where(
                DrugPriceProposal.irc.in_([FILL, REFR])))
            await db.execute(delete(DrugCatalogItem).where(
                DrugCatalogItem.irc.in_([FILL, REFR])))
            await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())
