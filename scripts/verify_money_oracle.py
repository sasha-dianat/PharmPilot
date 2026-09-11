#!/usr/bin/env python
"""Phase 1 of the pilot: the money oracle against the real pricing engine.

    python scripts/verify_money_oracle.py [--cases 4000] [--seed 11]

Differential testing. The oracle re-derives Iranian pharmacy adjudication from
the regulation and imports nothing from `services/core/pricing_ir`; this script
runs the real engine and the oracle over the same randomised prescriptions and
reports every disagreement.

No database. Pricing is pure arithmetic and a DB would only add a way for the
run to be skipped — the one thing a money check must never quietly do.

The generator deliberately produces the shapes that have actually broken this
engine rather than comfortable round numbers: sub-Rial prices (`sell_price` and
`manual_shelf_price` are both Numeric(12,4)), fractional quantities, references
ABOVE the consumer price (the stale-catalog case that billed an insurer above
the sale price), and the coarse 1,000-Rial rounding unit the config invites a
pharmacy to choose.
"""
from __future__ import annotations

import argparse
import random
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.core.pricing_ir import config as CFG            # noqa: E402
from services.core.pricing_ir.engine import (                 # noqa: E402
    DrugPrice, ItemCategory, LineInput, price_prescription)
from tests.simulation.domains.money import Line, MoneyDomain   # noqa: E402

D = Decimal
PLANS = ("tamin", "salamat", "armed_forces", "cash")
SETTINGS = ("outpatient", "inpatient")
CATEGORIES = ("drug", "otc", "supplement", "cosmetic")

# Prices that have actually appeared, not decorative ones.
PRICES = ["3324.5", "1662.25", "5750", "19663", "17", "727000", "100000",
          "1", "0.5", "12345.6789", "999999999", "0"]
QTYS = ["1", "3", "30", "0.5", "2.5", "7", "90", "0.001"]


