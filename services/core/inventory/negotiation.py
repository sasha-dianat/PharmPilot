"""⑳ — preparing the owner for a conversation with a distributor.

It does not negotiate. It does not send anything. It works out what this pharmacy
is actually worth to a supplier, where the current terms are worse than terms the
pharmacy is *already getting elsewhere*, and which concessions are cheap to give
— then hands a person the numbers.

**It never invents a benchmark.** This is the one rule that matters more than any
output here, because the failure is not a wrong number on a screen: the owner
repeats it aloud to a distributor who knows the real figure, and the whole
conversation is lost. So every comparison in this module is between two prices
*this pharmacy has actually paid*. Where there is only one supplier for a
molecule there is no comparison, and the brief says so instead of reaching for a
market rate. A cross-pharmacy benchmark would need more than one pharmacy on the
platform; until then that tier is inert and declares itself.

Three things it computes that nobody currently does:

**Leverage.** Annual spend with this supplier, its share of the pharmacy's total,
and how concentrated purchasing is. A supplier taking 40% of spend hears a very
different argument from one taking 4%, and the owner should know which
conversation they are in before they open their mouth.

**The negotiable gap.** For molecules bought from more than one supplier, the
difference between what this supplier charges and the best price already being
paid, times annual volume. Not the list price — the realised one.

**What unreliability costs.** E12's fill rate turned into money: the units this
supplier failed to deliver, times the margin they would have earned. This is the
strongest card in the conversation and it has never been computed. Where no sale
price is recorded the units are reported and the money is **not** — a guessed
margin quoted in a negotiation is the same failure as a guessed benchmark.

Pure functions over already-fetched rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import receiving as RCV
from .ledger import q

# Below two suppliers there is nothing to compare and the brief says so. Below
# roughly two quarters of orders, a "realised price" is one invoice with a
# rounding error.
MIN_SUPPLIERS_TO_COMPARE = 2
MIN_LINES_PER_SUPPLIER = 4

# A price gap smaller than this fraction is invoice noise — freight, a rounding
# difference, one delivery priced on an older list — and asking a distributor to
# match it spends credibility on nothing.
MEANINGFUL_GAP = Decimal("0.03")

# Herfindahl thresholds, read as plain language rather than reported as a number
# nobody outside economics recognises.
CONCENTRATED = Decimal("0.40")
DEPENDENT = Decimal("0.60")


@dataclass(frozen=True)
class Leverage:
    supplier: str
    spend: Decimal
    share: Decimal | None
    lines: int
    molecules: int
    unpriced_lines: int
    concentration: Decimal | None
    standing: str                    # marginal | significant | principal | sole
    explanation: str

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "spend": float(self.spend),
                "share": None if self.share is None else float(self.share),
                "lines": self.lines, "molecules": self.molecules,
                "unpriced_lines": self.unpriced_lines,
                "concentration": None if self.concentration is None
                                 else float(self.concentration),
                "standing": self.standing, "explanation": self.explanation}


@dataclass(frozen=True)
class TermGap:
    """One molecule this supplier charges more for than somebody already does."""
    ndc11: str
    our_cost: Decimal
    best_cost: Decimal
    best_supplier: str
    gap_per_unit: Decimal
    gap_pct: Decimal
    annual_units: Decimal
    annual_value: Decimal

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "our_cost": float(self.our_cost),
                "best_cost": float(self.best_cost),
                "best_supplier": self.best_supplier,
                "gap_per_unit": float(self.gap_per_unit),
                "gap_pct": float(self.gap_pct),
                "annual_units": float(self.annual_units),
                "annual_value": float(self.annual_value)}


@dataclass(frozen=True)
class ReliabilityCost:
    undelivered_units: Decimal
    priced_units: Decimal
    forgone_margin: Decimal | None
    basis: str                       # observed | partly_priced | unpriced
    explanation: str

    def as_dict(self) -> dict:
        return {"undelivered_units": float(self.undelivered_units),
                "priced_units": float(self.priced_units),
                "forgone_margin": None if self.forgone_margin is None
                                  else float(self.forgone_margin),
                "basis": self.basis, "explanation": self.explanation}


@dataclass(frozen=True)
class Ask:
    """Something to ask for, and the number that justifies asking."""
    ask: str
    worth: Decimal | None
    basis: str
    explanation: str

    def as_dict(self) -> dict:
        return {"ask": self.ask,
                "worth": None if self.worth is None else float(self.worth),
                "basis": self.basis, "explanation": self.explanation}


@dataclass(frozen=True)
class Brief:
    supplier: str
    leverage: Leverage
    gaps: list[TermGap]
    negotiable_annual: Decimal
    reliability: ReliabilityCost
    asks: list[Ask]
    concessions: list[Ask]
    cannot_say: list[str]
    basis: str                       # observed | volume_only
    explanation: str
    cloud: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "leverage": self.leverage.as_dict(),
                "gaps": [g.as_dict() for g in self.gaps],
                "negotiable_annual": float(self.negotiable_annual),
                "reliability": self.reliability.as_dict(),
                "asks": [a.as_dict() for a in self.asks],
                "concessions": [c.as_dict() for c in self.concessions],
                "cannot_say": list(self.cannot_say), "basis": self.basis,
                "explanation": self.explanation, "cloud": dict(self.cloud)}


def _settled(lines: list[dict]) -> list[dict]:
    return [l for l in lines if str(l.get("status")) in RCV.SETTLED]


def spend_by_supplier(lines: list[dict]) -> dict[str, dict]:
    """What each supplier was actually paid, and how much is unpriceable.

    A line with no unit cost contributes nothing, and the count of those is
    carried through. A spend figure quoted without it is a floor presented as a
    total, and the owner would open with a number lower than the truth.
    """
    out: dict[str, dict] = {}
    for l in _settled(lines):
        name = str(l.get("wholesaler") or "unknown")
        e = out.setdefault(name, {"spend": Decimal("0"), "lines": 0,
                                  "unpriced": 0, "molecules": set()})
        e["lines"] += 1
        e["molecules"].add(str(l.get("ndc11")))
        cost = l.get("unit_cost")
        got = q(l.get("quantity_received") or 0)
        if cost is None or got <= 0:
            e["unpriced"] += 1
            continue
        e["spend"] = e["spend"] + q(q(cost) * got)
    for e in out.values():
        e["spend"] = q(e["spend"])
    return out


def leverage(lines: list[dict], *, supplier: str) -> Leverage:
    """What this pharmacy is worth to this supplier."""
    per = spend_by_supplier(lines)
    mine = per.get(supplier, {"spend": Decimal("0"), "lines": 0, "unpriced": 0,
                              "molecules": set()})
    total = q(sum((e["spend"] for e in per.values()), Decimal("0")))
    share = q(mine["spend"] / total) if total > 0 else None
    hhi = (q(sum(((e["spend"] / total) ** 2 for e in per.values()),
                 Decimal("0"))) if total > 0 else None)

    if len(per) <= 1:
        standing = "sole"
    elif share is None:
        standing = "marginal"
    elif share >= DEPENDENT:
        standing = "principal"
    elif share >= CONCENTRATED:
        standing = "significant"
    else:
        standing = "marginal"

    reading = {
        "sole": ("the only supplier the pharmacy uses — there is no alternative "
                 "to point at, and asking as though there were invites the "
                 "obvious reply"),
        "principal": ("the pharmacy's principal supplier; losing this account "
                      "would be visible in their own numbers"),
        "significant": "a substantial share of the pharmacy's spend",
        "marginal": ("a small share of the pharmacy's spend — the leverage is "
                     "the volume that could move to them, not the volume they "
                     "already have"),
    }[standing]

    note = ""
    if mine["unpriced"]:
        note = (f" {mine['unpriced']} of {mine['lines']} line(s) carry no unit "
                f"cost, so this spend is a floor rather than a total.")

    return Leverage(
        supplier=supplier, spend=mine["spend"], share=share, lines=mine["lines"],
        molecules=len(mine["molecules"]), unpriced_lines=mine["unpriced"],
        concentration=hhi, standing=standing,
        explanation=(
            f"{mine['spend']} across {mine['lines']} delivered line(s) and "
            f"{len(mine['molecules'])} molecule(s)"
            + (f", {share:.0%} of measured spend" if share is not None else "")
            + f" — {reading}.{note}"))


def realised_costs(lines: list[dict]) -> dict[str, dict[str, Decimal]]:
    """Weighted realised unit cost per molecule per supplier.

    Weighted by units, not a mean of invoice prices: one small line at an odd
    price should not carry the same weight as the standing order.
    """
    acc: dict[str, dict[str, list[Decimal]]] = {}
    for l in _settled(lines):
        cost, got = l.get("unit_cost"), q(l.get("quantity_received") or 0)
        if cost is None or got <= 0:
            continue
        ndc = str(l.get("ndc11"))
        name = str(l.get("wholesaler") or "unknown")
        e = acc.setdefault(ndc, {}).setdefault(name, [Decimal("0"), Decimal("0")])
        e[0] += q(cost) * got
        e[1] += got
    return {ndc: {s: q(v[0] / v[1]) for s, v in sup.items() if v[1] > 0}
            for ndc, sup in acc.items()}


def term_gaps(lines: list[dict], *, supplier: str) -> list[TermGap]:
    """Molecules where somebody already charges this pharmacy less.

    The comparison is between two prices the pharmacy has paid. That is the only
    kind of benchmark available here, and the only kind that survives being
    repeated to the distributor.
    """
    costs = realised_costs(lines)
    volumes: dict[str, Decimal] = {}
    for l in _settled(lines):
        volumes[str(l.get("ndc11"))] = (
            volumes.get(str(l.get("ndc11")), Decimal("0"))
            + q(l.get("quantity_received") or 0))

    gaps = []
    for ndc, by_sup in costs.items():
        ours = by_sup.get(supplier)
        others = {s: c for s, c in by_sup.items() if s != supplier}
        if ours is None or not others:
            continue
        best_supplier = min(others, key=lambda s: others[s])
        best = others[best_supplier]
        if best >= ours:
            continue
        gap = q(ours - best)
        pct = q(gap / ours) if ours > 0 else Decimal("0")
        if pct < MEANINGFUL_GAP:
            continue
        units = volumes.get(ndc, Decimal("0"))
        gaps.append(TermGap(
            ndc11=ndc, our_cost=ours, best_cost=best,
            best_supplier=best_supplier, gap_per_unit=gap, gap_pct=pct,
            annual_units=units, annual_value=q(gap * units)))
    gaps.sort(key=lambda g: -g.annual_value)
    return gaps


def reliability_cost(lines: list[dict], *, supplier: str,
                     margins: dict[str, Decimal] | None = None) -> ReliabilityCost:
    """What this supplier's short deliveries cost in forgone margin.

    Units it failed to deliver, times the margin each would have earned. Where a
    molecule has no recorded sale price its units are counted and its money is
    not — quoting a guessed margin to a distributor is the same mistake as
    quoting a guessed benchmark, and this is the number the owner would lean on
    hardest.
    """
    margins = margins or {}
    mine = [l for l in _settled(lines)
            if str(l.get("wholesaler") or "unknown") == supplier]
    missing = Decimal("0")
    priced = Decimal("0")
    money = Decimal("0")
    for l in mine:
        gap = q(q(l.get("quantity_ordered") or 0) - q(l.get("quantity_received") or 0))
        if gap <= 0:
            continue
        missing += gap
        m = margins.get(str(l.get("ndc11")))
        if m is None:
            continue
        priced += gap
        money += q(gap * m)

    if missing <= 0:
        return ReliabilityCost(
            undelivered_units=Decimal("0.000"), priced_units=Decimal("0.000"),
            forgone_margin=Decimal("0.000"), basis="observed",
            explanation=("Every unit ordered from this supplier was delivered. "
                         "There is no service-level argument to make."))
    if priced <= 0:
        return ReliabilityCost(
            undelivered_units=q(missing), priced_units=Decimal("0.000"),
            forgone_margin=None, basis="unpriced",
            explanation=(
                f"{q(missing)} unit(s) ordered and never delivered. No sale "
                f"price is recorded for any of them, so the cost cannot be put "
                f"in money — and a guessed margin is not worth saying out loud "
                f"to someone who sells these for a living."))
    basis = "observed" if priced >= missing else "partly_priced"
    caveat = ("" if basis == "observed" else
              f" Only {priced} of those units have a recorded sale price, so "
              f"this is a floor.")
    return ReliabilityCost(
        undelivered_units=q(missing), priced_units=q(priced),
        forgone_margin=q(money), basis=basis,
        explanation=(f"{q(missing)} unit(s) ordered and never delivered, "
                     f"{q(money)} of margin forgone.{caveat}"))


def brief(lines: list[dict], *, supplier: str,
          margins: dict[str, Decimal] | None = None,
          safety_stock: dict[str, Decimal] | None = None,
          months_of_history: int | None = None) -> Brief:
    """Everything worth knowing before the conversation, and what is not known."""
    lev = leverage(lines, supplier=supplier)
    gaps = term_gaps(lines, supplier=supplier)
    rel = reliability_cost(lines, supplier=supplier, margins=margins)
    negotiable = q(sum((g.annual_value for g in gaps), Decimal("0")))

    per = spend_by_supplier(lines)
    suppliers = len(per)
    thin = per.get(supplier, {}).get("lines", 0) < MIN_LINES_PER_SUPPLIER

    cannot_say: list[str] = []
    if suppliers < MIN_SUPPLIERS_TO_COMPARE:
        cannot_say.append(
            "terms cannot be compared: this pharmacy buys from one supplier, so "
            "there is no price it is already paying that is lower. No market "
            "rate is available and none is invented")
    if thin:
        cannot_say.append(
            f"fewer than {MIN_LINES_PER_SUPPLIER} delivered lines from this "
            f"supplier — a realised price from this is one invoice with a "
            f"rounding error on it")
    if months_of_history is not None and months_of_history < 6:
        cannot_say.append(
            f"{months_of_history} month(s) of purchase history; annual volumes "
            f"are extrapolations, not measurements")
    cannot_say.append(
        "payment terms, credit periods and freight are not recorded anywhere in "
        "this system, so nothing here prices a longer payment window")
    cannot_say.append(
        "no cross-pharmacy benchmark exists — that needs more than one pharmacy "
        "on the platform, and until then this brief is entirely this pharmacy's "
        "own numbers")

    asks: list[Ask] = []
    # Strongest first, and each carries a number the distributor can check.
    if gaps:
        top = ", ".join(g.ndc11 for g in gaps[:3])
        asks.append(Ask(
            ask=f"match {gaps[0].best_supplier}'s price on {len(gaps)} molecule(s)",
            worth=negotiable, basis="observed",
            explanation=(
                f"This pharmacy already pays less elsewhere for {len(gaps)} of "
                f"the molecules it buys here ({top}…). Worth {negotiable} a year "
                f"at current volumes. Every figure is an invoice they can be "
                f"shown.")))
    if rel.forgone_margin and rel.forgone_margin > 0:
        asks.append(Ask(
            ask="a service-level commitment on fill rate",
            worth=rel.forgone_margin, basis=rel.basis,
            explanation=(f"Short deliveries cost {rel.forgone_margin} in margin "
                         f"that was never earned. That is the pharmacy's loss "
                         f"from their operations, not a matter of opinion.")))
    elif rel.undelivered_units > 0:
        asks.append(Ask(
            ask="a service-level commitment on fill rate",
            worth=None, basis="unpriced",
            explanation=(f"{rel.undelivered_units} unit(s) never arrived. The "
                         f"money cannot be quantified from what is recorded, so "
                         f"take the unit count and not a figure.")))
    if lev.share is not None and lev.share >= CONCENTRATED:
        asks.append(Ask(
            ask="a volume discount at this share of spend",
            worth=lev.spend, basis="observed",
            explanation=(f"They hold {lev.share:.0%} of measured spend "
                         f"({lev.spend}). That concentration is the argument, "
                         f"and it is also the pharmacy's own risk.")))

    concessions: list[Ask] = []
    # What the pharmacy can offer, priced only where a real number exists.
    if safety_stock:
        carried = q(sum((v for v in safety_stock.values()), Decimal("0")))
        concessions.append(Ask(
            ask="fewer, larger deliveries",
            worth=carried, basis="observed",
            explanation=(f"Cheap to give: the pharmacy already carries "
                         f"{carried} unit(s) of safety stock against their "
                         f"delivery variability. Fewer runs saves them money "
                         f"and changes little here — but only while the fill "
                         f"rate holds, so pair it with the service-level ask.")))
    concessions.append(Ask(
        ask="a longer payment window",
        worth=None, basis="unpriced",
        explanation=("Often the cheapest thing to trade, but this system records "
                     "no payment terms and no cost of capital, so what it is "
                     "worth to either side is genuinely unknown here.")))

    basis = "observed" if (gaps or rel.basis != "unpriced") and not thin \
        else "volume_only"
    return Brief(
        supplier=supplier, leverage=lev, gaps=gaps, negotiable_annual=negotiable,
        reliability=rel, asks=asks, concessions=concessions,
        cannot_say=cannot_say, basis=basis,
        explanation=(
            f"{lev.explanation} "
            + (f"{len(gaps)} molecule(s) are cheaper elsewhere, worth "
               f"{negotiable} a year. " if gaps else
               "No molecule bought here is cheaper from another supplier the "
               "pharmacy already uses. ")
            + rel.explanation),
        cloud={"available": False,
               "reason": ("cross-pharmacy benchmarks need more than one pharmacy "
                          "on the platform")})
