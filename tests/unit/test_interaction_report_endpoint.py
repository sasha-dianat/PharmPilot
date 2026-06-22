import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Result:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _FakeDB:
    # Order of execute() calls: patient, rx, meds, labs, allergies
    def __init__(self, patient, rx, meds):
        self._seq = [_Result([patient]), _Result(rx), _Result(meds), _Result([]), _Result([])]
    async def execute(self, *_a, **_k):
        return self._seq.pop(0)


def test_interaction_report_endpoint_returns_findings():
    pid = uuid4()
    patient = NS(id=pid, pharmacy_id=uuid4(), conditions=["peptic_ulcer_disease"],
                 date_of_birth=None, is_deleted=False)
    rx = [NS(drug_name="ibuprofen")]
    db = _FakeDB(patient, rx, meds=[])
    staff = NS(pharmacy_id=patient.pharmacy_id, id=uuid4())
    body = cds.InteractionReportRequest(patient_id=pid)
    out = asyncio.run(cds.interaction_report(body, staff=staff, db=db))
    assert "summary" in out and "findings" in out
    assert any(f["type"] == "drug_disease" for f in out["findings"])
    assert out["findings"][0]["severity"] in {"Contraindicated", "Major", "Moderate", "Minor"}
