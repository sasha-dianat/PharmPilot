"""A synthetic pharmacy, generated from a seed.

Everything here is deterministic given `seed`: the same seed produces the same
catalogue, the same lots, the same demand profiles. That is not a nicety — a
simulation that finds a defect and cannot reproduce it has found nothing.

The catalogue is built to have the shapes that break inventory systems, not the
shapes that flatter them:

  * items that never move (dead stock the reorder logic must not order)
  * items that move in bursts (a mean is a lie for these)
  * items with one unit left (the double-promise case)
  * lots that expire tomorrow next to lots that expire in two years
  * pack sizes that make unit-vs-pack confusion plausible
  * costs that differ between lots of the same drug, so FIFO and weighted
    average genuinely disagree
  * controlled and refrigerated items, where the rules are stricter
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

# Demand archetypes. The names matter downstream: the generator uses them to
# decide how dispensing events cluster in time.
ARCHETYPES = ("fast", "steady", "slow", "intermittent", "seasonal", "dead")

FORMS = ("TAB", "CAP", "SYRUP", "AMP", "VIAL", "CREAM", "DROPS", "INHALER")
# Pack sizes chosen so that "3" could plausibly mean units or packs.
PACK_SIZES = (1, 10, 20, 28, 30, 60, 100)

GENERICS = (
    "metformin", "atorvastatin", "lisinopril", "gabapentin", "amlodipine",
    "omeprazole", "metoprolol", "sertraline", "levothyroxine", "warfarin",
    "insulin glargine", "morphine sulfate", "amoxicillin", "salbutamol",
    "prednisolone", "furosemide", "clopidogrel", "pantoprazole",
    "methylphenidate", "diazepam", "ceftriaxone", "enoxaparin",
)
# Items where a mistake is expensive or regulated.
CONTROLLED = {"morphine sulfate", "methylphenidate", "diazepam"}
REFRIGERATED = {"insulin glargine", "enoxaparin", "ceftriaxone"}
HIGH_VALUE = {"insulin glargine", "enoxaparin", "clopidogrel"}

SUPPLIERS = ("darupakhsh", "hejrat", "ferdows", "alborz", "razi")


@dataclass
class Product:
    ndc11: str
    generic: str
    brand: str | None
    strength: str
    form: str
    pack_size: int
    is_controlled: bool
    refrigerated: bool
    archetype: str
    par_min: Decimal
    par_max: Decimal

    @property
    def high_value(self) -> bool:
        return self.generic in HIGH_VALUE


@dataclass
class Lot:
    lot_number: str
    ndc11: str
    expiry: date
    unit_cost: Decimal
    received: Decimal
    supplier: str


@dataclass
class World:
    seed: int
    as_of: date
    products: list[Product] = field(default_factory=list)
    lots: list[Lot] = field(default_factory=list)

    def by_ndc(self, ndc: str) -> Product:
        return next(p for p in self.products if p.ndc11 == ndc)

    def lots_for(self, ndc: str) -> list[Lot]:
        return [l for l in self.lots if l.ndc11 == ndc]


def _ndc(rng: random.Random, used: set[str]) -> str:
    while True:
        # 11 digits, prefixed 95 so simulated stock is distinguishable from any
        # real or demo catalogue row at a glance.
        cand = "95" + "".join(str(rng.randint(0, 9)) for _ in range(9))
        if cand not in used:
            used.add(cand)
            return cand


def build(seed: int = 1, *, n_products: int = 40, as_of: date | None = None) -> World:
    """A pharmacy with `n_products` SKUs and one to four lots each."""
    rng = random.Random(seed)
    today = as_of or date(2026, 8, 9)
    used: set[str] = set()
    w = World(seed=seed, as_of=today)

    for i in range(n_products):
        generic = GENERICS[i % len(GENERICS)]
        form = rng.choice(FORMS)
        pack = rng.choice(PACK_SIZES)
        archetype = ARCHETYPES[i % len(ARCHETYPES)]
        strength_mg = rng.choice((2.5, 5, 10, 20, 25, 40, 50, 100, 300, 500))
        par_min = Decimal(rng.choice((0, 10, 20, 30, 50)))
        p = Product(
            ndc11=_ndc(rng, used),
            generic=generic,
            brand=None if rng.random() < 0.4 else f"{generic[:4].title()}rex",
            strength=f"{strength_mg:g} mg",
            form=form,
            pack_size=pack,
            is_controlled=generic in CONTROLLED,
            refrigerated=generic in REFRIGERATED,
            archetype=archetype,
            par_min=par_min,
            par_max=par_min * 6 if par_min else Decimal(120),
        )
        w.products.append(p)

        # Lots: deliberately including an already-expired one and a
        # nearly-expired one on some items, so FEFO and expiry rules are
        # exercised rather than assumed.
        n_lots = rng.choice((1, 1, 2, 2, 3, 4))
        base_cost = Decimal(rng.choice((1, 2, 5, 12, 45, 180, 900)))
        for k in range(n_lots):
            if k == 0 and rng.random() < 0.12:
                offset = -rng.randint(1, 90)            # already expired
            elif rng.random() < 0.18:
                offset = rng.randint(1, 45)             # expiring soon
            else:
                offset = rng.randint(120, 900)
            # Cost drifts between lots, so FIFO and weighted average differ.
            cost = (base_cost * Decimal(str(round(rng.uniform(0.8, 1.4), 3)))
                    ).quantize(Decimal("0.0001"))
            qty = Decimal(rng.choice((1, 2, 5, 30, 60, 90, 120, 300, 1000)))
            w.lots.append(Lot(
                lot_number=f"L{seed}-{i}-{k}",
                ndc11=p.ndc11,
                expiry=today + timedelta(days=offset),
                unit_cost=cost,
                received=qty,
                supplier=rng.choice(SUPPLIERS),
            ))
    return w


# ── demand shapes ─────────────────────────────────────────────────────────

def daily_demand(product: Product, day: int, rng: random.Random) -> int:
    """Units dispensed on `day` for this product's archetype.

    Returns whole units — a pharmacy dispenses tablets, not fractions of them,
    and using fractional demand here would hide rounding defects rather than
    expose them.
    """
    a = product.archetype
    if a == "dead":
        return 0
    if a == "fast":
        return max(0, int(rng.gauss(12, 4)))
    if a == "steady":
        return max(0, int(rng.gauss(4, 1.5)))
    if a == "slow":
        return 1 if rng.random() < 0.15 else 0
    if a == "intermittent":
        # Long silences broken by a burst — the shape a mean describes worst.
        return rng.choice((20, 30, 45)) if rng.random() < 0.07 else 0
    if a == "seasonal":
        # A slow annual swing plus noise.
        import math
        base = 6 + 5 * math.sin(day / 58.0)
        return max(0, int(rng.gauss(base, 1.5)))
    return 0
