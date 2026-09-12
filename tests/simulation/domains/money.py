"""The money oracle: what the patient and the insurer each owe, re-derived.

Phase 1 of the pilot. This is an *independent* re-derivation, which is the only
kind worth having: it imports nothing from `services/core/pricing_ir`, so it
cannot inherit the engine's arithmetic and agree with it for the wrong reason.
What it knows about Iranian pharmacy adjudication is written here from the
regulation, with the same citations the engine's config carries, and a
disagreement means one of the two is wrong.

Three separations are deliberate and each one was a temptation:

**Tariffs are restated, not imported.** Reading `config.PLANS` would make a
wrong tariff invisible — both sides would be wrong together and the run would
pass. So the franchises live below, sourced independently. The cost is that a
lawful tariff change breaks the oracle; `tariff_drift()` exists to make that
break *say so* — "the tariff moved, confirm and update the oracle" is a
different sentence from "the engine computed the wrong number", and reporting
the first as the second would burn a domain expert's afternoon.

**Invariants are asserted; formulas are not copied.** The engine derives
مابه‌التفاوت as `gross − covered_base` rather than rounding `(consumer − ref)×qty`
on its own, because round(a) + round(b) ≠ round(a+b) and the naive form breaks
the line by a Rial — or by a thousand at the coarser rounding unit the config
invites a pharmacy to choose. An oracle that recomputed it naively would
"disagree" on exactly the cases the engine got right. So where rounding order is
a free choice this checks the *identity* (`covered_base + differential == gross`),
and where there is only one right answer it checks the value.

**The rounding unit is a parameter, not a constant.** It is a deployment
setting, not a tariff; hard-coding whole Rial here would make the oracle wrong
for a pharmacy that rounds to the thousand, and importing it from config would
reintroduce the coupling the first separation exists to prevent.

The cross-domain rule at the bottom is the reason the spine exists at all: every
unit that leaves the shelf must have been priced. Stock and money disagreeing
about the same dispense is the failure a single-domain simulation cannot see.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from ..spine.facts import Fact

# ── the regulation, restated independently ────────────────────────────────
# Sources are the same ones the pricing config cites (researched 2026-08-22,
# ≈ مرداد ۱۴۰۵) but the figures are entered here from the regulation rather than
# read from that module. See the module docstring for why.
#
# Drugs: 30% patient outpatient / 10% inpatient, statutory and unchanged for
# 1405, across تأمین اجتماعی and بیمه سلامت alike.
#
# TRAP, checked and avoided here as well: the 1405 cabinet resolution introduced
# a decile band for the outpatient franchise (1-3 → 25%, 4-6 → 30%, 7-10 → 40%)
# «به استثنای داروها». Drugs are carved out. An oracle that applied the decile
# table would disagree with a correct engine on almost every row and look like
# the engine was broken.
#
# ساخد cut the outpatient DRUG franchise to 15%. Its 0.00 inpatient figure is
# the government/military contracted centre — the common case, not universal.
FRANCHISE: dict[tuple[str, str], Decimal] = {
    ("tamin", "outpatient"): Decimal("0.30"),
    ("tamin", "inpatient"): Decimal("0.10"),
    ("salamat", "outpatient"): Decimal("0.30"),
    ("salamat", "inpatient"): Decimal("0.10"),
    ("armed_forces", "outpatient"): Decimal("0.15"),
    ("armed_forces", "inpatient"): Decimal("0.00"),
    ("cash", "outpatient"): Decimal("1.00"),
    ("cash", "inpatient"): Decimal("1.00"),
}

# حق فنی is the PATIENT's. No basic insurer contributes any part of it; the
# pharmacists' association is still lobbying to have basic insurance cover it,
# which is itself the evidence that it does not. The engine previously billed an
# insurer 508,900 Rial per prescription that no insurer pays.
INSURER_PAYS_ANY_TECHNICAL_FEE = False

# Drugs and vaccines are exempt — ماده ۹ بند (الف) جزء (۱۵) of the 1400 VAT law.
# مکمل دارویی holding an IRC licence became exempt under بخشنامه ۲۰۰/۴/۱۴۰۳; a
# food or sports supplement is not a drug and stays taxable, so a mis-categorised
# record is under-taxed — which is a catalog defect, not an arithmetic one, and
# this oracle deliberately does not try to second-guess the category.
VAT_RATE: dict[str, Decimal] = {
    "drug": Decimal("0"),
    "otc": Decimal("0"),
    "supplement": Decimal("0"),
    "cosmetic": Decimal("0.10"),   # 9% VAT + 1% عوارض
}

# Categories a basic insurer can be billed for at all.
INSURABLE = ("drug", "otc")


@dataclass
class Line:
    """One priced line, as the oracle understands it."""
    irc: str
    quantity: Decimal
    consumer_price: Decimal
    reference_price: Decimal | None
    category: str
    is_covered: bool


@dataclass
class Quote:
    """What the oracle says a prescription costs."""
    rx: str
    plan: str
    setting: str
    gross: Decimal = Decimal("0")
    covered_base: Decimal = Decimal("0")
    insurer: Decimal = Decimal("0")
    patient: Decimal = Decimal("0")
    differential: Decimal = Decimal("0")
    vat: Decimal = Decimal("0")
    fee_total: Decimal = Decimal("0")
    fee_patient: Decimal = Decimal("0")
    fee_insurer: Decimal = Decimal("0")
    lines: list[dict] = field(default_factory=list)


class MoneyDomain:
    """Independent ground truth for what each party owes."""

    name = "money"

    def __init__(self, *, rounding_unit: Decimal = Decimal("1")):
        if rounding_unit <= 0:
            raise ValueError("rounding unit must be positive")
        self.unit = rounding_unit
        self.quotes: dict[str, Quote] = {}
        self.priced_rx: set[str] = set()
        self.paid_rx: dict[str, Decimal] = {}
        self.adjudicated_rx: dict[str, Decimal] = {}
        # units that left the shelf, by rx — the cross-domain hook
        self.dispensed_units: dict[str, Decimal] = {}
        self._disagreements: list[str] = []
        self._broken: list[str] = []

    # ── arithmetic ────────────────────────────────────────────────────────
    def _round(self, amount: Decimal) -> Decimal:
        if self.unit == Decimal("1"):
            return amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return (amount / self.unit).quantize(Decimal("1"),
                                             rounding=ROUND_HALF_UP) * self.unit

    def franchise(self, plan: str, setting: str) -> Decimal:
        try:
            return FRANCHISE[(plan, setting)]
        except KeyError:
            # An unknown plan must not silently become self-pay. The engine
            # falls back to cash by design; the oracle says so out loud, because
            # a typo'd plan code that quietly charges the patient 100% is a
            # money defect that looks like a configuration one.
            self._broken.append(
                f"no franchise known for plan {plan!r} in {setting!r}; the "
                f"oracle cannot judge this line")
            return Decimal("1.00")

    def price_line(self, ln: Line, plan: str, setting: str) -> dict:
        """What this line costs, derived from the regulation."""
        qty = ln.quantity if isinstance(ln.quantity, Decimal) else Decimal(str(ln.quantity))
        gross = self._round(ln.consumer_price * qty)

        eligible = ln.is_covered and ln.category in INSURABLE
        vat_rate = VAT_RATE.get(ln.category, Decimal("0"))
        vat = self._round(gross * vat_rate)

        if not eligible:
            return {"irc": ln.irc, "covered": False, "gross": gross,
                    "covered_base": Decimal("0"), "insurer": Decimal("0"),
                    "patient_share": gross, "differential": Decimal("0"),
                    "vat": vat, "patient_total": self._round(gross + vat)}

        # The insurer never recognises more than the item actually costs. A
        # stale catalog price BELOW the published reference is the case that
        # billed an insurer above the sale price — ketotifen at 5,750 Rial
        # against a 19,663 reference collected 19,663 on a 5,750 item.
        ref_unit = ln.reference_price if ln.reference_price is not None else ln.consumer_price
        ref_unit = min(ref_unit, ln.consumer_price)
        covered_base = self._round(ref_unit * qty)
        # Identity, not formula: مابه‌التفاوت is the part of the sale price the
        # insurer does not recognise. See the module docstring.
        differential = gross - covered_base
        patient_share = self._round(covered_base * self.franchise(plan, setting))
        insurer = covered_base - patient_share
        return {"irc": ln.irc, "covered": True, "gross": gross,
                "covered_base": covered_base, "insurer": insurer,
                "patient_share": patient_share, "differential": differential,
                "vat": vat,
                "patient_total": self._round(patient_share + differential + vat)}

    def quote(self, rx: str, lines: list[Line], plan: str, setting: str,
              fee_total: Decimal) -> Quote:
        q = Quote(rx=rx, plan=plan, setting=setting)
        for ln in lines:
            b = self.price_line(ln, plan, setting)
            q.lines.append(b)
            q.gross += b["gross"]
            q.covered_base += b["covered_base"]
            q.insurer += b["insurer"]
            q.differential += b["differential"]
            q.vat += b["vat"]
            q.patient += b["patient_total"]
        q.fee_total = self._round(fee_total)
        # The whole fee is the patient's. Splitting it is the correction that
        # commit d68acfb made; an oracle that allowed a split would have let the
        # original defect back in unnoticed.
        q.fee_patient = q.fee_total if not INSURER_PAYS_ANY_TECHNICAL_FEE else Decimal("0")
        q.fee_insurer = q.fee_total - q.fee_patient
        q.patient += q.fee_patient
        q.insurer += q.fee_insurer
        return q

    # ── the Domain protocol ───────────────────────────────────────────────
    def observe(self, fact: Fact) -> None:
        if fact.kind == "priced":
            self._observe_priced(fact)
        elif fact.kind == "dispensed":
            rx = str(fact.payload.get("rx") or fact.subject)
            self.dispensed_units[rx] = (self.dispensed_units.get(rx, Decimal("0"))
                                        + (fact.quantity or Decimal("0")))
        elif fact.kind == "paid":
            rx = str(fact.payload.get("rx") or fact.subject)
            self.paid_rx[rx] = self.paid_rx.get(rx, Decimal("0")) + (fact.money or Decimal("0"))
        elif fact.kind == "adjudicated":
            rx = str(fact.payload.get("rx") or fact.subject)
            self.adjudicated_rx[rx] = (self.adjudicated_rx.get(rx, Decimal("0"))
                                       + (fact.money or Decimal("0")))

    def _observe_priced(self, fact: Fact) -> None:
        """Re-derive the quote and compare it against what the engine returned."""
        p = fact.payload
        rx = str(p.get("rx") or fact.subject)
        lines = [Line(irc=str(d["irc"]), quantity=Decimal(str(d["quantity"])),
                      consumer_price=Decimal(str(d["consumer_price"])),
                      reference_price=(None if d.get("reference_price") is None
                                       else Decimal(str(d["reference_price"]))),
                      category=str(d.get("category", "drug")),
                      is_covered=bool(d.get("is_covered", True)))
                 for d in p.get("lines", [])]
        mine = self.quote(rx, lines, str(p.get("plan", "cash")),
                          str(p.get("setting", "outpatient")),
                          Decimal(str(p.get("technical_fee", 0))))
        self.quotes[rx] = mine
        self.priced_rx.add(rx)

        self._disagreements.extend(self._check_invariants(rx, mine))

        engine = p.get("engine")
        if engine:
            self._disagreements.extend(self._compare(rx, mine, engine))

    def _check_invariants(self, rx: str, q: Quote) -> list[str]:
        """Properties that must hold whatever the engine computed."""
        out: list[str] = []
        for b in q.lines:
            tag = f"rx {rx} line {b['irc']}"
            if b["covered_base"] > b["gross"]:
                out.append(f"{tag}: the insurer-recognised base {b['covered_base']} "
                           f"exceeds the sale price {b['gross']}")
            # Only a covered line decomposes into base + مابه‌التفاوت. On an
            # uncovered one there is no insurer base to differ FROM: base and
            # differential are both zero while the patient pays the whole gross,
            # so applying the identity here reports every cosmetic and every
            # non-formulary drug as a conservation break. The first run of this
            # oracle did exactly that — 2,908 findings, all of them the oracle's
            # own fault, and none of them the engine's.
            if b["covered"] and b["covered_base"] + b["differential"] != b["gross"]:
                out.append(f"{tag}: covered_base {b['covered_base']} + differential "
                           f"{b['differential']} != gross {b['gross']}")
            if b["insurer"] + b["patient_total"] != b["gross"] + b["vat"]:
                out.append(f"{tag}: conservation broken — insurer {b['insurer']} + "
                           f"patient {b['patient_total']} != gross {b['gross']} + "
                           f"vat {b['vat']}")
            for name in ("gross", "covered_base", "insurer", "patient_share",
                         "differential", "vat", "patient_total"):
                if b[name] < 0:
                    out.append(f"{tag}: {name} is negative ({b[name]})")
            if not b["covered"] and b["insurer"] != 0:
                out.append(f"{tag}: not covered, yet an insurer is billed {b['insurer']}")
        if q.fee_insurer != 0:
            out.append(f"rx {rx}: an insurer is billed {q.fee_insurer} of حق فنی, "
                       f"which no basic insurer pays")
        if q.insurer + q.patient != q.gross + q.vat + q.fee_total:
            out.append(f"rx {rx}: prescription conservation broken — insurer "
                       f"{q.insurer} + patient {q.patient} != gross {q.gross} + "
                       f"vat {q.vat} + fee {q.fee_total}")
        return out

    def _compare(self, rx: str, mine: Quote, engine: dict) -> list[str]:
        """Where the oracle and the engine disagree about the same prescription."""
        out: list[str] = []
        for name, ours in (("gross", mine.gross), ("insurer", mine.insurer),
                           ("patient", mine.patient),
                           ("differential", mine.differential), ("vat", mine.vat)):
            if name not in engine:
                continue
            theirs = Decimal(str(engine[name]))
            if theirs != ours:
                out.append(f"rx {rx}: engine says {name} {theirs}, the regulation "
                           f"gives {ours} (difference {theirs - ours})")
        if "fee_insurer" in engine and Decimal(str(engine["fee_insurer"])) != 0:
            out.append(f"rx {rx}: engine split حق فنی and billed an insurer "
                       f"{engine['fee_insurer']}; the whole fee is the patient's")
        return out

    async def check(self, ctx) -> list[str]:
        out = list(self._disagreements)
        self._disagreements.clear()

        # The cross-domain rule this whole spine exists for: units left the
        # shelf, and money must know what they cost. A dispense nobody priced is
        # stock and money disagreeing about the same event.
        for rx, units in self.dispensed_units.items():
            if units > 0 and rx not in self.priced_rx:
                out.append(f"rx {rx}: {units} unit(s) dispensed but never priced — "
                           f"stock says they left, money says nothing was owed")

        # And what was collected must match what was owed.
        for rx, paid in self.paid_rx.items():
            q = self.quotes.get(rx)
            if q is None:
                out.append(f"rx {rx}: {paid} collected against a prescription that "
                           f"was never priced")
            elif paid != q.patient:
                out.append(f"rx {rx}: collected {paid}, the patient owed {q.patient}")

        for rx, billed in self.adjudicated_rx.items():
            q = self.quotes.get(rx)
            if q is not None and billed != q.insurer:
                out.append(f"rx {rx}: billed the insurer {billed}, the insurer owed "
                           f"{q.insurer}")
        return out

    def violations(self) -> list[str]:
        return list(self._broken)

    # ── the tariff gate ───────────────────────────────────────────────────
    def tariff_drift(self) -> list[str]:
        """Where the deployment's tariffs differ from the ones restated here.

        NOT a defect report. A tariff legitimately moves — the 1405 resolution
        moved several — and when it does this oracle is the stale one. Kept
        separate from `check()` so a tariff change is never reported as the
        engine computing a wrong number; those need different people.
        """
        from services.core.pricing_ir import config as cfg

        out: list[str] = []
        for code, plan in cfg.PLANS.items():
            for setting, theirs in (("outpatient", plan.outpatient_patient_share),
                                    ("inpatient", plan.inpatient_patient_share)):
                ours = FRANCHISE.get((code, setting))
                if ours is None:
                    out.append(f"config knows plan {code!r}, the oracle does not")
                elif Decimal(str(theirs)) != ours:
                    out.append(f"{code}/{setting}: config {theirs}, oracle {ours}")
            if plan.covers_technical_fee and not INSURER_PAYS_ANY_TECHNICAL_FEE:
                out.append(f"{code}: config says the insurer covers حق فنی; the "
                           f"oracle holds that no basic insurer does")
        if Decimal(str(cfg.VAT_RATE_DRUG)) != VAT_RATE["drug"]:
            out.append(f"drug VAT: config {cfg.VAT_RATE_DRUG}, oracle {VAT_RATE['drug']}")
        if Decimal(str(cfg.VAT_RATE_COSMETIC)) != VAT_RATE["cosmetic"]:
            out.append(f"cosmetic VAT: config {cfg.VAT_RATE_COSMETIC}, "
                       f"oracle {VAT_RATE['cosmetic']}")
        return out
