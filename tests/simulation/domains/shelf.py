"""The shelf oracle: what is standing on the sales floor, and who may be accused.

Phase 3 of the pilot, and the phase that closes a gap the earlier ones left: the
simulated world received stock into lots and never placed any of it on a shelf,
so `services/core/inventory/shelf.py` — the pharmacy's second ledger — went
untouched by the pilot while `verify_spine.py` printed "stock records disagree
with the shelf" on every run.

The shelf is harder to oracle than stock or money because **half its numbers are
not measurements**. When a delivery is placed, somebody scanned it: that is
OBSERVED. When a prescription is dispensed, nobody looks at the shelf; the units
left the lot and *which* shelf they came off is inferred by allocating against
that lot's placements. Both change the number. Only one is evidence.

Everything below follows from that. A theft detector that treats its own
allocation guess as evidence will accuse an honest pharmacy every week, and one
false accusation ends the credibility of every true one. So the oracle checks
that uncertainty only ever *weakens* a verdict — an assertion about monotonicity,
not about any particular threshold.

Two conservation rules it also enforces, because the shelf keeps the same total
twice:

  `pharmacy_shelves.current_units` is a cached sum of `shelf_placements.units`.
  Two writable copies of one number is a drift generator, and the decrement path
  updates them with different arithmetic — `float(take.after)` into the
  placement, `int(take.units)` out of the cache. The oracle keeps its own count
  and reports whichever copy disagrees with it.

Thresholds are restated here rather than imported, as in the money and workflow
oracles, with `policy_drift()` to keep "the policy moved" a different sentence
from "the detector is wrong".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP

from ..spine.facts import Fact

# ── the provenance vocabulary, restated ───────────────────────────────────
OBSERVED = "observed"      # somebody scanned it and attested to it
INFERRED = "inferred"      # derived from a dispense; nobody looked at the shelf
BASES = (OBSERVED, INFERRED)

# A shortfall no larger than the movement we merely inferred is not evidence.
INFERENCE_NOISE = Decimal("1.0")
# Below this, a variance is a miscount. A system that opens a case over one
# tablet gets switched off, and then it detects nothing at all.
MIN_UNITS_TO_JUDGE = Decimal("2")

VERDICTS = ("agrees", "shrinkage", "surplus", "inconclusive", "uncounted")


def q(v) -> Decimal:
    """Three decimal places, matching the ledger's exactness."""
    d = v if isinstance(v, Decimal) else Decimal(str(v))
    return d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


@dataclass
class Placement:
    placement_id: str
    shelf_id: str
    lot_id: str
    ndc11: str
    units: Decimal
    placed_at: object = None


@dataclass
class Variance:
    shelf_id: str
    ndc11: str
    expected: Decimal
    counted: Decimal | None
    variance: Decimal
    inferred_units: Decimal
    verdict: str
    value_at_risk: Decimal | None
    why: str
    concerns: list[str] = field(default_factory=list)


