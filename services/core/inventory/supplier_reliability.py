"""E12 — which supplier can actually be relied on, and for what.

Now that a delivery closes its order (`receiving.py`), three questions have
answers for the first time: how much of what was ordered arrived, how long it
took, and how much that time varies. This turns them into one comparable
picture per supplier.

**Slow is not the same as unreliable, and only one of them is a fault here.**
A supplier that takes eleven days every single time is not a problem: eleven
days goes into the reorder point (`lead_time.reorder_signals`) and the shelf is
covered. A supplier averaging four days that occasionally takes fifteen is the
expensive one, because nothing can be planned around it. Lead-time *duration* is
therefore reported but deliberately **not scored** — it is already priced into
the reorder point, and scoring it here would punish a supplier twice for
something the planner has handled. What is scored is how much arrives and how
predictably it arrives.

    score = 0.6 × fill rate + 0.4 × consistency

**An unmeasured supplier must not win.** The failure mode this engine exists to
avoid is the new supplier with two orders topping the table on a perfect record,
and the pharmacy moving its business to it. A composite is only as measured as
its weakest input: if either component is unmeasured the score is withheld
entirely, and a withheld score sorts last rather than first.

**Two suppliers can only be compared over what they both sell.** Ranking a
supplier who carries the difficult items against one who carries the easy ones
is a comparison of catalogues, not of reliability, so `comparable()` refuses a
head-to-head where the baskets barely overlap.

Pure functions over already-fetched rows.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from . import lead_time as LT
from . import receiving as RCV
from .ledger import q

# What is scored, and why the split is 60/40: a short delivery is an immediate
# stockout risk with a known cost, while an erratic lead time is a cost carried
# as extra safety stock. The first is worse, but not overwhelmingly so.
FILL_WEIGHT = Decimal("0.6")
CONSISTENCY_WEIGHT = Decimal("0.4")

# Above this share of lines closing short, the pharmacy is chasing a quarter of
# its own orders — a staffing cost that the volume-weighted fill rate hides,
# because one large line filled in full offsets many small ones that were not.
SHORT_LINE_CONCERN = Decimal("0.25")

# Coefficient of variation above which a lead time cannot be planned around.
ERRATIC_CV = Decimal("0.5")

# Enough deliveries to compute a spread is not the same as enough to award a
# supplier a grade. Below this the score is reported and the grade withheld.
PROVISIONAL_BELOW_ORDERS = 6

# Below this many products in common, a head-to-head is a comparison of
# catalogues rather than of suppliers.
MIN_SHARED_PRODUCTS = 3

GRADES = (
    (Decimal("0.90"), "dependable"),
    (Decimal("0.75"), "workable"),
    (Decimal("0.60"), "mixed"),
    (Decimal("0.00"), "poor"),
)

# Consistency cannot rescue a supplier that does not deliver. A supplier filling
# 40% of every order with metronomic regularity scores 0.64 on the blend alone —
# "mixed", which is far too kind to something that fails more often than it
# succeeds. Being reliably absent is not a virtue, so the grade is also capped
# by what actually arrived.
GRADE_FILL_CEILING = (
    (Decimal("0.95"), "dependable"),
    (Decimal("0.85"), "workable"),
    (Decimal("0.70"), "mixed"),
    (Decimal("0.00"), "poor"),
)
_SEVERITY = ("dependable", "workable", "mixed", "poor")


@dataclass(frozen=True)
class SupplierScore:
    supplier: str
    orders: int
    lines: int

    fill_rate: Decimal | None
    fill_basis: str
    short_lines: int
    short_line_rate: Decimal | None
    outstanding_lines: int

    lead_days: int
    lead_basis: str
    lead_stdev: float | None
    consistency: Decimal | None          # 1 − CV, floored at zero

    score: Decimal | None
    basis: str                           # observed | provisional | insufficient_history
    grade: str | None
    substitutions: int
    substitution_basis: str
    concerns: list[str] = field(default_factory=list)
    products: frozenset[str] = frozenset()
    explanation: str = ""

    def as_dict(self) -> dict:
        return {
            "supplier": self.supplier, "orders": self.orders, "lines": self.lines,
            "fill_rate": None if self.fill_rate is None else float(self.fill_rate),
            "fill_basis": self.fill_basis,
            "short_lines": self.short_lines,
            "short_line_rate": None if self.short_line_rate is None
                               else float(self.short_line_rate),
            "outstanding_lines": self.outstanding_lines,
            "lead_days": self.lead_days, "lead_basis": self.lead_basis,
            "lead_stdev": self.lead_stdev,
            "consistency": None if self.consistency is None else float(self.consistency),
            "score": None if self.score is None else float(self.score),
            "basis": self.basis, "grade": self.grade,
            "substitutions": self.substitutions,
            "substitution_basis": self.substitution_basis,
            "concerns": list(self.concerns),
            "products": len(self.products),
            "explanation": self.explanation,
        }


def _consistency(lead: LT.LeadTime) -> tuple[Decimal | None, Decimal | None]:
    """How predictable this supplier's delivery time is, and its raw spread.

    Expressed as 1 − CV so it reads the same direction as the fill rate: higher
    is better. A supplier whose deliveries never vary scores 1.0 however slow it
    is, which is the whole point — its slowness is already in the reorder point.
    """
    # One delivery has a standard deviation of zero, which would read as perfect
    # predictability and hand a brand-new supplier the top of the table — the
    # exact failure this engine exists to avoid. Consistency is a property of a
    # distribution, so it requires enough deliveries to have one.
    if lead.basis != "observed" or lead.median_days in (None, 0):
        return None, None
    sd = Decimal(str(lead.stdev_days or 0))
    cv = q(sd / Decimal(str(lead.median_days)))
    return max(Decimal("0.000"), q(Decimal("1") - cv)), cv


# Short deliveries left open below this count are ordinary housekeeping; a
# supplier is only stalled when they outnumber what it has actually settled.
MIN_STALLED_LINES = 3


def _stalled(outstanding: int, settled: int) -> bool:
    """Whether this supplier's short deliveries are piling up unclosed."""
    return outstanding >= MIN_STALLED_LINES and outstanding >= settled


