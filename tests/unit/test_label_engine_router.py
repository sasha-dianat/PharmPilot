"""
Unit tests for the label engine router (services/platform/routers/label_engine.py).

Covers two regressions found in code review:
1. _load_rx_for_label was selecting columns/joining tables that don't exist on
   the real schema (rx.ndc11/rx.dosage_form/rx.fill_number, "providers" table)
   instead of the actual prescriptions.ndc / prescriptions.drug_form /
   prescription_fills.fill_number / "prescribers" table — this 500'd at runtime.
2. _load_rx_for_label did not scope by pharmacy_id, allowing any authenticated
   staff member to fetch label PHI for another pharmacy's prescriptions.

Tests run without a real DB by stubbing the AsyncSession, following the same
pattern as tests/unit/test_intake_precompute.py.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from services.platform.routers.label_engine import _load_rx_for_label


def _row(**overrides) -> dict:
    """A row shaped like the corrected SQL SELECT in _load_rx_for_label —
    real column names (ndc, drug_form, latest_fill_number from the
    prescription_fills LATERAL join, prescribers.*) rather than the bogus
    ndc11/dosage_form/fill_number/providers.* names the old query used."""
    base = {
        "id": str(uuid4()),
        "rx_number": "RX-1001",
        "fill_date": None,
        "drug_name": "Lisinopril",
        "drug_strength": "10mg",
        "drug_form": "tablet",
        "ndc": "00071015523",
        "quantity_prescribed": 30,
        "days_supply": 30,
        "refills_remaining": 2,
        "sig_text": "Take 1 tablet daily",
        "is_controlled": False,
        "dea_schedule": None,
        "patient_first": "Jane",
        "patient_last": "Doe",
        "date_of_birth": "1980-01-01",
        "presc_first": "John",
        "presc_last": "Smith",
        "presc_npi": "1234567890",
        "ph_name": "PharmPilot Pharmacy",
        "address_line1": "123 Main St",
        "city": "Springfield",
        "state": "IL",
        "zip_code": "62704",
        "phone": "555-0100",
        "ph_npi": "9999999999",
        "latest_fill_number": 2,
    }
    base.update(overrides)
    return base


def _make_db(row: dict | None, *, capture: dict | None = None):
    """Stub AsyncSession.execute — returns `row` (or None → 404) and records
    the SQL text + bound params passed in, so tests can assert on the query."""
    db = AsyncMock()

    async def _execute(query, params=None):
        if capture is not None:
            capture["sql"] = str(query)
            capture["params"] = params
        r = MagicMock()
        r.mappings.return_value.first.return_value = row
        return r

    db.execute = AsyncMock(side_effect=_execute)
    return db


PHARMACY_ID = str(uuid4())
RX_ID = str(uuid4())


def test_load_rx_for_label_maps_real_schema_columns():
    """The dict handed to LabelGenerator must be built from the actual
    prescriptions/prescription_fills columns, not the old bogus names."""
    row = _row()
    db = _make_db(row)

    result = asyncio.run(_load_rx_for_label(RX_ID, PHARMACY_ID, db))

    rx = result["rx"]
    assert rx["dosage_form"] == "tablet"          # mapped from row["drug_form"]
    assert rx["ndc11"] == "00071015523"           # mapped from row["ndc"]
    assert rx["fill_number"] == 2                 # mapped from row["latest_fill_number"]
    assert rx["prescriber"]["first_name"] == "John"
    assert rx["prescriber"]["npi"] == "1234567890"
    assert result["patient"]["first_name"] == "Jane"
    assert result["pharmacy"]["name"] == "PharmPilot Pharmacy"


def test_load_rx_for_label_defaults_fill_number_when_no_fills_exist():
    """A brand-new Rx with no prescription_fills rows yet should default to 1,
    matching the original `row["fill_number"] or 1` fallback behavior."""
    row = _row(latest_fill_number=None)
    db = _make_db(row)

    result = asyncio.run(_load_rx_for_label(RX_ID, PHARMACY_ID, db))
    assert result["rx"]["fill_number"] == 1


def test_load_rx_for_label_query_is_scoped_to_callers_pharmacy():
    """The SQL must filter on rx.pharmacy_id, and the bound params must carry
    the authenticated staff's pharmacy_id — never just the bare rx_id."""
    row = _row()
    capture: dict = {}
    db = _make_db(row, capture=capture)

    asyncio.run(_load_rx_for_label(RX_ID, PHARMACY_ID, db))

    sql = capture["sql"].lower()
    assert "pharmacy_id" in sql
    assert "prescribers" in sql      # not the bogus "providers" table
    assert "providers" not in sql.replace("prescribers", "")
    assert capture["params"]["pharmacy_id"] == PHARMACY_ID
    assert capture["params"]["rx_id"] == RX_ID


def test_load_rx_for_label_404s_when_rx_belongs_to_another_pharmacy():
    """Cross-tenant lookups must 404 — the WHERE clause filters by pharmacy_id,
    so a staff member from pharmacy B querying pharmacy A's Rx gets no row."""
    db = _make_db(row=None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_load_rx_for_label(RX_ID, PHARMACY_ID, db))

    assert exc_info.value.status_code == 404
