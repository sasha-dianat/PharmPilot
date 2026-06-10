"""
Phase 31 — DUR Override Documentation
========================================
When a DUR (Drug Utilization Review) alert fires and the pharmacist chooses
to override it, this module records:

  • The alert type (DDI, duplicate therapy, dose check, allergy, age-check…)
  • The override reason code (professional judgement, prescriber overrule, etc.)
  • Free-text clinical notes
  • Whether a prescriber callback was performed
  • The staff member and timestamp

These records form the clinical audit trail required by most pharmacy
regulatory bodies and are queryable per patient / per Rx.

No auto-dispense:  pharmacist must explicitly POST an override before the
                   system allows the dispensing step to proceed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user

router = APIRouter(tags=["dur overrides"])

# ─── Reason code catalogue ────────────────────────────────────────────────────

DUR_OVERRIDE_REASONS = [
    {"code": "PROF_JUDGMENT",   "label": "Professional judgement — risk-benefit acceptable"},
    {"code": "PRESCRIBER_AUTH", "label": "Prescriber authorised override (verbal/written)"},
    {"code": "DUPLICATE_OK",    "label": "Duplicate therapy intentional — different indication"},
    {"code": "ALLERGY_REFUTED", "label": "Documented allergy is refuted / mislabelled"},
    {"code": "DOSE_TITRATION",  "label": "High dose is intentional titration per protocol"},
    {"code": "DOSE_ADJUST",     "label": "Dose adjusted for renal/hepatic impairment"},
    {"code": "AGE_EXCEPTION",   "label": "Age-related alert — paediatric/geriatric dosing confirmed"},
    {"code": "PREGNANCY_OK",    "label": "Pregnancy risk accepted — benefit outweighs risk"},
    {"code": "SHORT_COURSE",    "label": "Short-course therapy — interaction risk negligible"},
    {"code": "PATIENT_CONSENT", "label": "Patient counselled and consented to risk"},
    {"code": "KNOWN_TOLERANCE", "label": "Patient has documented tolerance to combination"},
    {"code": "FORMULARY_REQ",   "label": "Formulary / insurance requirement overrides preferred agent"},
    {"code": "OTHER",           "label": "Other — see clinical notes"},
]

DUR_ALERT_TYPES = [
    "DDI",        # Drug-Drug Interaction
    "ALLERGY",    # Allergy / adverse drug reaction
    "DUPLICATE",  # Duplicate therapy
    "DOSE_HIGH",  # Dose above recommended range
    "DOSE_LOW",   # Dose below therapeutic range
    "AGE",        # Age-related warning (paediatric / geriatric)
    "PREGNANCY",  # Pregnancy / lactation
    "RENAL",      # Renal dosing alert
    "HEPATIC",    # Hepatic dosing alert
    "QTPROLONGATION", # QT interval prolongation
    "OTHER",
]

# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class CreateOverrideRequest(BaseModel):
    rx_id:              str
    alert_type:         str   = Field(..., description="DDI | ALLERGY | DUPLICATE | …")
    alert_detail:       str   = Field(..., description="Machine-generated alert message")
    reason_code:        str   = Field(..., description="Code from DUR_OVERRIDE_REASONS")
    clinical_notes:     Optional[str] = None
    prescriber_callback:bool  = False
    callback_note:      Optional[str] = None
    overridden_by:      str

class OverrideRecord(BaseModel):
    id:                 str
    rx_id:              str
    alert_type:         str
    alert_detail:       str
    reason_code:        str
    reason_label:       str
    clinical_notes:     Optional[str]
    prescriber_callback:bool
    callback_note:      Optional[str]
    overridden_by:      str
    created_at:         str

class OverrideListResponse(BaseModel):
    total:     int
    overrides: List[OverrideRecord]

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _ensure_table(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS dur_override_events (
            id                  TEXT PRIMARY KEY,
            rx_id               TEXT NOT NULL,
            alert_type          TEXT NOT NULL,
            alert_detail        TEXT NOT NULL,
            reason_code         TEXT NOT NULL,
            reason_label        TEXT NOT NULL,
            clinical_notes      TEXT,
            prescriber_callback BOOLEAN DEFAULT FALSE,
            callback_note       TEXT,
            overridden_by       TEXT NOT NULL,
            created_at          TIMESTAMPTZ DEFAULT now()
        )
    """))
    await db.commit()

def _reason_label(code: str) -> str:
    for r in DUR_OVERRIDE_REASONS:
        if r["code"] == code:
            return r["label"]
    return code

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/reason-codes")
async def list_reason_codes():
    return {"reason_codes": DUR_OVERRIDE_REASONS, "alert_types": DUR_ALERT_TYPES}