def _band(value: Decimal, ladder) -> str:
    for floor, name in ladder:
        if value >= floor:
            return name
    return "poor"


def _grade(score: Decimal, fill: Decimal) -> str:
    """The worse of what the blend says and what the fill rate permits."""
    a, b = _band(score, GRADES), _band(fill, GRADE_FILL_CEILING)
    return max(a, b, key=_SEVERITY.index)


def score_supplier(orders: list[dict], lines: list[dict], *,
                   supplier: str) -> SupplierScore:
    """One supplier's record, from its delivered orders and their lines."""
    lead = LT.estimate(orders, supplier=supplier)
    fill = RCV.fill_rate(lines, supplier=supplier)
    consistency, cv = _consistency(lead)

    closed = [l for l in lines if str(l.get("status")) in RCV.SETTLED]
    short = [l for l in closed
             if q(l.get("quantity_received") or 0) < q(l.get("quantity_ordered") or 0)]
    short_rate = q(Decimal(len(short)) / Decimal(len(closed))) if closed else None
    # Lines where a delivery *arrived short* and the order was never closed out.
    # Deliberately not lines still in transit: an order placed yesterday has not
    # failed to arrive, and counting it would nag about every open order.
    outstanding = [l for l in lines if str(l.get("status")) == "partial"]

    subs = len([l for l in lines if str(l.get("status")) == "substituted"])
    sub_basis = "observed" if subs else "not_captured"

    concerns: list[str] = []
    if short_rate is not None and short_rate > SHORT_LINE_CONCERN:
        concerns.append(
            f"{short_rate:.0%} of lines closed short — the volume fill rate "
            f"hides this, because one large line filled in full offsets many "
            f"small ones that were not")
    if cv is not None and cv > ERRATIC_CV:
        concerns.append(
            f"delivery time varies by {cv:.0%} of its own median — this is the "
            f"cost that is carried as safety stock rather than seen")
    if sub_basis == "not_captured":
        concerns.append(
            "substitutions are not recorded by any receiving path, so zero here "
            "means unmeasured, not never")
    if _stalled(len(outstanding), len(closed)):
        concerns.append(
            f"{len(outstanding)} line(s) delivered short and never closed out — "
            f"until someone records that the rest is not coming, none of this "
            f"supplier's orders complete, it acquires no lead time, and it "
            f"cannot be told apart from a supplier that never delivered at all")

    # A composite is only as measured as its weakest input. Unmeasured must not
    # look like unblemished.
    if fill.rate is None or consistency is None:
        missing = []
        if fill.rate is None:
            missing.append(f"fill rate ({fill.lines} delivered lines)")
        if consistency is None:
            missing.append(
                f"lead-time spread ({lead.samples} delivered order(s); a "
                f"standard deviation needs a distribution, and one delivery "
                f"has none)")
        return SupplierScore(
            supplier=supplier, orders=lead.samples, lines=len(closed),
            fill_rate=fill.rate, fill_basis=fill.basis, short_lines=len(short),
            short_line_rate=short_rate, outstanding_lines=len(outstanding),
            lead_days=lead.days,
            lead_basis=lead.basis, lead_stdev=lead.stdev_days,
            consistency=consistency, score=None, basis="insufficient_history",
            grade=None, substitutions=subs, substitution_basis=sub_basis,
            concerns=concerns,
            products=frozenset(str(l.get("ndc11")) for l in lines if l.get("ndc11")),
            explanation=(
                f"Not enough history to score {supplier} — missing "
                f"{' and '.join(missing)}. Scoring it now would put an "
                f"unmeasured supplier at the top of the table."))

    score = q(FILL_WEIGHT * fill.rate + CONSISTENCY_WEIGHT * consistency)
    # Three deliveries is enough for a spread; it is not enough to award a
    # supplier a standing. The score is shown because it is real, the grade is
    # withheld because a label outlives the caveat printed beside it.
    provisional = lead.samples < PROVISIONAL_BELOW_ORDERS
    basis = "provisional" if provisional else "observed"
    return SupplierScore(
        supplier=supplier, orders=lead.samples, lines=len(closed),
        fill_rate=fill.rate, fill_basis=fill.basis, short_lines=len(short),
        short_line_rate=short_rate, outstanding_lines=len(outstanding),
        lead_days=lead.days, lead_basis=lead.basis,
        lead_stdev=lead.stdev_days, consistency=consistency, score=score,
        basis=basis,
        grade=None if provisional else _grade(score, fill.rate),
        substitutions=subs, substitution_basis=sub_basis, concerns=concerns,
        products=frozenset(str(l.get("ndc11")) for l in lines if l.get("ndc11")),
        explanation=(
            f"{fill.rate:.0%} of ordered units delivered across {len(closed)} "
            f"lines; {lead.days} days typical, consistency {consistency:.2f}"
            + (f". Based on {lead.samples} delivered order(s) — provisional."
               if provisional else ".")))


