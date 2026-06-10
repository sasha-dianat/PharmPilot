"""Shared clinical context loading for pharmacy workflow reasoning."""
from __future__ import annotations

from typing import Any

from sqlalchemy import text


async def load_active_medications_and_diagnoses(db: Any, patient_id: Any) -> dict:
    ctx: dict = {
        "active_medications": [],
        "diagnoses": [],
        "inherited_conditions": [],
    }
    # active meds (derived from non-terminal prescriptions)
    try:
        rows = (await db.execute(
            text("""SELECT DISTINCT drug_name FROM prescriptions
                    WHERE patient_id=:id AND status NOT IN ('cancelled','dispensed')"""),
            {"id": str(patient_id)})).mappings().all()
        ctx["active_medications"] = [{"drug_name": r["drug_name"]} for r in rows if r["drug_name"]]
    except Exception:
        pass
    # diagnoses + inherited conditions from clinical_notes
    try:
        rows = (await db.execute(
            text("""SELECT note_type, content FROM clinical_notes
                    WHERE patient_id=:id AND note_type IN ('condition','diagnosis','inherited_condition')"""),
            {"id": str(patient_id)})).mappings().all()
        for r in rows:
            if r["note_type"] == "inherited_condition":
                ctx["inherited_conditions"].append(r["content"])
            else:
                ctx["diagnoses"].append(r["content"])
    except Exception:
        pass
    return ctx
