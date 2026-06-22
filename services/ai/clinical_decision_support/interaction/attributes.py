from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from services.ai.clinical_decision_support.normalizer import normalize

_DATA = Path(__file__).parent / "data" / "drug_attributes.yaml"


@dataclass(frozen=True)
class EnzymeRole:
    enzyme: str
    role: str                       # substrate | inhibitor | inducer
    strength: str | None = None     # strong | moderate | weak
    fm: float | None = None
    yields: str | None = None       # parent | active
    inhibition_type: str | None = None  # reversible | mechanism_based


@dataclass(frozen=True)
class TransporterRole:
    name: str
    role: str
    strength: str | None = None
    organ: str | None = None


@dataclass(frozen=True)
class PDAxis:
    axis: str
    strength: str | None = None
    tier: str | None = None
    subtype: str | None = None
    direction: str = "additive"     # additive | synergistic | antagonistic


@dataclass(frozen=True)
class NTI:
    is_nti: bool = False
    consequence: str = "toxicity"   # toxicity | efficacy_loss


@dataclass(frozen=True)
class Absorption:
    chelation_cations: list[str] = field(default_factory=list)
    separation_hours: int | None = None
    ph_dependent: str | None = None  # "acid_requiring"


@dataclass(frozen=True)
class DrugAttributes:
    ingredient: str
    classes: tuple[str, ...] = ()
    enzymes: tuple[EnzymeRole, ...] = ()
    transporters: tuple[TransporterRole, ...] = ()
    pd: dict[str, PDAxis] = field(default_factory=dict)
    nti: NTI = field(default_factory=NTI)
    prodrug: bool = False
    activating_enzyme: str | None = None
    active_metabolite: str | None = None
    pgx_enzyme: str | None = None
    elimination_route: str | None = None
    absorption: Absorption | None = None
    provides_cations: tuple[str, ...] = ()
    absorption_suppressant_ph: bool = False
    reduces_renal_clearance_of: tuple[str, ...] = ()
    induction_offset_days: int | None = None
    long_acting: bool = False


@dataclass(frozen=True)
class AttributeIndex:
    by_ingredient: dict[str, DrugAttributes]

    def get(self, name: str | None) -> DrugAttributes | None:
        return self.by_ingredient.get(normalize(name))


def _parse(entry: dict) -> DrugAttributes:
    pd = {}
    for axis, body in (entry.get("pd") or {}).items():
        body = body or {}
        pd[axis] = PDAxis(axis=axis, strength=body.get("strength"), tier=body.get("tier"),
                          subtype=body.get("subtype"), direction=body.get("direction", "additive"))
    absn = entry.get("absorption")
    absorption = Absorption(
        chelation_cations=list((absn or {}).get("chelation_cations", [])),
        separation_hours=(absn or {}).get("separation_hours"),
        ph_dependent=(absn or {}).get("ph_dependent"),
    ) if absn else None
    nti_raw = entry.get("nti") or {}
    induction = entry.get("induction") or {}
    return DrugAttributes(
        ingredient=normalize(entry["ingredient"]),
        classes=tuple(entry.get("classes", [])),
        enzymes=tuple(EnzymeRole(**e) for e in entry.get("enzymes", [])),
        transporters=tuple(TransporterRole(**t) for t in entry.get("transporters", [])),
        pd=pd,
        nti=NTI(is_nti=nti_raw.get("is_nti", False), consequence=nti_raw.get("consequence", "toxicity")),
        prodrug=entry.get("prodrug", False),
        activating_enzyme=entry.get("activating_enzyme"),
        active_metabolite=entry.get("active_metabolite"),
        pgx_enzyme=entry.get("pgx_enzyme"),
        elimination_route=entry.get("elimination_route"),
        absorption=absorption,
        provides_cations=tuple(entry.get("provides_cations", [])),
        absorption_suppressant_ph=bool((entry.get("absorption_suppressant") or {}).get("ph", False)),
        reduces_renal_clearance_of=tuple(normalize(x) for x in entry.get("reduces_renal_clearance_of", [])),
        induction_offset_days=induction.get("offset_days"),
        long_acting=entry.get("long_acting", False),
    )


@lru_cache(maxsize=1)
def load_attribute_index() -> AttributeIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    return AttributeIndex(by_ingredient={a.ingredient: a for a in (_parse(e) for e in raw)})
