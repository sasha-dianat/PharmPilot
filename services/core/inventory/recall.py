"""Recall — answering "where did lot X go, and who received it?"

A recall is the moment the whole inventory system is either worth having or is
not. Everything else can be reconstructed later; a recall has to be answered in
minutes, and the answer has to be honest about what it does not know.

That last part drives the design. The obvious implementation resolves the lots,
follows the dispense movements to the fills, and reports the patients. Run
against this pharmacy today it would report **zero patients affected** — and be
completely wrong, because all 46 existing fills predate the lot link and carry
no lot at all. A recall that quietly reports zero because it cannot see is
worse than one that reports nothing, so `trace_completeness` measures the blind
spot first and every result carries it.

Four states an affected unit can be in, each with a different action:

  on the shelf      → quarantine now; this is the only urgent one
  already blocked   → write off under approval
  dispensed         → the patient needs telling
  already gone      → written off or returned; nothing to do but record it

and a fifth that is not a state but an admission: **untraceable** — units we
know left the building without knowing to whom.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .ledger import q

# ── scope ─────────────────────────────────────────────────────────────────
BY_LOT = "lot_number"        # the manufacturer named a lot
BY_IRC = "irc"               # every lot of a product
BY_GTIN = "gtin"
BY_SUPPLIER_BATCH = "supplier_batch"
SCOPES = (BY_LOT, BY_IRC, BY_GTIN, BY_SUPPLIER_BATCH)

# ── classification of affected quantity ───────────────────────────────────
ON_SHELF = "on_shelf"
BLOCKED = "blocked"              # quarantined / cold-chain / already flagged
DISPENSED = "dispensed"
ALREADY_GONE = "already_gone"    # written off, returned to supplier, destroyed
UNTRACEABLE = "untraceable"

ACTION = {
    ON_SHELF:      "quarantine immediately, then write off under approval",
    BLOCKED:       "already off the shelf — write off under approval",
    DISPENSED:     "contact the patient",
    ALREADY_GONE:  "no action; recorded for completeness",
    UNTRACEABLE:   "units left without a recorded lot — widen the notification",
}

# Severity classes drive how far the notification has to reach. Class I is the
# level at which "we could not trace 46 dispenses" stops being a data-quality
# note and becomes a reason to contact every patient who received the product.
CLASS_I = "I"       # reasonable probability of serious harm or death
CLASS_II = "II"     # temporary or reversible harm
CLASS_III = "III"   # unlikely to cause harm
CLASSES = (CLASS_I, CLASS_II, CLASS_III)


class RecallError(ValueError):
    """A recall that cannot be defined or cannot legitimately be closed."""


@dataclass(frozen=True)
class AffectedUnit:
    """One quantity of recalled stock, and what must happen to it."""
    state: str
    quantity: Decimal
    lot_id: str | None = None
    lot_number: str | None = None
    location: str | None = None
    fill_id: str | None = None
    patient_id: str | None = None
    dispensed_at: str | None = None

    @property
    def action(self) -> str:
        return ACTION[self.state]

    def as_dict(self) -> dict:
        return {"state": self.state, "quantity": float(self.quantity),
                "lot_id": self.lot_id, "lot_number": self.lot_number,
                "location": self.location, "fill_id": self.fill_id,
                "patient_id": self.patient_id, "dispensed_at": self.dispensed_at,
                "action": self.action}


def classify_lot(lot: dict, *, as_of: date | None = None) -> AffectedUnit | None:
    """What state is this lot's remaining stock in? None when it holds nothing."""
    qty = q(lot.get("quantity_on_hand") or 0)
    if qty <= 0:
        return None
    blocked = bool(lot.get("is_quarantined") or lot.get("is_recalled")
                   or lot.get("cold_chain_breach"))
    exp = lot.get("expiry_date")
    if isinstance(exp, str):
        exp = date.fromisoformat(exp[:10])
    if not blocked and exp and exp < (as_of or date.today()):
        # Expired stock is off-limits for dispensing but still physically
        # present, so a recall must still account for and remove it.
        blocked = True
    return AffectedUnit(state=BLOCKED if blocked else ON_SHELF, quantity=qty,
                        lot_id=str(lot.get("id") or lot.get("lot_id") or ""),
                        lot_number=lot.get("lot_number"),
                        location=lot.get("storage_location"))


