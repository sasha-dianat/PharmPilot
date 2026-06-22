from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.clinical import InteractionReportCache
from shared.models.patient import Patient
from .engine import MODEL_VERSION, evaluate
from .report import findings_hash, report_to_dict
from .review_set import build_review_set, review_set_hash

logger = logging.getLogger(__name__)


async def recompute_and_cache(*, db: AsyncSession, patient, pharmacy_id, rs=None):
    if rs is None:
        rs = await build_review_set(db=db, patient=patient, pharmacy_id=pharmacy_id)
    report = evaluate(rs)
    rsh = review_set_hash(rs)
    fh = findings_hash(report)
    existing = (await db.execute(
        select(InteractionReportCache).where(
            InteractionReportCache.patient_id == patient.id,
            InteractionReportCache.pharmacy_id == pharmacy_id,
        ))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    payload = report_to_dict(report)
    if existing:
        existing.review_set_hash = rsh
        existing.findings_hash = fh
        existing.report = payload
        existing.model_version = MODEL_VERSION
        existing.computed_at = now
    else:
        db.add(InteractionReportCache(
            patient_id=patient.id, pharmacy_id=pharmacy_id,
            review_set_hash=rsh, findings_hash=fh, report=payload,
            model_version=MODEL_VERSION, computed_at=now))
    await db.flush()
    return report, rsh, fh


async def recompute_for_patient_id(*, db: AsyncSession, patient_id, pharmacy_id) -> bool:
    """Isolated hook for intake/transition paths. Never raises into the caller."""
    try:
        patient = (await db.execute(
            select(Patient).where(Patient.id == patient_id,
                                  Patient.pharmacy_id == pharmacy_id,
                                  Patient.is_deleted == False))).scalar_one_or_none()  # noqa: E712
        if not patient:
            return False
        await recompute_and_cache(db=db, patient=patient, pharmacy_id=pharmacy_id)
        return True
    except Exception as exc:  # pragma: no cover
        logger.warning("[interaction] precompute failed for patient %s: %s", patient_id, exc)
        return False
