"""E13 — which drugs are about to become unobtainable, and whose fault it is.

A rebuild of service ⑰ (`supply_warning`), which has never produced a real
number because the columns it reads were never written. Now that they are, the
rebuild can also fix what was wrong with it on the merits:

  * `fill_rate = 1.0` when nothing had been ordered — absence of evidence scored
    as a perfect record, the same trap E12 exists to avoid;
  * `COALESCE(avg_daily_demand, 0)` — a NULL demand rate became zero, which is
    the fallback constant the provenance rule was written to forbid;
  * a *third* hard-coded lead time (5 days) disagreeing with the 7 in
    `lead_time.DECLARED_DEFAULT_DAYS`, unlabelled;
  * every ordered line counted, so an order placed yesterday and not yet
    delivered read as a total short-fill.

**The distinction that makes this worth building.** A short fill is not a
shortage. If a molecule short-fills from *every* supplier that carries it, the
market is out and the remedies are stock, substitution and warning prescribers.
If it short-fills from one supplier while another delivers it in full, the
molecule is available and the remedy is to move the order — a completely
different action, and one that costs nothing. Service ⑰ scored both the same and
recommended buffer stock for both, which means paying to hold inventory against
a problem a phone call would solve.

So the verdicts are about *cause*, not severity:

    market_shortage     short from every supplier that carries it
    supplier_shortage   short from one, filled by another
    thin_cover          arriving fine, but not enough of it on the shelf
    watch               a declining trend that has not yet bitten
    no_signal           nothing to say
    unknown             too little settled history to say anything

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import lead_time as LT
from . import receiving as RCV
from .ledger import q

# Below this many settled lines for a molecule, there is no fill-rate history —
# only a couple of deliveries that happen to have a ratio. `unknown`, not
# reassurance.
MIN_SETTLED_LINES = 3

# A fill rate under this is a short-filling supplier. Deliberately not 1.0:
# whole packs do not always split evenly and a 2% gap is arithmetic.
SHORT_FILL = Decimal("0.95")

# And this is what "another supplier filled it in full" means. The gap between
# the two thresholds is intentional: a molecule sitting between them is not
# clearly available *or* clearly scarce, and the verdict says so rather than
# picking the more dramatic reading.
FULL_FILL = Decimal("0.98")

# Cover the pharmacy wants beyond the lead time itself, so a shortage that
# starts today is noticed before the shelf is bare rather than after.
BUFFER_DAYS = 14

# One-sided CUSUM on the fill-rate sequence, to catch a slow downward creep that
# no single delivery would trigger. This is the part of ⑰ worth keeping: a
# supplier rationing a molecule rarely refuses an order outright, it trims each
# one, and a trailing average absorbs that.
#
# The alarm is calibrated at four consecutive deliveries running ten points below
# target — the smallest pattern that is not noise. ⑰ used 0.5, which needs
# fifteen such deliveries; by then the aggregate rate has long since fallen
# through `SHORT_FILL` and the item is flagged on that instead, so the creep
# detector never fired and the verdict it feeds was unreachable. Where it earns
# its place is a molecule with a long clean history and a recent decline: the
# aggregate stays above the threshold precisely because the past was good.
CUSUM_TARGET = Decimal("0.95")
CUSUM_SLACK = Decimal("0.05")
CUSUM_ALARM = Decimal("0.20")

# `seek_alternative` was here and no code path could produce it. Proposing a
# therapeutic substitute needs an equivalence source this platform does not
# have, and a declared action the engine can never reach is the same kind of
# promise as a fabricated number — so it is gone rather than aspirational.
# `alert_prescribers` is what a market shortage with no cover actually gets.
ACTIONS = ("buffer_stock", "switch_supplier", "alert_prescribers", "reorder",
           "watch", "none")
VERDICTS = ("market_shortage", "supplier_shortage", "thin_cover", "watch",
            "no_signal", "unknown")


@dataclass(frozen=True)
class SupplierFill:
    """How one supplier has served this molecule."""
    supplier: str
    lines: int
    ordered: Decimal
    received: Decimal
    rate: Decimal | None
    basis: str

    def as_dict(self) -> dict:
        return {"supplier": self.supplier, "lines": self.lines,
                "ordered": float(self.ordered), "received": float(self.received),
                "rate": None if self.rate is None else float(self.rate),
                "basis": self.basis}


@dataclass(frozen=True)
class ShortageSignal:
    ndc11: str
    drug_name: str | None
    verdict: str
    action: str
    severity: str                    # critical | high | medium | info

    fill_rate: Decimal | None        # across all suppliers, settled lines only
    basis: str                       # observed | insufficient_history
    settled_lines: int
    by_supplier: list[SupplierFill]
    short_suppliers: list[str]
    filling_suppliers: list[str]

    creep: Decimal | None            # accumulated downward deviation
    on_hand: Decimal
    days_of_cover: Decimal | None
    demand_basis: str
    lead_days: int
    lead_basis: str
    horizon_days: int                # lead time + buffer
    suggested_buffer: Decimal | None

    explanation: str
    concerns: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "ndc11": self.ndc11, "drug_name": self.drug_name,
            "verdict": self.verdict, "action": self.action,
            "severity": self.severity,
            "fill_rate": None if self.fill_rate is None else float(self.fill_rate),
            "basis": self.basis, "settled_lines": self.settled_lines,
            "by_supplier": [s.as_dict() for s in self.by_supplier],
            "short_suppliers": list(self.short_suppliers),
            "filling_suppliers": list(self.filling_suppliers),
            "creep": None if self.creep is None else float(self.creep),
            "on_hand": float(self.on_hand),
            "days_of_cover": None if self.days_of_cover is None
                             else float(self.days_of_cover),
            "demand_basis": self.demand_basis,
            "lead_days": self.lead_days, "lead_basis": self.lead_basis,
            "horizon_days": self.horizon_days,
            "suggested_buffer": None if self.suggested_buffer is None
                                else float(self.suggested_buffer),
            "explanation": self.explanation, "concerns": list(self.concerns),
        }


def downward_creep(rates: list[Decimal]) -> Decimal | None:
    """How far this molecule's fill rate has run below target, cumulatively.

    None below three deliveries: a CUSUM over two points is the second point.
    """
    if len(rates) < 3:
        return None
    run = Decimal("0")
    peak = Decimal("0")
    for r in rates:
        run = max(Decimal("0"), run + (CUSUM_TARGET - r) - CUSUM_SLACK)
        peak = max(peak, run)
    return q(peak)


def _settled(lines: list[dict]) -> list[dict]:
    return [l for l in lines if str(l.get("status")) in RCV.SETTLED]


def _rate(ordered: Decimal, received: Decimal) -> Decimal | None:
    """Fill rate, or None when nothing was ordered.

    Not 1.0. A molecule nobody has ordered has no fill rate, and the version of
    this that returned 1.0 reported a flawless record for every drug the
    pharmacy had never bought.
    """
    if ordered <= 0:
        return None
    return q(min(received, ordered) / ordered)


def _by_supplier(lines: list[dict]) -> list[SupplierFill]:
    grouped: dict[str, list[dict]] = {}
    for l in lines:
        grouped.setdefault(str(l.get("wholesaler") or "unknown"), []).append(l)
    out = []
    for name, rows in sorted(grouped.items()):
        ordered = q(sum((q(r.get("quantity_ordered") or 0) for r in rows),
                        Decimal("0")))
        received = q(sum((min(q(r.get("quantity_received") or 0),
                              q(r.get("quantity_ordered") or 0))
                          for r in rows), Decimal("0")))
        rate = _rate(ordered, received)
        out.append(SupplierFill(
            supplier=name, lines=len(rows), ordered=ordered, received=received,
            rate=rate,
            basis="observed" if rate is not None else "insufficient_history"))
    return out


def assess_item(item: dict, *, leads: dict[str, LT.LeadTime] | None = None,
                as_of: date | None = None) -> ShortageSignal:
    """One molecule: is it running out, and is that the market or a supplier?"""
    leads = leads or {}
    ndc = str(item.get("ndc11"))
    lines = _settled(item.get("lines") or [])
    fills = _by_supplier(lines)

    ordered = q(sum((f.ordered for f in fills), Decimal("0")))
    received = q(sum((f.received for f in fills), Decimal("0")))
    overall = _rate(ordered, received)

    short = [f.supplier for f in fills
             if f.rate is not None and f.rate < SHORT_FILL]
    filling = [f.supplier for f in fills
               if f.rate is not None and f.rate >= FULL_FILL]

    # Lead time from whoever is actually filling it; if nobody is, from whoever
    # was asked. E11's per-supplier estimate, never a constant of our own.
    who = (filling or short or [f.supplier for f in fills] or [""])[0]
    lead = leads.get(who) or LT.declared_default(who or None)
    horizon = lead.days + BUFFER_DAYS

    on_hand = q(item.get("on_hand") or 0)
    demand_basis = str(item.get("demand_basis") or "no_history")
    adq = item.get("avg_daily_demand")
    # No measured demand means the cover is unknown. Writing zero — which is
    # what ⑰ did through COALESCE — turns "we do not know" into "it will last
    # for ever", and the item silently drops off every shortage report.
    cover = (q(on_hand / q(adq))
             if adq not in (None, 0) and demand_basis in ("observed", "sparse")
             else None)

    rates = [r for r in (_rate(q(l.get("quantity_ordered") or 0),
                               q(l.get("quantity_received") or 0))
                         for l in sorted(lines, key=lambda x: (
                             x.get("ordered_at") is None, x.get("ordered_at"))))
             if r is not None]
    creep = downward_creep(rates)

    concerns: list[str] = list(item.get("extra_concerns") or [])
    if demand_basis not in ("observed", "sparse"):
        concerns.append(
            "no measured demand for this item, so days of cover cannot be "
            "computed — it is reported as unknown rather than as plenty")
    if lead.basis == "declared_default":
        concerns.append(
            f"no delivered orders from {who or 'any supplier'}, so the "
            f"{lead.days}-day horizon is a declared assumption")

    if overall is None or len(lines) < MIN_SETTLED_LINES:
        return ShortageSignal(
            ndc11=ndc, drug_name=item.get("drug_name"), verdict="unknown",
            action="none", severity="info", fill_rate=overall,
            basis="insufficient_history", settled_lines=len(lines),
            by_supplier=fills, short_suppliers=short, filling_suppliers=filling,
            creep=creep, on_hand=on_hand, days_of_cover=cover,
            demand_basis=demand_basis, lead_days=lead.days,
            lead_basis=lead.basis, horizon_days=horizon, suggested_buffer=None,
            concerns=concerns,
            explanation=(
                f"{len(lines)} settled order line(s) for {ndc} — too few to "
                f"tell a shortage from an ordinary delivery. No risk is scored, "
                f"because a number from this would be a guess wearing a "
                f"decimal point."))

    thin = cover is not None and cover < horizon
    buffer = (q(q(adq) * Decimal(horizon) - on_hand)
              if thin and adq else None)
    if buffer is not None and buffer <= 0:
        buffer = None

    if short and not filling:
        # Every supplier that carries it is trimming orders. The molecule, not
        # the relationship, is the problem.
        verdict = "market_shortage"
        action = "alert_prescribers" if thin else "buffer_stock"
        severity = "critical" if thin else "high"
        why = (f"{', '.join(short)} short-filled {ndc} and no supplier filled "
               f"it — this is the market, not the relationship.")
    elif short and filling:
        # Available elsewhere. Moving the order costs nothing; buffering costs
        # money and shelf life.
        verdict = "supplier_shortage"
        action = "switch_supplier"
        severity = "medium"
        why = (f"{', '.join(short)} short-filled {ndc} while "
               f"{', '.join(filling)} delivered it in full — the molecule is "
               f"available, so move the order rather than buy cover.")
    elif thin:
        verdict = "thin_cover"
        action = "reorder"
        severity = "high" if cover is not None and cover < lead.days else "medium"
        why = (f"{ndc} arrives reliably but there is {cover} day(s) of cover "
               f"against a {horizon}-day horizon.")
    elif creep is not None and creep >= CUSUM_ALARM:
        verdict = "watch"
        action = "watch"
        severity = "medium"
        why = (f"{ndc} is filled on average but each order is being trimmed — "
               f"cumulative shortfall {creep} against a {CUSUM_TARGET} target. "
               f"A trailing average hides this.")
    else:
        verdict = "no_signal"
        action = "none"
        severity = "info"
        why = (f"{ndc}: {overall:.0%} filled across {len(lines)} settled lines, "
               f"{'cover unknown' if cover is None else f'{cover} days of cover'}"
               f" against a {horizon}-day horizon.")

    return ShortageSignal(
        ndc11=ndc, drug_name=item.get("drug_name"), verdict=verdict,
        action=action, severity=severity, fill_rate=overall, basis="observed",
        settled_lines=len(lines), by_supplier=fills, short_suppliers=short,
        filling_suppliers=filling, creep=creep, on_hand=on_hand,
        days_of_cover=cover, demand_basis=demand_basis, lead_days=lead.days,
        lead_basis=lead.basis, horizon_days=horizon, suggested_buffer=buffer,
        explanation=why, concerns=concerns)


@dataclass(frozen=True)
class ShortageReport:
    signals: list[ShortageSignal]
    market: int
    supplier_side: int
    unknown: int
    explanation: str

    def as_dict(self) -> dict:
        return {"signals": [s.as_dict() for s in self.signals],
                "market_shortages": self.market,
                "supplier_shortages": self.supplier_side,
                "unknown": self.unknown, "explanation": self.explanation}


_ORDER = {"critical": 0, "high": 1, "medium": 2, "info": 3}


def assess(items: list[dict], *, leads: dict[str, LT.LeadTime] | None = None,
           as_of: date | None = None) -> ShortageReport:
    signals = [assess_item(i, leads=leads, as_of=as_of) for i in items]
    signals.sort(key=lambda s: (_ORDER.get(s.severity, 9), s.ndc11))
    market = sum(1 for s in signals if s.verdict == "market_shortage")
    supplier_side = sum(1 for s in signals if s.verdict == "supplier_shortage")
    unknown = sum(1 for s in signals if s.verdict == "unknown")
    return ShortageReport(
        signals=signals, market=market, supplier_side=supplier_side,
        unknown=unknown,
        explanation=(
            f"{market} molecule(s) short from every supplier that carries them, "
            f"{supplier_side} short from one supplier but available from "
            f"another, {unknown} without enough settled history to judge."))


def worth_raising(s: ShortageSignal) -> bool:
    """Whether this needs a decision.

    `unknown` never is. Every molecule starts there, and filing it would bury
    the ones that are genuinely running out.
    """
    return s.verdict in ("market_shortage", "supplier_shortage", "thin_cover")
