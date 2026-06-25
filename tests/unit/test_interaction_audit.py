from types import SimpleNamespace as NS
from datetime import datetime, timezone
from uuid import uuid4
import asyncio

from services.platform.routers import cds


def _dt(): return datetime(2026, 6, 25, 10, 0, tzinfo=timezone.utc)


def test_normalize_ack_reads_snapshot():
    row = NS(id=uuid4(), user_id=uuid4(), patient_id=uuid4(), created_at=_dt(),
             input_snapshot={"pharmacist": {"id": "p", "name": "Pat", "license": "L9"},
                             "physician": {"name": "Dr Who", "medical_council_id": "NP-7"},
                             "findings": [{"severity": "Contraindicated"}, {"severity": "Major"}]},
             output_snapshot={"findings_hash": "h"})
    r = cds._normalize_ack(row, "Ali Karimi")
    assert r["type"] == "ack" and r["pharmacist"]["name"] == "Pat"
    assert r["physician"]["medical_council_id"] == "NP-7"
    assert r["patient"]["name"] == "Ali Karimi"
    assert set(r["severities"]) == {"Contraindicated", "Major"}
    assert r["letter_id"] is None


def test_normalize_letter_reads_columns():
    lid = uuid4()
    row = NS(id=lid, pharmacist_id=uuid4(), pharmacist_name="Pat", pharmacist_license="L9",
             patient_id=uuid4(), prescriber_name="Dr Who", prescriber_council_id="NP-7",
             language="fa", source="deterministic", content_hash="abc", created_at=_dt())
    r = cds._normalize_letter(row, "Ali Karimi")
    assert r["type"] == "letter" and r["letter_id"] == str(lid)
    assert r["physician"]["council_id"] == "NP-7" and r["language"] == "fa"
    assert r["content_hash"] == "abc" and r["patient"]["name"] == "Ali Karimi"


class _Result:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _DB:
    """Returns the given result row-lists in the order the endpoint issues its
    queries. Pass one list per expected execute() call. Each row is a tuple
    (orm_row, first_name, last_name) like a joined select."""
    def __init__(self, *results): self._seq = [_Result(r) for r in results]
    async def execute(self, *_a, **_k): return self._seq.pop(0) if self._seq else _Result([])


def _ack_row(hour, sev):
    return NS(id=uuid4(), user_id=uuid4(), patient_id=uuid4(),
              created_at=datetime(2026, 6, 25, hour, tzinfo=timezone.utc),
              input_snapshot={"pharmacist": {"name": "Pat"}, "physician": {},
                              "findings": [{"severity": sev}]}, output_snapshot={})


def _letter_row(hour):
    return NS(id=uuid4(), pharmacist_id=uuid4(), pharmacist_name="Pat", pharmacist_license="L9",
              patient_id=uuid4(), prescriber_name="Dr Who", prescriber_council_id="NP-7",
              language="fa", source="deterministic", content_hash="abc",
              created_at=datetime(2026, 6, 25, hour, tzinfo=timezone.utc))


def test_interaction_audit_merges_and_sorts():
    db = _DB([(_ack_row(9, "Major"), "Ali", "Karimi")], [(_letter_row(11), "Ali", "Karimi")])
    staff = NS(id=uuid4(), pharmacy_id=uuid4())
    out = asyncio.run(cds.interaction_audit(
        patient_id=None, patient_name=None, council_id=None, from_=None, to=None,
        type=None, limit=50, offset=0, staff=staff, db=db))
    assert out["count"] == 2
    assert out["records"][0]["type"] == "letter"
    assert out["records"][1]["type"] == "ack"


def test_interaction_audit_type_filter_letter_only():
    db = _DB([(_letter_row(11), "Ali", "Karimi")])
    staff = NS(id=uuid4(), pharmacy_id=uuid4())
    out = asyncio.run(cds.interaction_audit(
        patient_id=None, patient_name=None, council_id=None, from_=None, to=None,
        type="letter", limit=50, offset=0, staff=staff, db=db))
    assert out["count"] == 1 and out["records"][0]["type"] == "letter"