def rank(orders: list[dict], lines: list[dict]) -> list[SupplierScore]:
    """Every supplier, best first, with the unmeasured ones last.

    Sorting a `None` score to the bottom is the point: an unranked supplier is
    an unknown quantity, and an unknown quantity at the top of a table gets
    read as a recommendation.
    """
    by_sup: dict[str, list[dict]] = {}
    for o in orders:
        by_sup.setdefault(str(o.get("wholesaler") or "unknown"), []).append(o)
    lines_by_sup: dict[str, list[dict]] = {}
    for l in lines:
        lines_by_sup.setdefault(str(l.get("wholesaler") or "unknown"), []).append(l)

    names = sorted(set(by_sup) | set(lines_by_sup))
    scored = [score_supplier(by_sup.get(n, []), lines_by_sup.get(n, []), supplier=n)
              for n in names]
    return sorted(scored, key=lambda s: (s.score is None,
                                         -(s.score or Decimal("0")), s.supplier))


@dataclass(frozen=True)
class Comparison:
    a: str
    b: str
    shared_products: int
    verdict: str            # a | b | too_close | not_comparable
    explanation: str

    def as_dict(self) -> dict:
        return {"a": self.a, "b": self.b, "shared_products": self.shared_products,
                "verdict": self.verdict, "explanation": self.explanation}


# Two scores closer than this are not distinguishable from the noise in a few
# dozen deliveries, and presenting one as the winner invites a switch of
# supplier that the evidence does not support.
MEANINGFUL_GAP = Decimal("0.05")


def comparable(a: SupplierScore, b: SupplierScore) -> Comparison:
    """A head-to-head, or an honest refusal to hold one.

    Suppliers who barely stock the same products cannot be compared on fill
    rate: the one carrying the scarce items will always look worse, and the
    difference measures the catalogue rather than the supplier.
    """
    shared = len(a.products & b.products)
    if a.score is None or b.score is None:
        unscored = a.supplier if a.score is None else b.supplier
        return Comparison(a.supplier, b.supplier, shared, "not_comparable",
                          f"{unscored} has no measured record yet.")
    if shared < MIN_SHARED_PRODUCTS:
        return Comparison(
            a.supplier, b.supplier, shared, "not_comparable",
            f"only {shared} product(s) in common — this would compare "
            f"catalogues, not suppliers.")
    gap = a.score - b.score
    if abs(gap) < MEANINGFUL_GAP:
        return Comparison(
            a.supplier, b.supplier, shared, "too_close",
            f"{a.score:.2f} against {b.score:.2f} over {shared} shared products "
            f"— inside the noise, so neither is the better choice on this "
            f"evidence.")
    winner, loser = (a, b) if gap > 0 else (b, a)
    return Comparison(
        a.supplier, b.supplier, shared, winner.supplier,
        f"{winner.supplier} {winner.score:.2f} against {loser.supplier} "
        f"{loser.score:.2f} over {shared} shared products.")


def worth_raising(s: SupplierScore) -> bool:
    """Whether this supplier's record needs a decision from someone.

    An insufficient history is not a finding — every supplier starts there, and
    filing it would bury the ones that have earned a look.
    """
    # A supplier whose short deliveries were never closed out needs a decision
    # *before* it needs a grade. Until someone records that the rest is not
    # coming, none of its orders complete and it cannot be measured at all —
    # so waiting for a score would mean never raising the worst case.
    if _stalled(s.outstanding_lines, s.lines):
        return True
    if s.basis != "observed":
        return False
    return (s.grade in ("mixed", "poor")
            or (s.short_line_rate is not None
                and s.short_line_rate > SHORT_LINE_CONCERN))