class ShelfDomain:
    """Independent ground truth for the sales floor."""

    name = "shelf"

    def __init__(self):
        self.placements: dict[str, Placement] = {}
        # (shelf_id, ndc11) → units that left without anybody scanning
        self.inferred: dict[tuple[str, str], Decimal] = {}
        self.counts: dict[tuple[str, str], Decimal] = {}
        self._findings: list[str] = []
        self._broken: list[str] = []

    # ── allocation, re-derived ────────────────────────────────────────────
    def allocate(self, lot_id: str, units, *, basis: str = INFERRED) -> dict:
        """Take units off this lot's placements, oldest first.

        Oldest first because a pharmacy works the front of the shelf. Where a lot
        sits on two shelves the choice is genuinely unknowable without a scan —
        which is exactly why the result is marked inferred rather than observed.
        """
        want = q(units)
        if want <= 0:
            return {"takes": [], "from_shelf": q(0), "from_backstock": q(0)}
        mine = [p for p in self.placements.values()
                if p.lot_id == str(lot_id) and p.units > 0]
        mine.sort(key=lambda p: (p.placed_at is None, p.placed_at, p.placement_id))

        takes, left = [], want
        for p in mine:
            if left <= 0:
                break
            take = min(left, p.units)
            if take <= 0:
                continue
            takes.append({"placement_id": p.placement_id, "shelf_id": p.shelf_id,
                          "before": p.units, "units": take,
                          "after": q(p.units - take), "basis": basis})
            left = q(left - take)
        return {"takes": takes, "from_shelf": q(want - left),
                "from_backstock": q(left)}

    def allocation_findings(self, want, plan: dict) -> list[str]:
        """Properties every allocation must have, whoever computed it."""
        out: list[str] = []
        want = q(want)
        got = q(plan["from_shelf"]) + q(plan["from_backstock"])
        if got != want:
            out.append(f"allocation lost units: asked for {want}, accounted for "
                       f"{got} ({plan['from_shelf']} shelf + "
                       f"{plan['from_backstock']} back-stock)")
        summed = sum((q(t["units"]) for t in plan["takes"]), q(0))
        if summed != q(plan["from_shelf"]):
            out.append(f"takes sum to {summed} but from_shelf says "
                       f"{plan['from_shelf']}")
        for t in plan["takes"]:
            if q(t["after"]) != q(q(t["before"]) - q(t["units"])):
                out.append(f"placement {t['placement_id']}: {t['before']} − "
                           f"{t['units']} != {t['after']}")
            if q(t["after"]) < 0:
                out.append(f"placement {t['placement_id']} left negative "
                           f"({t['after']})")
            if t["basis"] not in BASES:
                out.append(f"placement {t['placement_id']} carries basis "
                           f"{t['basis']!r}, which is not one of {BASES}")
        return out

    # ── the detector, re-derived ──────────────────────────────────────────
    def reconcile(self, *, shelf_id: str, ndc11: str, expected, counted,
                  inferred_units=0, sell_price=None) -> Variance:
        exp = q(expected)
        inf = q(inferred_units)
        concerns: list[str] = []

        def out(verdict, why, at_risk=None, got=None, var=None):
            return Variance(shelf_id=shelf_id, ndc11=ndc11, expected=exp,
                            counted=got, variance=var if var is not None else q(0),
                            inferred_units=inf, verdict=verdict,
                            value_at_risk=at_risk, why=why, concerns=concerns)

        if counted is None:
            # Nobody looked. A detector that scores an uncounted shelf is
            # inventing a measurement.
            return out("uncounted", "not counted; nothing is claimed")

        got = q(counted)
        var = q(got - exp)
        if inf > 0:
            concerns.append(f"{inf} unit(s) left this shelf unscanned")

        if var == 0:
            return out("agrees", f"counted {got}, expected {exp}", got=got, var=var)
        if var > 0:
            # Stock does not appear by itself. Counted above expected is a
            # bookkeeping failure, and treating it as a finding against a person
            # would be absurd.
            return out("surplus", f"{var} more than the books show — an "
                                  f"unrecorded placement, not a loss",
                       got=got, var=var)

        missing = -var
        if missing < MIN_UNITS_TO_JUDGE:
            return out("inconclusive", f"{missing} short — a miscount",
                       got=got, var=var)
        if missing <= q(inf * INFERENCE_NOISE):
            return out("inconclusive",
                       f"{missing} short but {inf} left unscanned — the missing "
                       f"units may be on the next shelf along",
                       got=got, var=var)
        at_risk = q(missing * q(sell_price)) if sell_price is not None else None
        if sell_price is None:
            concerns.append("no shelf price, so the loss is in units not money")
        return out("shrinkage",
                   f"{missing} unit(s) missing from {shelf_id}", at_risk,
                   got=got, var=var)

    def worth_investigating(self, v: Variance) -> bool:
        return v.verdict == "shrinkage"

    # ── position and valuation, re-derived ────────────────────────────────
    def position(self, prices: dict[str, Decimal | None] | None = None) -> dict:
        """Units and retail value standing on the floor right now.

        Valued at the sell price, because the question is what the floor would
        ring up for. A line with no price contributes units and no money, and the
        count of those travels with the total — a figure that silently omits them
        reads as smaller than the floor really is.
        """
        prices = prices or {}
        by_shelf: dict[str, dict] = {}
        units = q(0)
        value = q(0)
        unpriced = 0
        for p in self.placements.values():
            if p.units <= 0:
                continue
            e = by_shelf.setdefault(p.shelf_id, {"units": q(0), "lines": 0,
                                                 "value": q(0), "unpriced": 0})
            e["units"] = q(e["units"] + p.units)
            e["lines"] += 1
            units = q(units + p.units)
            price = prices.get(p.ndc11)
            if price is None:
                e["unpriced"] += 1
                unpriced += 1
                continue
            line = q(q(price) * p.units)
            e["value"] = q(e["value"] + line)
            value = q(value + line)
        return {"shelves": by_shelf, "units": units, "value": value,
                "unpriced_lines": unpriced}

    # ── the Domain protocol ───────────────────────────────────────────────
    def observe(self, fact: Fact) -> None:
        p = fact.payload
        if fact.kind == "placed_on_shelf":
            pid = str(p.get("placement_id"))
            units = q(fact.quantity or 0)
            if units < 0:
                self._broken.append(
                    f"placement {pid}: told {units} units were placed, which is "
                    f"not a quantity a placement can have")
                return
            existing = self.placements.get(pid)
            if existing is not None:
                existing.units = q(existing.units + units)
            else:
                self.placements[pid] = Placement(
                    placement_id=pid, shelf_id=str(p.get("shelf_id")),
                    lot_id=str(p.get("lot_id")), ndc11=str(fact.subject),
                    units=units, placed_at=p.get("placed_at") or fact.at)
            return

        if fact.kind == "taken_off_shelf":
            pid = str(p.get("placement_id"))
            units = q(fact.quantity or 0)
            basis = str(p.get("basis", INFERRED))
            if basis not in BASES:
                self._broken.append(f"take off {pid} carries basis {basis!r}")
                return
            placement = self.placements.get(pid)
            if placement is None:
                self._findings.append(
                    f"units were taken off placement {pid}, which the shelf "
                    f"ledger has no record of")
                return
            if units > placement.units:
                self._findings.append(
                    f"placement {pid}: {units} taken off a placement holding "
                    f"only {placement.units} — the shelf would go negative")
            placement.units = q(max(Decimal("0"), placement.units - units))
            if basis == INFERRED:
                key = (placement.shelf_id, placement.ndc11)
                self.inferred[key] = q(self.inferred.get(key, q(0)) + units)
            return

        if fact.kind == "counted":
            key = (str(p.get("shelf_id")), str(fact.subject))
            self.counts[key] = q(fact.quantity or 0)
            return

    async def check(self, ctx) -> list[str]:
        out = list(self._findings)
        self._findings.clear()

        for p in self.placements.values():
            if p.units < 0:
                out.append(f"placement {p.placement_id} holds {p.units} units")

        db = getattr(ctx, "db", None)
        if db is None:
            return out
        out.extend(await self._check_against_db(ctx))
        return out

    async def _check_against_db(self, ctx) -> list[str]:
        """The shelf keeps its total twice; both copies must match the oracle."""
        from sqlalchemy import text
        out: list[str] = []

        rows = (await ctx.db.execute(text(
            "SELECT id::text AS id, units FROM shelf_placements "
            " WHERE pharmacy_id = :p AND is_deleted = false"),
            {"p": ctx.pharmacy_id})).mappings().all()
        stored = {r["id"]: q(r["units"]) for r in rows}
        for pid, mine in self.placements.items():
            if pid not in stored:
                out.append(f"placement {pid}: the oracle holds {mine.units} "
                           f"units, the database has no such placement")
            elif stored[pid] != mine.units:
                out.append(f"placement {pid}: database says {stored[pid]} "
                           f"unit(s), the oracle says {mine.units}")

        # The cached per-shelf total against the placements it caches.
        drift = (await ctx.db.execute(text(
            "SELECT s.id::text AS id, s.label, s.current_units, "
            "       COALESCE(SUM(p.units), 0) AS placed "
            "  FROM pharmacy_shelves s "
            "  LEFT JOIN shelf_placements p "
            "         ON p.shelf_id = s.id AND p.is_deleted = false "
            " WHERE s.pharmacy_id = :p AND s.is_deleted = false "
            " GROUP BY s.id, s.label, s.current_units "
            "HAVING s.current_units <> COALESCE(SUM(p.units), 0)"),
            {"p": ctx.pharmacy_id})).mappings().all()
        for r in drift:
            out.append(
                f"shelf {r['label']}: pharmacy_shelves.current_units is "
                f"{r['current_units']} but its placements sum to {r['placed']} — "
                f"the cached total and the rows it caches disagree")
        return out

    def violations(self) -> list[str]:
        return list(self._broken)

    # ── the policy gate ───────────────────────────────────────────────────
    def policy_drift(self) -> list[str]:
        """Where the deployment's shelf policy differs from the one restated here.

        Not a defect report. A pharmacy may legitimately retune either threshold;
        when it does, this oracle is the stale one. Same separation as the money
        oracle's `tariff_drift()`.
        """
        from services.core.inventory import shelf as impl

        out: list[str] = []
        if Decimal(str(impl.INFERENCE_NOISE)) != INFERENCE_NOISE:
            out.append(f"inference noise: implementation "
                       f"{impl.INFERENCE_NOISE}, oracle {INFERENCE_NOISE}")
        if Decimal(str(impl.MIN_UNITS_TO_JUDGE)) != MIN_UNITS_TO_JUDGE:
            out.append(f"minimum units to judge: implementation "
                       f"{impl.MIN_UNITS_TO_JUDGE}, oracle {MIN_UNITS_TO_JUDGE}")
        if tuple(impl.BASES) != BASES:
            out.append(f"bases: implementation {impl.BASES}, oracle {BASES}")
        if tuple(impl.VERDICTS) != VERDICTS:
            out.append(f"verdicts: implementation {impl.VERDICTS}, "
                       f"oracle {VERDICTS}")
        return out