@router.post("/overrides", response_model=OverrideRecord, status_code=201)
async def create_override(
    body:     CreateOverrideRequest,
    db:       AsyncSession = Depends(get_db),
    current:  dict         = Depends(get_current_user),
):
    await _ensure_table(db)

    if body.alert_type not in DUR_ALERT_TYPES:
        raise HTTPException(400, f"Unknown alert_type '{body.alert_type}'")

    valid_codes = {r["code"] for r in DUR_OVERRIDE_REASONS}
    if body.reason_code not in valid_codes:
        raise HTTPException(400, f"Unknown reason_code '{body.reason_code}'")

    override_id  = str(uuid.uuid4())
    reason_label = _reason_label(body.reason_code)
    now          = datetime.now(timezone.utc)

    await db.execute(text("""
        INSERT INTO dur_override_events
            (id, rx_id, alert_type, alert_detail, reason_code, reason_label,
             clinical_notes, prescriber_callback, callback_note,
             overridden_by, created_at)
        VALUES
            (:id, :rx_id, :at, :ad, :rc, :rl,
             :cn, :cb, :cbn,
             :by, :now)
    """), dict(
        id=override_id, rx_id=body.rx_id,
        at=body.alert_type, ad=body.alert_detail,
        rc=body.reason_code, rl=reason_label,
        cn=body.clinical_notes, cb=body.prescriber_callback,
        cbn=body.callback_note, by=body.overridden_by, now=now,
    ))
    await db.commit()

    return OverrideRecord(
        id=override_id, rx_id=body.rx_id,
        alert_type=body.alert_type, alert_detail=body.alert_detail,
        reason_code=body.reason_code, reason_label=reason_label,
        clinical_notes=body.clinical_notes,
        prescriber_callback=body.prescriber_callback,
        callback_note=body.callback_note,
        overridden_by=body.overridden_by,
        created_at=now.isoformat(),
    )


@router.get("/overrides", response_model=OverrideListResponse)
async def list_overrides(
    rx_id:     Optional[str] = Query(None),
    patient_id:Optional[str] = Query(None),
    alert_type:Optional[str] = Query(None),
    limit:     int            = Query(50, ge=1, le=200),
    db:        AsyncSession   = Depends(get_db),
    _current:  dict           = Depends(get_current_user),
):
    await _ensure_table(db)

    filters = ["1=1"]
    params:  dict = {}
    if rx_id:
        filters.append("d.rx_id = :rx_id"); params["rx_id"] = rx_id
    if alert_type:
        filters.append("d.alert_type = :alert_type"); params["alert_type"] = alert_type
    if patient_id:
        filters.append("p.patient_id = :patient_id"); params["patient_id"] = patient_id

    # Join to prescriptions only when patient_id filter requested
    if patient_id:
        from_clause = """
            FROM dur_override_events d
            JOIN prescriptions p ON p.id = d.rx_id
        """
    else:
        from_clause = "FROM dur_override_events d"

    where = " AND ".join(filters)
    params["limit"] = limit

    count_result = await db.execute(
        text(f"SELECT COUNT(*) {from_clause} WHERE {where}"), params
    )
    total = count_result.scalar() or 0

    rows = await db.execute(text(f"""
        SELECT d.* {from_clause}
        WHERE  {where}
        ORDER  BY d.created_at DESC
        LIMIT  :limit
    """), params)

    overrides = []
    for row in rows.mappings():
        r = dict(row)
        overrides.append(OverrideRecord(
            id=r["id"], rx_id=r["rx_id"],
            alert_type=r["alert_type"], alert_detail=r["alert_detail"],
            reason_code=r["reason_code"], reason_label=r["reason_label"],
            clinical_notes=r.get("clinical_notes"),
            prescriber_callback=bool(r.get("prescriber_callback", False)),
            callback_note=r.get("callback_note"),
            overridden_by=r["overridden_by"],
            created_at=str(r["created_at"]),
        ))

    return OverrideListResponse(total=total, overrides=overrides)


@router.get("/overrides/{override_id}", response_model=OverrideRecord)
async def get_override(
    override_id: str,
    db:          AsyncSession = Depends(get_db),
    _current:    dict         = Depends(get_current_user),
):
    await _ensure_table(db)
    result = await db.execute(
        text("SELECT * FROM dur_override_events WHERE id = :id"),
        {"id": override_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, "Override record not found")
    r = dict(row)
    return OverrideRecord(
        id=r["id"], rx_id=r["rx_id"],
        alert_type=r["alert_type"], alert_detail=r["alert_detail"],
        reason_code=r["reason_code"], reason_label=r["reason_label"],
        clinical_notes=r.get("clinical_notes"),
        prescriber_callback=bool(r.get("prescriber_callback", False)),
        callback_note=r.get("callback_note"),
        overridden_by=r["overridden_by"],
        created_at=str(r["created_at"]),
    )
