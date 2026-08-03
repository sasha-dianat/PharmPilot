"""X4–X5: snapshot capture and the canonical bundle round-trip (test DB)."""
import asyncio
import json
import os
import uuid

import pytest


def _db_or_skip():
    """A live-database test, pointed at the TEST database and never the real one.

    DATABASE_URL is honoured so CI can redirect these — but DATABASE_URL is also
    what `.env` sets, to the PRODUCTION database. Reading it unguarded sent this
    file's fixtures into the live decided layer on 2026-08-03: three formulary
    snapshots for insurers `regtest_a`/`regtest_b` and a crosswalk row pointing
    at IRC `__REGT__`, which fired the `crosswalk_dangling_irc` critical until
    it was found and archived a day later. A test that writes must name the
    database it is willing to write to.
    """
    url = os.environ.get("DATABASE_URL",
        "postgresql+asyncpg://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot_test")
    dbname = url.rsplit("/", 1)[-1].split("?")[0]
    if not dbname.endswith("_test"):
        pytest.skip(f"refusing to write fixtures to non-test database {dbname!r} — "
                    "point DATABASE_URL at pharmpilot_test to run this")
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


def test_snapshot_capture_and_bundle_roundtrip(tmp_path):
    url = _db_or_skip()
    from sqlalchemy import delete, select
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.crosswalk import CrosswalkEntry, FieldOverride
    from shared.models.formulary_snapshot import FormularySnapshot
    from services.core.drug_catalog.canonical_export import export_bundle, import_decided
    from services.core.drug_catalog.coverage_harvest import save_snapshots
    from services.core.drug_catalog.crosswalk import record_decision, set_override

    run_id = uuid.uuid4()

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)

        async def scrub(db):
            """Remove this test's markers. Called before AND in a finally after,
            so a failed assertion cannot leave fixtures in the database."""
            await db.execute(delete(FormularySnapshot).where(
                FormularySnapshot.insurer == "testins"))
            await db.execute(delete(CrosswalkEntry).where(
                CrosswalkEntry.insurer == "testins"))
            await db.execute(delete(FieldOverride).where(
                FieldOverride.irc == "__XTEST__"))
            await db.commit()

        async with S() as db:
          await scrub(db)
          try:
            # X4: snapshots keep code, price, share, covered + the raw row
            n = await save_snapshots(db, run_id, "testins", [
                {"drug_name": "METFORMIN 500", "drug_code": "01211",
                 "reference_price": "21,000", "share_pct": "70", "covered": "1"},
                {"drug_name": "GIBBERISH ITEM", "covered": ""},
                {"no_name": True},                       # skipped
            ])
            await db.commit()
            assert n == 2
            snap = (await db.execute(select(FormularySnapshot).where(
                FormularySnapshot.insurer == "testins",
                FormularySnapshot.source_code == "01211"))).scalar_one()
            assert snap.reference_price == 21000 and float(snap.share_pct) == 70.0
            assert snap.covered is True and snap.row["drug_name"] == "METFORMIN 500"

            # decided layer to export
            await record_decision(db, insurer="testins", raw_name="METFORMIN 500",
                                  irc="__XTEST__", status="confirmed",
                                  source_code="01211")
            await set_override(db, "__XTEST__", "country", "ایران", reason="t")
            await db.commit()

            # X5 export: formulary resolves through the crosswalk
            manifest = await export_bundle(db, out_dir=tmp_path)
            names = {f["file"] for f in manifest["files"]}
            assert {"catalog.json", "crosswalk.json", "overrides.json",
                    "prices_current.json", "formulary_testins.json"} <= names
            form = json.loads((tmp_path / "formulary_testins.json").read_text())
            row = next(r for r in form["rows"] if r["source_code"] == "01211")
            assert row["product_irc"] == "__XTEST__" and row["decision"] == "confirmed"
            assert row["reference_price"] == 21000
            assert manifest["counts"]["formulary_testins_resolved"] == 1

            # round-trip: wipe decided layer, re-import from the bundle
            await db.execute(delete(CrosswalkEntry).where(CrosswalkEntry.insurer == "testins"))
            await db.execute(delete(FieldOverride).where(FieldOverride.irc == "__XTEST__"))
            await db.commit()
            res = await import_decided(db, in_dir=tmp_path)
            assert res["crosswalk"] >= 1 and res["overrides"] >= 1
            back = (await db.execute(select(CrosswalkEntry).where(
                CrosswalkEntry.insurer == "testins"))).scalar_one()
            assert back.irc == "__XTEST__" and back.status == "confirmed"
            assert back.source_code == "01211"
          finally:
            await scrub(db)
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())


def test_code_registry_richest_name_and_confirmed_irc_only():
    """The national-code registry keeps the LONGEST observed name per code
    (across insurers) and attaches IRCs only from confirmed decisions."""
    url = _db_or_skip()
    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from shared.models.crosswalk import CrosswalkEntry
    from shared.models.formulary_snapshot import FormularySnapshot
    from services.core.drug_catalog.coverage_harvest import save_snapshots
    from services.core.drug_catalog.crosswalk import build_code_registry, record_decision

    run_id = uuid.uuid4()

    async def run():
        eng = create_async_engine(url)
        S = async_sessionmaker(eng, expire_on_commit=False)
        async with S() as db:
            for ins in ("regtest_a", "regtest_b"):
                await db.execute(delete(FormularySnapshot).where(
                    FormularySnapshot.insurer == ins))
                await db.execute(delete(CrosswalkEntry).where(
                    CrosswalkEntry.insurer == ins))
            await db.commit()

            # regtest_a truncates (salamat-style); regtest_b publishes rich names
            await save_snapshots(db, run_id, "regtest_a", [
                {"drug_name": "CICLOSPORIN", "generic_code": "90287"},
            ])
            await save_snapshots(db, run_id, "regtest_b", [
                {"drug_name": "CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL",
                 "drug_code": "90287"},
                {"drug_name": "PLAIN ITEM", "drug_code": "90001"},
            ])
            # confirmed → irc lands in the registry; rejected must NOT
            await record_decision(db, insurer="regtest_b", raw_name="PLAIN ITEM",
                                  irc="__REGT__", status="confirmed",
                                  source_code="90001")
            await record_decision(db, insurer="regtest_a", raw_name="CICLOSPORIN",
                                  irc=None, status="rejected", source_code="90287",
                                  reason="wrong_product")
            await db.commit()

            # The cleanup is in `finally` because it used not to be: when an
            # assertion below failed, the fixtures stayed behind in whatever
            # database this ran against.
            try:
                reg = await build_code_registry(db)
                assert reg["90287"]["name"].startswith("CICLOSPORIN 100 mg")  # richest wins
                assert "irc" not in reg["90287"]                # rejected ≠ confirmed
                assert reg["90001"] == {"name": "PLAIN ITEM", "irc": "__REGT__"}
            finally:
                for ins in ("regtest_a", "regtest_b"):
                    await db.execute(delete(FormularySnapshot).where(
                        FormularySnapshot.insurer == ins))
                    await db.execute(delete(CrosswalkEntry).where(
                        CrosswalkEntry.insurer == ins))
                await db.commit()
        await eng.dispose()

    asyncio.get_event_loop().run_until_complete(run())
