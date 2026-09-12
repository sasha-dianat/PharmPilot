#!/usr/bin/env python
"""Prove the money adds up, on real catalog rows, through the real endpoint.

`tests/unit/test_pricing_ir.py` pins the identity across a synthetic parameter
sweep. This drives `/pricing/quote` itself against the WORKING database — the
only one holding a real formulary — on IRCs that carry a genuine insurer
coverage entry, which is what produces مابه‌التفاوت and is exactly where the
rounding defect lived.

The identities, per line and for the prescription:

    insurer_share + patient_total == gross + vat          (every line)
    covered_base  + differential  == gross                (covered lines)
    insurer_share == 0 and patient_share == gross         (uncovered lines)
    grand_total   == gross + vat + technical_fee

It ran at every rounding unit, because a pharmacy may set a coarser one and
config.py invites it. The engine used to round `covered_base` and `differential`
independently, so `round(a) + round(b) ≠ round(a + b)` opened a gap of a Rial at
the default unit and 1,000 Rial at the coarse one — on a receipt handed to a
patient, with the insurer billed the difference.

    python scripts/verify_pricing_conservation.py

Read-only: it prices, it does not write.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text                                       # noqa: E402
from sqlalchemy.ext.asyncio import (async_sessionmaker,           # noqa: E402
                                    create_async_engine)

from services.core.pricing_ir import config as CFG                # noqa: E402
from services.platform.routers import pricing as PR               # noqa: E402


def url() -> str:
    """The working database, deliberately, and READ-ONLY.

    The point of this script is real formulary rows: 28,113 of them carry a real
    insurer reference price, which is what produces مابه‌التفاوت and is exactly
    where the rounding defect lived. `pharmpilot_test` holds one priced row and
    none with a reference, so running there would prove nothing and report
    success while doing it.

    Nothing here writes: it resolves catalog records and prices them. Set
    PRICING_VERIFY_DATABASE_URL to point elsewhere.
    """
    if os.getenv("PRICING_VERIFY_DATABASE_URL"):
        return os.environ["PRICING_VERIFY_DATABASE_URL"]
    dotenv = Path(__file__).resolve().parents[1] / ".env"
    for line in dotenv.read_text().splitlines():
        m = re.match(r"^DATABASE_URL=(.+)$", line.strip())
        if m:
            return m.group(1)
    raise SystemExit("no database configured")


class Staff:
    """Stand-in for the authenticated caller; `quote` reads no other field."""
    def __init__(self, pharmacy_id):
        self.id = uuid.uuid4()
        self.pharmacy_id = pharmacy_id
        self.role = "pharmacist"
        self.permissions = ["*"]


async def sample_ircs(db, limit: int = 40) -> tuple[list[str], list[str]]:
    """(IRCs carrying an insurer coverage entry, plain IRCs).

    The first group is the interesting one: a coverage entry supplies a
    reference price, and a reference below the sale price is what creates the
    differential the defect used to mis-round.
    """
    covered = (await db.execute(text("""
        SELECT irc FROM drug_catalog
         WHERE coverage IS NOT NULL
           AND coverage::text LIKE '%reference_price%'
           AND announced_price IS NOT NULL AND announced_price > 0
         LIMIT :n"""), {"n": limit})).scalars().all()
    plain = (await db.execute(text("""
        SELECT irc FROM drug_catalog
         WHERE announced_price IS NOT NULL AND announced_price > 0
         LIMIT :n"""), {"n": limit})).scalars().all()
    return [str(i) for i in covered], [str(i) for i in plain]


async def main() -> int:
    engine = create_async_engine(url(), echo=False)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    fails: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}" + (f"  — {detail}" if detail else ""))
        if not cond:
            fails.append(label)

    async with sm() as db:
        pid = (await db.execute(text("SELECT id FROM pharmacies LIMIT 1"))).scalar()
        staff = Staff(pid)
        covered, plain = await sample_ircs(db)
        print(f"\n{len(covered)} IRCs with an insurer reference, {len(plain)} plain\n")
        if not plain:
            check("a loaded formulary to price against", False, "drug_catalog is empty")
            await engine.dispose()
            return 1

        QUANTITIES = [1, 2, 3, 30, 0.5, 1.5, 2.5, 0.333]
        UNITS = [Decimal("1"), Decimal("10"), Decimal("100"), Decimal("1000")]
        INSURERS = ["tamin", "salamat", "armed_forces", "cash"]

        before = CFG.ROUNDING_UNIT_RIAL
        try:
            for unit in UNITS:
                CFG.ROUNDING_UNIT_RIAL = unit
                line_breaks = base_breaks = rx_breaks = uncovered_breaks = 0
                priced = 0
                for insurer in INSURERS:
                    for setting in ("outpatient", "inpatient"):
                        for qty in QUANTITIES:
                            lines = [PR.QuoteLineIn(irc=i, quantity=qty)
                                     for i in (covered[:12] or plain[:12])]
                            lines += [PR.QuoteLineIn(irc=i, quantity=qty)
                                      for i in plain[:6]]
                            body = PR.QuoteRequest(insurer=insurer, setting=setting,
                                                   technical_fee=150000, lines=lines)
                            res = await PR.quote(body, staff, db)

                            for l in res["lines"]:
                                lhs = (Decimal(str(l["insurer_share"]))
                                       + Decimal(str(l["patient_total"])))
                                rhs = Decimal(str(l["gross"])) + Decimal(str(l["vat"]))
                                if lhs != rhs:
                                    line_breaks += 1
                                if l["covered"]:
                                    # Only meaningful for a covered line. On an
                                    # uncovered one both are zero by design and
                                    # the patient pays the whole gross — asserting
                                    # the identity there reports a defect that is
                                    # actually the rule working. salamat marks
                                    # rows uncovered that the other plans fall
                                    # back to covering, which is why this only
                                    # ever fired under one insurer.
                                    if (Decimal(str(l["covered_base"]))
                                            + Decimal(str(l["differential"]))
                                            != Decimal(str(l["gross"]))):
                                        base_breaks += 1
                                else:
                                    if (Decimal(str(l["insurer_share"])) != 0
                                            or Decimal(str(l["patient_share"]))
                                            != Decimal(str(l["gross"]))):
                                        uncovered_breaks += 1
                                priced += 1
                            t = res["totals"]
                            if (Decimal(str(t["grand_total"]))
                                    != Decimal(str(t["gross"])) + Decimal(str(t["vat"]))
                                    + Decimal(str(res["technical_fee"]["total"]))):
                                rx_breaks += 1
                print(f"rounding unit {unit} — {priced} priced lines")
                check("every line: insurer_share + patient_total == gross + vat",
                      line_breaks == 0, f"{line_breaks} broken")
                check("every line: covered_base + differential == gross",
                      base_breaks == 0, f"{base_breaks} broken")
                check("uncovered line: insurer pays 0, patient pays the gross",
                      uncovered_breaks == 0, f"{uncovered_breaks} broken")
                check("every prescription: grand_total == gross + vat + fee",
                      rx_breaks == 0, f"{rx_breaks} broken")
        finally:
            CFG.ROUNDING_UNIT_RIAL = before

        # The response floats every Decimal. That is lossless ONLY because every
        # amount is rounded to the Rial unit first — a float holds integers
        # exactly to 2^53. Worth pinning: if a raw sub-Rial amount ever reaches
        # the response, this is where it stops being exact.
        res = await PR.quote(PR.QuoteRequest(
            insurer="tamin", setting="outpatient",
            lines=[PR.QuoteLineIn(irc=i, quantity=1) for i in plain[:5]]), staff, db)
        exact = all(float(v).is_integer()
                    for l in res["lines"] for v in
                    (l["gross"], l["insurer_share"], l["patient_total"], l["vat"]))
        check("amounts survive the float boundary as exact Rial", exact)

    await engine.dispose()
    print("\n" + ("all checks passed" if not fails
                  else f"{len(fails)} FAILED: " + "; ".join(fails)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