def classify_dispense(row: dict) -> AffectedUnit:
    """A dispense of recalled stock. The patient may be None — a fill with no
    patient link is still a unit that left, and it is recorded as such rather
    than dropped."""
    return AffectedUnit(
        state=DISPENSED, quantity=q(abs(row.get("quantity") or 0)),
        lot_id=str(row["lot_id"]) if row.get("lot_id") else None,
        lot_number=row.get("lot_number"),
        fill_id=str(row["fill_id"]) if row.get("fill_id") else None,
        patient_id=str(row["patient_id"]) if row.get("patient_id") else None,
        dispensed_at=str(row["dispensed_at"]) if row.get("dispensed_at") else None)


@dataclass
class TraceCompleteness:
    """How much of this recall the records can actually account for.

    `traceable_pct` is the number that decides whether the patient list may be
    treated as complete. Reporting a recall's patient list without it invites
    exactly the wrong conclusion from a short list.
    """
    dispensed_fills_total: int = 0
    fills_with_lot_link: int = 0
    fills_with_lot_text_only: int = 0
    fills_untraceable: int = 0
    units_dispensed: Decimal = Decimal("0")
    units_untraceable: Decimal = Decimal("0")

    @property
    def traceable_pct(self) -> float:
        if not self.dispensed_fills_total:
            return 100.0
        traced = self.fills_with_lot_link + self.fills_with_lot_text_only
        return round(100.0 * traced / self.dispensed_fills_total, 1)

    @property
    def complete(self) -> bool:
        return self.fills_untraceable == 0

    def warning(self, severity: str) -> str | None:
        """The sentence a pharmacist has to read before trusting the list."""
        if self.complete:
            return None
        base = (f"{self.fills_untraceable} of {self.dispensed_fills_total} "
                f"dispenses of this product carry no lot, so the patient list "
                f"below is incomplete ({self.traceable_pct}% traceable).")
        if severity == CLASS_I:
            return (base + " For a Class I recall the safe course is to notify "
                    "every patient who received this product in the period, not "
                    "only those the records can tie to the lot.")
        return base + " Consider widening the notification to the product."

    def as_dict(self) -> dict:
        return {"dispensed_fills_total": self.dispensed_fills_total,
                "fills_with_lot_link": self.fills_with_lot_link,
                "fills_with_lot_text_only": self.fills_with_lot_text_only,
                "fills_untraceable": self.fills_untraceable,
                "units_dispensed": float(self.units_dispensed),
                "units_untraceable": float(self.units_untraceable),
                "traceable_pct": self.traceable_pct, "complete": self.complete}


@dataclass
class RecallImpact:
    units: list[AffectedUnit] = field(default_factory=list)
    completeness: TraceCompleteness = field(default_factory=TraceCompleteness)
    severity: str = CLASS_II

    def by_state(self, state: str) -> list[AffectedUnit]:
        return [u for u in self.units if u.state == state]

    def quantity_in(self, state: str) -> Decimal:
        return q(sum((u.quantity for u in self.by_state(state)), Decimal("0")))

    @property
    def patients(self) -> list[str]:
        seen, out = set(), []
        for u in self.by_state(DISPENSED):
            if u.patient_id and u.patient_id not in seen:
                seen.add(u.patient_id)
                out.append(u.patient_id)
        return out

    @property
    def sellable_remaining(self) -> Decimal:
        return self.quantity_in(ON_SHELF)

    def summary(self) -> dict:
        return {
            "severity": self.severity,
            "lots_affected": len({u.lot_id for u in self.units if u.lot_id}),
            "on_shelf": float(self.quantity_in(ON_SHELF)),
            "blocked": float(self.quantity_in(BLOCKED)),
            "dispensed": float(self.quantity_in(DISPENSED)),
            "patients_identified": len(self.patients),
            "completeness": self.completeness.as_dict(),
            "warning": self.completeness.warning(self.severity),
            "urgent_action": (
                f"{float(self.quantity_in(ON_SHELF))} units are still sellable "
                f"— quarantine before anything else"
                if self.quantity_in(ON_SHELF) > 0 else None),
        }


