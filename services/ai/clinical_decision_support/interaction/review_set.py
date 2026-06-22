from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.clinical import Medication
from shared.models.prescription import Prescription, RxStatus
from services.ai.clinical_decision_support.normalizer import normalize, classes_of

CURRENT_RX_STATUSES = [
    RxStatus.INTAKE, RxStatus.PENDING_DUR, RxStatus.DUR_HOLD,
    RxStatus.PENDING_VERIFICATION, RxStatus.VERIFICATION_IN_PROGRESS,
    RxStatus.PENDING_ADJUDICATION, RxStatus.PENDING_PA, RxStatus.READY_TO_FILL,
    RxStatus.FILLING, RxStatus.WILL_CALL, RxStatus.ON_HOLD,
]


@dataclass(frozen=True)
class ReviewMed:
    normalized_name: str
    classes: tuple[str, ...]
    provenance: str                 # current_rx | active | historical
    last_seen_date: date | None


@dataclass(frozen=True)
class Condition:
    concept: str
    status: str = "active"          # model has no per-condition status yet (#1 limitation)


@dataclass(frozen=True)
class ReviewSet:
    meds: list[ReviewMed]
    conditions: list[Condition]
    allergies: list[str] = field(default_factory=list)
    labs: dict = field(default_factory=dict)        # {lab_key: float} latest value
    age: int | None = None


def norm_condition(text: str) -> str:
    """Conditions are NOT drugs — never run the drug normalizer on them."""
    return (text or "").strip().lower().replace(" ", "_")


def _med(name: str, provenance: str, last_seen: date | None) -> ReviewMed:
    norm = normalize(name)
    tokens = {norm, *classes_of(name)}
    return ReviewMed(normalized_name=norm, classes=tuple(sorted(tokens)),
                     provenance=provenance, last_seen_date=last_seen)


def assemble_review_set(*, current_rx, meds, conditions, allergies,
                        labs=None, age=None) -> ReviewSet:
    review_meds = [_med(rx.drug_name, "current_rx", None) for rx in current_rx]
    for m in meds:
        prov = "active" if getattr(m, "status", "active") == "active" else "historical"
        review_meds.append(_med(getattr(m, "normalized_name", None) or m.drug_name,
                                prov, getattr(m, "start_date", None)))
    return ReviewSet(
        meds=review_meds,
        conditions=[Condition(concept=norm_condition(c)) for c in (conditions or [])],
        allergies=list(allergies or []),
        labs=labs or {},
        age=age,
    )


async def build_review_set(*, db: AsyncSession, patient, pharmacy_id, rx_ids=None) -> ReviewSet:
    rx_q = select(Prescription).where(
        Prescription.patient_id == patient.id,
        Prescription.pharmacy_id == pharmacy_id,
        Prescription.is_deleted == False,  # noqa: E712
    )
    rx_q = rx_q.where(Prescription.id.in_(rx_ids)) if rx_ids else \
        rx_q.where(Prescription.status.in_([s.value for s in CURRENT_RX_STATUSES]))
    current_rx = (await db.execute(rx_q)).scalars().all()

    meds = (await db.execute(select(Medication).where(
        Medication.patient_id == patient.id,
        Medication.pharmacy_id == pharmacy_id,
        Medication.is_deleted == False,  # noqa: E712
    ))).scalars().all()

    # Latest value per lab (reuse the existing cds helpers for keys/floats).
    from services.platform.routers.cds import _lab_key, _to_float, _age_from_dob
    from shared.models.patient import LabResult, PatientAllergy
    lab_rows = (await db.execute(
        select(LabResult).where(LabResult.patient_id == patient.id,
                                LabResult.is_deleted == False)  # noqa: E712
        .order_by(LabResult.result_date.desc()))).scalars().all()
    labs: dict[str, float] = {}
    for row in lab_rows:
        key = _lab_key(row.test_name)
        if key not in labs and _to_float(row.value) is not None:
            labs[key] = _to_float(row.value)
    allergy_rows = (await db.execute(
        select(PatientAllergy).where(PatientAllergy.patient_id == patient.id,
                                     PatientAllergy.is_deleted == False))  # noqa: E712
        ).scalars().all()

    return assemble_review_set(
        current_rx=current_rx, meds=meds,
        conditions=getattr(patient, "conditions", None) or [],
        allergies=[a.allergen_name for a in allergy_rows],
        labs=labs, age=_age_from_dob(getattr(patient, "date_of_birth", None)),
    )


# Lab keys whose values can change findings (engine reads these).
_RELEVANT_LABS = ("potassium", "egfr")


def review_set_hash(rs: ReviewSet) -> str:
    """Stable content hash over everything the engine consumes, so any change
    that could change findings changes the cache key."""
    meds = sorted(f"{m.normalized_name}:{m.provenance}" for m in rs.meds)
    conds = sorted(c.concept for c in rs.conditions)
    labs = [f"{k}={round(rs.labs[k], 2)}" for k in _RELEVANT_LABS if k in rs.labs]
    payload = "|".join(["M", *meds, "C", *conds, "L", *labs, "A", *sorted(rs.allergies)])
    return hashlib.sha256(payload.encode()).hexdigest()
