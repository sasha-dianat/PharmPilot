from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services.ai.inventory_intelligence.forecaster import (
    DemandForecast,
    SmartReorderEngine,
)


NDC11 = "00071015523"
PHARMACY_ID = uuid4()


class _StubForecaster:
    def __init__(self, db):
        self.db = db

    async def forecast(self, ndc11, pharmacy_id):
        return DemandForecast(
            ndc11=ndc11,
            pharmacy_id=pharmacy_id,
            avg_daily_demand=10.0,
            std_dev_daily=1.0,
            forecast_7d=70.0,
            forecast_30d=300.0,
            stockout_probability_7d=0.0,
            stockout_probability_30d=0.0,
        )


def _make_db(config=None, *, raises=False):
    db = AsyncMock()
    if raises:
        db.execute = AsyncMock(side_effect=RuntimeError("db unavailable"))
        return db

    result = MagicMock()
    result.scalar_one_or_none.return_value = SimpleNamespace(config=config)
    db.execute = AsyncMock(return_value=result)
    return db


async def _evaluate(db, ndc11=NDC11):
    engine = SmartReorderEngine(forecaster=_StubForecaster(db))
    return await engine.evaluate_reorder(
        ndc11=ndc11,
        pharmacy_id=PHARMACY_ID,
        current_stock=5.0,
    )


@pytest.mark.asyncio
async def test_reorder_uses_per_drug_wholesaler_override():
    db = _make_db({
        "preferred_wholesaler": "cardinal",
        "wholesaler_contracts": {
            NDC11: "amerisource",
        },
    })

    decision = await _evaluate(db)

    assert decision.preferred_wholesaler == "amerisource"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_uses_pharmacy_wide_preferred_wholesaler():
    db = _make_db({
        "preferred_wholesaler": "cardinal",
        "wholesaler_contracts": {},
    })

    decision = await _evaluate(db)

    assert decision.preferred_wholesaler == "cardinal"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_falls_back_to_mckesson_without_wholesaler_config():
    db = _make_db({})

    decision = await _evaluate(db)

    assert decision.preferred_wholesaler == "mckesson"
    db.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_reorder_falls_back_to_mckesson_without_db_session():
    decision = await _evaluate(db=None)

    assert decision.preferred_wholesaler == "mckesson"


@pytest.mark.asyncio
async def test_reorder_falls_back_to_mckesson_when_db_lookup_raises():
    db = _make_db(raises=True)

    decision = await _evaluate(db)

    assert decision.preferred_wholesaler == "mckesson"
    db.execute.assert_awaited_once()
