import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from services.core.pharmacy_workflow.patient_context import load_active_medications_and_diagnoses


def _result(rows):
    result = MagicMock()
    result.mappings.return_value.all.return_value = rows
    return result


def _make_db(prescriptions=None, notes=None):
    db = AsyncMock()
    prescriptions = prescriptions or []
    notes = notes or []

    async def _execute(query, params=None):
        q = str(query).lower()
        if "from prescriptions" in q:
            assert "status not in ('cancelled','dispensed')" in q
            rows = [
                {"drug_name": row["drug_name"]}
                for row in prescriptions
                if row["patient_id"] == params["id"] and row["status"] not in ("cancelled", "dispensed")
            ]
            return _result(rows)
        if "from clinical_notes" in q:
            assert "note_type in ('condition','diagnosis','inherited_condition')" in q
            rows = [
                {"note_type": row["note_type"], "content": row["content"]}
                for row in notes
                if row["patient_id"] == params["id"]
                and row["note_type"] in ("condition", "diagnosis", "inherited_condition")
            ]
            return _result(rows)
        return _result([])

    db.execute = AsyncMock(side_effect=_execute)
    return db


def test_load_active_medications_and_diagnoses_empty_patient():
    patient_id = str(uuid4())
    db = _make_db()

    result = asyncio.run(load_active_medications_and_diagnoses(db, patient_id))

    assert result == {
        "active_medications": [],
        "diagnoses": [],
        "inherited_conditions": [],
    }


def test_load_active_medications_excludes_terminal_prescriptions():
    patient_id = str(uuid4())
    db = _make_db(prescriptions=[
        {"patient_id": patient_id, "drug_name": "Amlodipine", "status": "active"},
        {"patient_id": patient_id, "drug_name": "Atorvastatin", "status": "ready"},
        {"patient_id": patient_id, "drug_name": "Lisinopril", "status": "cancelled"},
        {"patient_id": patient_id, "drug_name": "Metformin", "status": "dispensed"},
    ])

    result = asyncio.run(load_active_medications_and_diagnoses(db, patient_id))

    assert result["active_medications"] == [
        {"drug_name": "Amlodipine"},
        {"drug_name": "Atorvastatin"},
    ]
    assert result["diagnoses"] == []
    assert result["inherited_conditions"] == []


def test_load_diagnoses_buckets_supported_clinical_note_types():
    patient_id = str(uuid4())
    db = _make_db(notes=[
        {"patient_id": patient_id, "note_type": "condition", "content": "Hypertension"},
        {"patient_id": patient_id, "note_type": "diagnosis", "content": "Type 2 diabetes"},
        {"patient_id": patient_id, "note_type": "inherited_condition", "content": "G6PD deficiency"},
        {"patient_id": patient_id, "note_type": "other", "content": "Ignore me"},
    ])

    result = asyncio.run(load_active_medications_and_diagnoses(db, patient_id))

    assert result["active_medications"] == []
    assert result["diagnoses"] == ["Hypertension", "Type 2 diabetes"]
    assert result["inherited_conditions"] == ["G6PD deficiency"]
