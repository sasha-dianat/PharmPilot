"""Is this delivery unlike every other one — and unlike the national price?

Engine E2. The check that can be honest on day one, because it does not need
this pharmacy's history: 36,612 rows of the national formulary carry an
announced price, so a receipt costing forty times the published figure is
catchable before the pharmacy has dispensed anything at all.

Goods receipt is where wrong numbers enter inventory. Everything downstream —
valuation, ABC class, days of supply, the reorder point — is computed from what
was keyed in at the bench, and a mistyped cost or a pack counted as units is
invisible from that moment on. `check_unit_conversion` finds the 30x error
*after* the shelf figure is already wrong and has already driven a purchase.
This finds it at the door.

Four checks, each declaring its own basis, because they do not all become
available at the same time:

  * **against the formulary** — works now, needs no local history
  * **against this item's own price history** — needs prior receipts
  * **against this item's own quantity history** — needs prior receipts
  * **shelf life on arrival** — works now

Robust statistics throughout: median and MAD, not mean and standard deviation.
One bad receipt is exactly what this is looking for, and a mean poisoned by the
outlier stops the next one being detected — the failure mode where a detector
quietly trains itself to accept the thing it exists to catch.

Pure functions over already-fetched rows; `as_of` is always injected.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import q

# A cost this far from the reference is worth a human look. Wide on purpose:
# real acquisition cost sits well under the announced retail price, discounts
# vary, and a band that fires on every ordinary delivery gets switched off.
CHEAP_RATIO = Decimal("0.05")     # under 5% of announced — probably a pack/unit slip
DEAR_RATIO = Decimal("1.20")      # above the announced retail price — never normal

# Robust z-score threshold. 3.5 on a MAD scale is the conventional cut for
# "this did not come from the same distribution as the others".
MAD_THRESHOLD = Decimal("3.5")
MIN_HISTORY = 3                   # below this there is no distribution to test

# Stock arriving with less life than this has to be sold almost immediately.
SHORT_SHELF_LIFE_DAYS = 90

SEVERITIES = ("critical", "high", "medium", "info")
BASES = ("formulary", "history", "arrival", "insufficient_history", "no_reference")


@dataclass
class Finding:
    check: str
    severity: str
    basis: str
    detail: str
    observed: Decimal | None = None
    reference: Decimal | None = None
    remediation: str = ""

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity,
                "basis": self.basis, "detail": self.detail,
                "observed": None if self.observed is None else float(self.observed),
                "reference": None if self.reference is None else float(self.reference),
                "remediation": self.remediation}


@dataclass
class Verdict:
    ndc11: str
    lot_number: str
    findings: list[Finding] = field(default_factory=list)

    @property
    def worst(self) -> str:
        for s in SEVERITIES:
            if any(f.severity == s for f in self.findings):
                return s
        return "info"

    @property
    def clean(self) -> bool:
        return not self.findings

    def as_dict(self) -> dict:
        return {"ndc11": self.ndc11, "lot_number": self.lot_number,
                "severity": self.worst, "clean": self.clean,
                "findings": [f.as_dict() for f in self.findings]}


def _median(values: list[Decimal]) -> Decimal:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return q(0)
    mid = n // 2
    return s[mid] if n % 2 else q((s[mid - 1] + s[mid]) / 2)


def _mad(values: list[Decimal], med: Decimal) -> Decimal:
    """Median absolute deviation — the spread measure an outlier cannot inflate."""
    if not values:
        return q(0)
    return _median([abs(v - med) for v in values])


def robust_z(value: Decimal, history: list[Decimal]) -> Decimal | None:
    """How many robust deviations `value` sits from the history's centre.

    None when there is not enough history to have a centre. The 0.6745 constant
    puts MAD on the same scale as a standard deviation for normal data, so the
    threshold means what a reader expects it to mean.
    """
    vals = [q(v) for v in history if v is not None]
    if len(vals) < MIN_HISTORY:
        return None
    med = _median(vals)
    mad = _mad(vals, med)
    if mad == 0:
        # Every prior receipt was identical. Any difference at all is a
        # departure, but scale it so an exact repeat is not infinitely odd.
        return q(0) if q(value) == med else q(MAD_THRESHOLD + 1)
    return q(Decimal("0.6745") * (q(value) - med) / mad)


def unit_reference_price(announced_price, package_count) -> Decimal | None:
    """The formulary's price expressed per unit.

    `announced_price` is per pack; a lot's `unit_cost` is per unit. Comparing
    them directly is the pack-basis error this catalogue has already produced
    once, and it makes every ordinary receipt look thirty times too cheap.
    """
    if announced_price is None:
        return None
    price = q(announced_price)
    if package_count is None or q(package_count) <= 0:
        return None
    return q(price / q(package_count))


def check_receipt(
    *,
    ndc11: str,
    lot_number: str,
    unit_cost,
    quantity,
    expiry_date: date | None,
    as_of: date,
    announced_price=None,
    package_count=None,
    cost_history: list | None = None,
    quantity_history: list | None = None,
    prior_lot_numbers: set[str] | None = None,
    shortest_existing_expiry: date | None = None,
) -> Verdict:
    """Everything checkable about one receipt, at the moment it is keyed in."""
    v = Verdict(ndc11=ndc11, lot_number=lot_number)
    cost = None if unit_cost is None else q(unit_cost)
    qty = q(quantity)

    # ── against the national formulary ────────────────────────────────────
    reference = unit_reference_price(announced_price, package_count)
    if cost is not None and reference is not None and reference > 0:
        ratio = q(cost / reference)
        if ratio > DEAR_RATIO:
            v.findings.append(Finding(
                "cost_above_reference", "high", "formulary",
                f"Unit cost {cost} is {ratio}x the formulary's per-unit price of "
                f"{reference}. Acquisition cost above the published retail price "
                f"is not a normal purchase.",
                observed=cost, reference=reference,
                remediation="Check the invoice and whether this is a pack price "
                            "keyed as a unit price."))
        elif ratio < CHEAP_RATIO:
            v.findings.append(Finding(
                "cost_below_reference", "medium", "formulary",
                f"Unit cost {cost} is {ratio}x the formulary's per-unit price of "
                f"{reference} — implausibly cheap.",
                observed=cost, reference=reference,
                remediation="Usually a pack cost divided by the wrong pack size, "
                            "or a unit price keyed where a pack price belongs."))
    elif cost is not None and reference is None:
        v.findings.append(Finding(
            "no_price_reference", "info", "no_reference",
            "This product has no formulary price to compare against, so the "
            "cost cannot be sanity-checked.", observed=cost,
            remediation="Bind the item to the formulary to enable the check."))

    # ── against this item's own history ───────────────────────────────────
    hist_costs = [q(c) for c in (cost_history or []) if c is not None]
    if cost is not None:
        z = robust_z(cost, hist_costs)
        if z is None:
            v.findings.append(Finding(
                "cost_history", "info", "insufficient_history",
                f"{len(hist_costs)} prior receipt(s) — too few to say whether "
                f"{cost} is unusual for this item.", observed=cost))
        elif abs(z) > MAD_THRESHOLD:
            med = _median(hist_costs)
            v.findings.append(Finding(
                "cost_unlike_history", "high", "history",
                f"Unit cost {cost} is {abs(z)} robust deviations from this "
                f"item's usual {med}.",
                observed=cost, reference=med,
                remediation="Confirm against the invoice before receiving."))

    hist_qty = [q(x) for x in (quantity_history or []) if x is not None]
    zq = robust_z(qty, hist_qty)
    if zq is not None and abs(zq) > MAD_THRESHOLD:
        medq = _median(hist_qty)
        v.findings.append(Finding(
            "quantity_unlike_history", "high", "history",
            f"Quantity {qty} is {abs(zq)} robust deviations from this item's "
            f"usual {medq}. A delivery this far out is usually packs keyed as "
            f"units, or the reverse.",
            observed=qty, reference=medq,
            remediation="Confirm the unit of measure before receiving."))

    # ── the delivery itself ───────────────────────────────────────────────
    if expiry_date is not None:
        days = expiry_date.toordinal() - as_of.toordinal()
        if days < 0:
            v.findings.append(Finding(
                "expired_on_arrival", "critical", "arrival",
                f"This stock expired {abs(days)} days ago.",
                remediation="Do not receive it into sellable stock; quarantine "
                            "and raise a supplier claim."))
        elif days <= SHORT_SHELF_LIFE_DAYS:
            v.findings.append(Finding(
                "short_shelf_life", "medium", "arrival",
                f"Only {days} days of shelf life on arrival.",
                remediation="Check the order was not filled from old stock, and "
                            "whether the quantity can realistically sell in time."))
        if (shortest_existing_expiry is not None
                and expiry_date < shortest_existing_expiry):
            v.findings.append(Finding(
                "arrives_older_than_stock", "medium", "arrival",
                f"This delivery expires {expiry_date.isoformat()}, before stock "
                f"already on the shelf ({shortest_existing_expiry.isoformat()}). "
                f"FEFO will now issue the new delivery first.",
                remediation="Verify the supplier did not ship near-dated stock."))

    if prior_lot_numbers and lot_number in prior_lot_numbers:
        v.findings.append(Finding(
            "repeated_lot_number", "medium", "history",
            f"Lot {lot_number} has been received before for this item. A genuine "
            f"repeat is possible; a re-keyed delivery is more likely.",
            remediation="Check this is not the same delivery entered twice."))

    return v


def worth_raising(v: Verdict) -> bool:
    """Whether this receipt deserves a recommendation of its own.

    `info` findings are context for whoever is looking at the receipt already —
    filing them as advice would bury the ones that need a decision.
    """
    return any(f.severity in ("critical", "high", "medium") for f in v.findings)
