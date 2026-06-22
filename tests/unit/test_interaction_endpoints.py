import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return self
    def all(self): return self._rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _DB:
    """First execute() returns the patient (the endpoint's first query); all
    subsequent queries (review-set build, cache lookup) return empty → cache miss."""
    def __init__(self, patient): self._first = _Scalar([patient]); self._used = False; self.added = []
    async def execute(self, *_a, **_k):
        if not self._used:
            self._used = True
            return self._first
        return _Scalar([])
    def add(self, r): self.added.append(r)
    async def flush(self): pass


def test_get_interaction_report_recomputes_on_miss():
    pid = uuid4(); ph = uuid4()
    patient = NS(id=pid, pharmacy_id=ph, conditions=["peptic_ulcer_disease"],
                 date_of_birth=None, is_deleted=False)
    db = _DB(patient)
    staff = NS(pharmacy_id=ph, id=uuid4())
    out = asyncio.run(cds.get_interaction_report(pid, staff=staff, db=db))
    assert out["cached"] is False
    assert "findings_hash" in out and "report" in out


def test_ack_snapshot_captures_full_context():
    staff = NS(id=uuid4(), first_name="Pat", last_name="Pharm", pharmacist_license_number="LIC-9")
    patient = NS(id=uuid4(), first_name="Ali", last_name="Karimi", national_id="1234567890")
    prescriber = NS(first_name="Dr", last_name="Who", medical_council_id="NP-77", specialty="cardio")
    rx = NS(id=uuid4(), drug_name="warfarin")
    snap = cds._ack_snapshot(staff=staff, patient=patient, prescription=rx, prescriber=prescriber,
                             acknowledged=[{"rule_id": "dd:anticoagulant-nsaid", "severity": "Major"}])
    assert snap["pharmacist"]["license"] == "LIC-9"
    assert snap["physician"]["medical_council_id"] == "NP-77"
    assert snap["patient"]["national_id"] == "1234567890"
    assert snap["prescription"]["rx_id"] == str(rx.id)
    assert snap["findings"][0]["severity"] == "Major"