def build_impact(lots: list[dict], dispenses: list[dict], *,
                 severity: str = CLASS_II,
                 product_dispenses_total: int | None = None,
                 as_of: date | None = None) -> RecallImpact:
    """Assemble the picture from resolved lots and their dispense movements.

    `product_dispenses_total` is how many dispenses of the *product* exist in
    the recall window, regardless of lot. The gap between it and the dispenses
    we can tie to a lot is the blind spot, and it is the whole reason this
    function takes the argument rather than counting only what it can see.
    """
    if severity not in CLASSES:
        raise RecallError(f"unknown recall class {severity!r}")

    units: list[AffectedUnit] = []
    for lot in lots:
        u = classify_lot(lot, as_of=as_of)
        if u is not None:
            units.append(u)

    linked = text_only = 0
    dispensed_units = Decimal("0")
    for d in dispenses:
        u = classify_dispense(d)
        units.append(u)
        dispensed_units = q(dispensed_units + u.quantity)
        if d.get("lot_id"):
            linked += 1
        else:
            text_only += 1

    total = product_dispenses_total if product_dispenses_total is not None \
        else (linked + text_only)
    untraceable = max(0, total - linked - text_only)
    comp = TraceCompleteness(
        dispensed_fills_total=total, fills_with_lot_link=linked,
        fills_with_lot_text_only=text_only, fills_untraceable=untraceable,
        units_dispensed=dispensed_units)
    return RecallImpact(units=units, completeness=comp, severity=severity)


# ── closure ───────────────────────────────────────────────────────────────
OPEN = "open"
CONTAINED = "contained"      # nothing sellable remains, patients still to notify
CLOSED = "closed"
CANCELLED = "cancelled"
RECALL_STATUSES = (OPEN, CONTAINED, CLOSED, CANCELLED)


def closure_check(impact: RecallImpact, *, patients_notified: int,
                  force_reason: str | None = None) -> dict:
    """May this recall be closed?

    Two independent conditions, and neither is waivable by accident:
    no recalled stock remains sellable, and every identified patient has been
    told. A Class I recall additionally may not close while the trace is
    incomplete — the untraceable dispenses are precisely the ones most likely to
    matter, and closing over them is how a recall becomes paperwork.
    """
    blockers = []
    if impact.sellable_remaining > 0:
        blockers.append(
            f"{float(impact.sellable_remaining)} units are still sellable")
    outstanding = len(impact.patients) - patients_notified
    if outstanding > 0:
        blockers.append(f"{outstanding} identified patients have not been notified")
    if impact.severity == CLASS_I and not impact.completeness.complete:
        blockers.append(
            f"Class I recall with an incomplete trace "
            f"({impact.completeness.traceable_pct}% of dispenses tied to a lot)")

    if not blockers:
        return {"may_close": True, "blockers": [], "next_status": CLOSED}
    if force_reason:
        if not force_reason.strip():
            raise RecallError("closing a recall over open blockers needs a reason")
        return {"may_close": True, "blockers": blockers, "forced": True,
                "next_status": CLOSED, "reason": force_reason.strip()}
    return {"may_close": False, "blockers": blockers,
            "next_status": CONTAINED if impact.sellable_remaining == 0 else OPEN}
