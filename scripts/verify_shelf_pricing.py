#!/usr/bin/env python
"""Prove the price chain reaches the shelf, against a real database.

The unit tests pin the arithmetic in `services.core.inventory.shelf_price`. This
drives the receiving endpoint itself and then reads the lot rows back, because
the defect this work exists to fix was never in the arithmetic: the columns were
there, and nothing ever wrote them. Every lot in the working database carries a
`unit_cost` and not one carries a sell price, so `shelf_price_of` returns None
for every product and every quote line falls through to NFI's announced price.

What is proven here, in order:

  1. an invoice price entered at the door lands on the lot, with the margin
     derived from it and the basis recorded as `invoice`
  2. a declared margin, where the document is silent, computes the price and is
     recorded as `margin` — distinguishable from the case above
  3. neither leaves the lot unpriced, and the product without a shelf price,
     rather than filled with a house default
  4. the shelf price is the highest sellable lot, across those
  5. a sell price under cost is reported and the receipt still succeeds

    python scripts/verify_shelf_pricing.py

Every write lands in `pharmpilot_test` under a pharmacy created for this run.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.core.inventory import shelf_price as SP             # noqa: E402
from services.platform.routers import inventory_admin as AD       # noqa: E402
from tests.simulation.driver import Staff                          # noqa: E402


def url() -> str:
    if os.getenv("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    for line in dotenv.read_text().splitlines():
        m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
        if m:
            # Anchored: an unanchored replacement rewrites the *first* match and
            # points a test run at production.
            return re.sub(r"/pharmpilot$", "/pharmpilot_test", m.group(1))
    raise SystemExit("no test database configured")


async def tenant(db) -> uuid.UUID:
    pid = uuid.uuid4()
    src = (await db.execute(text("SELECT * FROM pharmacies LIMIT 1"))).mappings().first()
    unique = {"npi": str(uuid.uuid4().int)[:10], "ncpdp_id": str(uuid.uuid4().int)[:7],
              "dea_number": f"S{uuid.uuid4().hex[:8].upper()}",
              "nabp_number": str(uuid.uuid4().int)[:7]}
    cols = [c for c in src.keys() if c != "id"]
    sel = ", ".join(f":{c}" if c in unique else c for c in cols)
    await db.execute(text(
        f"INSERT INTO pharmacies (id, {', '.join(cols)}) "
        f"SELECT :nid, {sel} FROM pharmacies WHERE id = :src"),
        {"nid": pid, "src": src["id"],
         **{k: v for k, v in unique.items() if k in cols}})
    await db.commit()
    return pid


async def product(db, ndc: str) -> uuid.UUID:
    did = uuid.uuid4()
    await db.execute(text("""
        INSERT INTO drug_products
          (id, ndc11, generic_name, brand_name, strength, dosage_form,
           package_quantity, is_controlled, requires_refrigeration, is_active,
           discontinued, is_generic, is_otc, is_hazardous, high_risk_flag,
           drug_db_metadata, created_at, updated_at, is_deleted)
        VALUES (:i,:n,'verify drug','Verify','10mg','tablet',30,false,false,
                true,false,true,false,false,false,'{}',now(),now(),false)
        ON CONFLICT (ndc11) DO NOTHING"""), {"i": did, "n": ndc})
    got = (await db.execute(text("SELECT id FROM drug_products WHERE ndc11 = :n"),
                            {"n": ndc})).scalar()
    await db.commit()
    return got


async def lot_row(db, lot_id) -> dict:
    r = (await db.execute(text(
        "SELECT unit_cost, sell_price, margin_pct, sell_price_basis "
        "FROM inventory_lots WHERE id = :i"), {"i": lot_id})).mappings().first()
    return dict(r)


async def main() -> int:
    engine = create_async_engine(url(), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    fails: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail else ""))
        if not cond:
            fails.append(label)

    async with sm() as db:
        pid = await tenant(db)
        staff = Staff(pid)
        ndc = f"{uuid.uuid4().int % 10**11:011d}"
        did = await product(db, ndc)
        far = date.today() + timedelta(days=500)
        print(f"\ntenant {pid}\nproduct {did}  ndc {ndc}\n")

        def receipt(**kw):
            return AD.ReceiveLot(ndc11=ndc, expiry_date=far, quantity=10, **kw)

        # ── 1. the invoice names both prices ─────────────────────────────
        print("an invoice that names the consumer price")
        r1 = await AD.receive_stock(
            receipt(lot_number="INV-A", unit_cost=2770, sell_price=3324), staff, db)
        row = await lot_row(db, r1["lot_id"])
        check("sell price transcribed onto the lot",
              row["sell_price"] == Decimal("3324.0000"), str(row["sell_price"]))
        check("margin derived from the two figures, not typed",
              row["margin_pct"] == Decimal("20.000"), str(row["margin_pct"]))
        check("basis recorded as observed", row["sell_price_basis"] == "invoice",
              str(row["sell_price_basis"]))
        check("the response says which way it came",
              r1["pricing"]["basis"] == "invoice" and not r1["pricing"]["sells_at_a_loss"])

        # ── 2. a silent document, and a declared margin ──────────────────
        print("\na document that omits it, and a margin someone declares")
        r2 = await AD.receive_stock(
            receipt(lot_number="INV-B", unit_cost=3000, margin_pct=25), staff, db)
        row = await lot_row(db, r2["lot_id"])
        check("price computed from the declared margin",
              row["sell_price"] == Decimal("3750.0000"), str(row["sell_price"]))
        check("basis recorded as declared — NOT interchangeable with an invoice",
              row["sell_price_basis"] == "margin", str(row["sell_price_basis"]))

        # ── 3. neither: the gap is left open, not filled ─────────────────
        print("\na receipt with neither figure")
        r3 = await AD.receive_stock(
            receipt(lot_number="INV-C", unit_cost=1500), staff, db)
        row = await lot_row(db, r3["lot_id"])
        check("no price invented", row["sell_price"] is None, str(row["sell_price"]))
        check("no basis claimed", row["sell_price_basis"] is None)
        check("the bench is told this batch prices nothing",
              "قیمت قفسه تعیین نمی‌کند" in r3["pricing"]["explanation"])

        # ── 4. the shelf price, across those three lots ──────────────────
        print("\nthe shelf price the counter would charge")
        shelf = await SP.shelf_price_for_product(db, did)
        check("the dearest sellable lot sets it", shelf == Decimal("3750"), str(shelf))
        check("the unpriced lot did not drag it to zero", shelf is not None)

        # ── 5. a price under cost is reported, never refused ─────────────
        print("\na sell price below what the lot cost")
        r5 = await AD.receive_stock(
            receipt(lot_number="INV-D", unit_cost=5000, sell_price=4000), staff, db)
        row = await lot_row(db, r5["lot_id"])
        check("the receipt still succeeded — the goods did arrive",
              r5["lot_created"] is True)
        check("the loss is measured, not clamped at zero",
              row["margin_pct"] == Decimal("-20.000"), str(row["margin_pct"]))
        check("and flagged for the person holding the carton",
              r5["pricing"]["sells_at_a_loss"] is True)

        # ── 6. a later invoice for the same lot number ───────────────────
        print("\na top-up under the same lot number, at a new price")
        r6 = await AD.receive_stock(
            receipt(lot_number="INV-A", unit_cost=3100, sell_price=3900), staff, db)
        row = await lot_row(db, r6["lot_id"])
        check("the newer document wins", row["sell_price"] == Decimal("3900.0000"),
              str(row["sell_price"]))
        check("its margin is recomputed with it",
              row["margin_pct"] == Decimal("25.806"), str(row["margin_pct"]))

        # ── 7. a mandate must reach the TILL, not just the drawer ────────
        # The defect this section exists for: `shelf_price_for_product` read the
        # mandate flag and `shelf_prices_for_ircs` — the path a quote actually
        # takes — did not. The owner would override a bad price, the drawer would
        # agree with them, and the customer would still be charged the batch.
        print("\na mandated price, down the path a quote takes")
        irc = (await db.execute(text(
            "SELECT irc FROM drug_catalog WHERE irc IS NOT NULL LIMIT 1"))).scalar()
        if not irc:
            check("a formulary IRC to bind to", False, "drug_catalog is empty")
        else:
            ndc2 = f"{uuid.uuid4().int % 10**11:011d}"
            did2 = await product(db, ndc2)
            await AD.receive_stock(AD.ReceiveLot(
                ndc11=ndc2, expiry_date=far, quantity=10, lot_number="MAND-A",
                unit_cost=8000, sell_price=9999, irc=irc), staff, db)

            by_irc = await SP.shelf_prices_for_ircs(db, [irc])
            check("the batch prices the IRC before any override",
                  by_irc.get(irc) == Decimal("9999.0000"), str(by_irc.get(irc)))

            await SP.set_shelf_price(db, did2, 3100, reason="mistyped cost",
                                     staff_id=None, mandate=True)
            drawer = await SP.shelf_price_for_product(db, did2)
            till = (await SP.shelf_prices_for_ircs(db, [irc])).get(irc)
            check("the drawer honours the mandate", drawer == Decimal("3100"),
                  str(drawer))
            check("and so does the quote path", till == Decimal("3100"),
                  f"drawer={drawer} till={till}")

    await engine.dispose()
    print("\n" + ("all checks passed" if not fails
                  else f"{len(fails)} FAILED: " + "; ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