def a_line(rng: random.Random) -> tuple[Line, LineInput]:
    """One line, described identically to both implementations."""
    consumer = D(rng.choice(PRICES))
    category = rng.choices(CATEGORIES, weights=[70, 15, 10, 5])[0]
    covered = category in ("drug", "otc") and rng.random() > 0.15
    # A reference above the consumer price is the stale-catalog case; below is
    # the ordinary one that produces مابه‌التفاوت.
    if rng.random() < 0.2:
        ref = None
    elif rng.random() < 0.25:
        ref = consumer * D(rng.choice(["1.5", "3.42", "10"]))
    else:
        ref = consumer * D(rng.choice(["0.5", "0.8", "1.0", "0.33"]))
    qty = D(rng.choice(QTYS))
    irc = f"IRC{rng.randint(1, 9999)}"

    vat = CFG.VAT_RATE_COSMETIC if category == "cosmetic" else (
        CFG.VAT_RATE_SUPPLEMENT if category == "supplement" else CFG.VAT_RATE_DRUG)

    ours = Line(irc=irc, quantity=qty, consumer_price=consumer,
                reference_price=ref, category=category, is_covered=covered)
    theirs = LineInput(
        drug=DrugPrice(irc=irc, name=irc, consumer_price=consumer,
                       insurer_reference_price=ref,
                       category=ItemCategory(category), is_covered=covered,
                       vat_rate=D(str(vat))),
        quantity=qty)
    return ours, theirs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=11)
    args = ap.parse_args()

    fails: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        print(f"  {'ok  ' if cond else 'FAIL'}  {label}"
              + (f"  — {detail}" if detail and not cond else ""))
        if not cond:
            fails.append(label)

    # ── the tariff gate ───────────────────────────────────────────────────
    print("\nthe tariff gate")
    drift = MoneyDomain().tariff_drift()
    check("the oracle's tariffs and the deployment's agree", not drift,
          "; ".join(drift))

    # ── differential testing ──────────────────────────────────────────────
    for unit in (D("1"), D("1000")):
        CFG.ROUNDING_UNIT_RIAL = unit
        rng = random.Random(args.seed)
        oracle = MoneyDomain(rounding_unit=unit)
        disagreements: list[str] = []
        engine_conservation: list[str] = []
        n_lines = 0

        print(f"\n{args.cases} prescriptions at a {unit}-Rial rounding unit")
        for i in range(args.cases):
            plan_code = rng.choice(PLANS)
            setting = rng.choice(SETTINGS)
            fee = D(rng.choice(["0", "727000", "81000", "1620000"]))
            pairs = [a_line(rng) for _ in range(rng.randint(1, 5))]
            ours = [p[0] for p in pairs]
            theirs = [p[1] for p in pairs]
            n_lines += len(pairs)
            rx = f"RX{i}"

            got = price_prescription(theirs, CFG.get_plan(plan_code),
                                     technical_fee=fee, setting=setting)
            mine = oracle.quote(rx, ours, plan_code, setting, fee)

            # the engine's own output must conserve, independent of the oracle
            for b in got.lines:
                if b.insurer_share + b.patient_total != b.gross + b.vat:
                    engine_conservation.append(
                        f"{rx}/{b.irc}: insurer {b.insurer_share} + patient "
                        f"{b.patient_total} != gross {b.gross} + vat {b.vat}")
                # Covered lines only: an uncovered line has no insurer base to
                # differ from, so base and differential are both legitimately
                # zero against a non-zero gross.
                if b.covered and b.covered_base + b.differential != b.gross:
                    engine_conservation.append(
                        f"{rx}/{b.irc}: base {b.covered_base} + differential "
                        f"{b.differential} != gross {b.gross}")
                if b.insurer_share < 0 or b.patient_total < 0:
                    engine_conservation.append(
                        f"{rx}/{b.irc}: negative money "
                        f"(insurer {b.insurer_share}, patient {b.patient_total})")

            # the oracle's own invariants
            for v in oracle._check_invariants(rx, mine):
                disagreements.append(f"ORACLE {v}")

            for name, theirs_v, ours_v in (
                    ("gross", got.totals.gross, mine.gross),
                    ("insurer", got.totals.insurer, mine.insurer),
                    ("patient", got.totals.patient, mine.patient),
                    ("differential", got.totals.differential, mine.differential),
                    ("vat", got.totals.vat, mine.vat),
                    ("fee to insurer", got.technical_fee.insurer, mine.fee_insurer)):
                if theirs_v != ours_v:
                    disagreements.append(
                        f"{rx} ({plan_code}/{setting}): {name} — engine {theirs_v}, "
                        f"regulation {ours_v}, difference {theirs_v - ours_v}")

        check(f"the engine's own lines conserve ({n_lines} lines)",
              not engine_conservation,
              f"{len(engine_conservation)} break(s): {engine_conservation[:3]}")
        check("the engine and the regulation agree on every prescription",
              not disagreements,
              f"{len(disagreements)} disagreement(s): {disagreements[:5]}")
        if oracle.violations():
            check("the oracle did not break its own rules", False,
                  str(oracle.violations()[:3]))

    CFG.ROUNDING_UNIT_RIAL = D("1")

    # ── the defects the engine has actually had ───────────────────────────
    print("\nregressions the oracle must be able to catch")
    o = MoneyDomain()

    # an insurer billed above the sale price
    b = o.price_line(Line("K", D("1"), D("5750"), D("19663"), "drug", True),
                     "salamat", "outpatient")
    check("a reference above the sale price cannot bill the insurer more",
          b["insurer"] + b["patient_total"] == b["gross"], str(b))

    # حق فنی split with an insurer that pays none of it
    q = o.quote("RXF", [Line("A", D("1"), D("100000"), None, "drug", True)],
                "tamin", "outpatient", D("727000"))
    check("the whole dispensing fee falls to the patient",
          q.fee_insurer == 0 and q.fee_patient == D("727000"),
          f"insurer {q.fee_insurer}, patient {q.fee_patient}")

    # the decile trap: drugs are carved out of the 1405 band
    b = o.price_line(Line("A", D("1"), D("100000"), None, "drug", True),
                     "tamin", "outpatient")
    check("the 1405 decile band is not applied to a drug line",
          b["patient_share"] == D("30000"), str(b["patient_share"]))

    # ── the cross-domain rule, which is the point of the spine ────────────
    print("\nthe rule a single-domain simulation cannot see")
    import asyncio
    from tests.simulation.spine.facts import Fact
    from tests.simulation.spine.clock import SimClock
    from datetime import date as _date

    m = MoneyDomain()
    clk = SimClock(start=_date(2026, 1, 1))
    m.observe(Fact(seq=1, at=clk.now(), day=0, kind="dispensed", subject="N1",
                   quantity=D("30"), payload={"rx": "RX-UNPRICED"}))
    found = asyncio.run(m.check(None))
    check("units dispensed without a price are reported",
          any("never priced" in f for f in found), str(found))

    print(f"\n{'PASS' if not fails else 'FAIL: ' + ', '.join(fails)}\n")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
