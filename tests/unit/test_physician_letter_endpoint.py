# tests/unit/test_physician_letter_endpoint.py
import asyncio
from types import SimpleNamespace as NS
from uuid import uuid4

from services.platform.routers import cds


class _Scalar:
    def __init__(self, rows): self._rows = rows
    def scalar_one_or_none(self): return self._rows[0] if self._rows else None


class _DB:
    """Returns patient, then pharmacy, for the two lookups; records the added letter."""
    def __init__(self, patient, pharmacy):
        self._seq = [_Scalar([patient]), _Scalar([pharmacy])]; self.added = []
    async def execute(self, *_a, **_k): return self._seq.pop(0) if self._seq else _Scalar([])
    def add(self, r): self.added.append(r)
    async def flush(self): pass


def test_generate_physician_letter_persists_and_substitutes(monkeypatch):
    # Force the deterministic path (no network) by making the LLM report degraded.
    from services.ai.clinical_decision_support.physician_letter import compose as comp
    async def _gen(*a, **k): return NS(text="", degraded=True, provider="none", model="")
    monkeypatch.setattr(comp.local_llm, "generate", _gen)

    ph = uuid4()
    patient = NS(id=uuid4(), pharmacy_id=ph, first_name="Ali", last_name="Karimi",
                 national_id="1234567890", is_deleted=False)
    pharmacy = NS(id=ph, name="Central Pharmacy")
    db = _DB(patient, pharmacy)
    staff = NS(id=uuid4(), pharmacy_id=ph, first_name="Pat", last_name="Pharm",
               pharmacist_license_number="LIC-9")
    body = cds.PhysicianLetterRequest(
        patient_id=patient.id, rx_id=None, language="fa",
        physician_name="Dr Who", council_id="NP-77",
        findings=[{"participants": [{"name": "warfarin"}, {"name": "phenelzine"}],
                   "mechanism": "MAOI + serotonergic.", "severity": "Contraindicated"}])
    out = asyncio.run(cds.generate_physician_letter(body, staff=staff, db=db))
    assert out["language"] == "fa" and out["id"]
    letter = out["letter_text"]
    assert "Ali Karimi" in letter and "NP-77" in letter and "{{" not in letter
    # a letter row + an audit row were added
    assert any(getattr(r, "letter_text", None) for r in db.added)
