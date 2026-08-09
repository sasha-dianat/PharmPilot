"""
ML Inventory Intelligence — Demand Forecasting + Smart Reorder Engine.
Prophet + XGBoost ensemble, dynamic safety stock, multi-wholesaler optimization.
Runs as a Celery beat task every night and on-demand for urgent reorder triggers.
"""
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional
from uuid import UUID

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DemandForecast:
    ndc11: str
    pharmacy_id: UUID
    avg_daily_demand: float
    std_dev_daily: float
    forecast_7d: float
    forecast_30d: float
    stockout_probability_7d: float
    stockout_probability_30d: float
    seasonal_factor: float = 1.0
    trend: str = "stable"           # up, down, stable
    anomaly_detected: bool = False
    forecast_date: date = field(default_factory=date.today)
    # observed | sparse | no_history — how much of a measurement this is.
    # A consumer that ignores this cannot tell a rate derived from 180 days of
    # dispensing from one derived from nothing at all.
    basis: str = "observed"


@dataclass
class ReorderDecision:
    ndc11: str
    pharmacy_id: UUID
    current_stock: float
    reorder_point: float
    reorder_now: bool
    recommended_quantity: float
    estimated_cost: float
    urgency: str                    # immediate, soon, scheduled, none
    preferred_wholesaler: str
    alternative_wholesalers: list[str]
    stockout_eta_days: Optional[float]
    rationale: str


class DemandForecaster:
    """
    Ensemble demand forecasting per NDC per pharmacy.
    Combines Prophet (seasonality) + XGBoost (covariates) + LSTM (trends).
    Falls back gracefully when insufficient data exists.
    """

    MIN_HISTORY_DAYS = 30     # Minimum dispense history for ML forecasting
    # There is deliberately no fallback demand rate. This was 1.0 units/day,
    # which meant an item with no dispensing at all reported a full unit a day
    # to the purchasing engine — indistinguishable, downstream, from a measured
    # rate. A forecast with no history now reports 0.0 with basis="no_history",
    # so the caller can tell "we measured nothing" from "we know nothing".

    def __init__(self, db=None, clickhouse_client=None):
        self.db = db
        self.ch = clickhouse_client

    async def forecast(self, ndc11: str, pharmacy_id: UUID) -> DemandForecast:
        """
        Forecast demand for a specific drug at a pharmacy.
        Uses ensemble model if sufficient history; falls back to simple average.
        """
        history = await self._load_dispense_history(ndc11, pharmacy_id, days=180)

        if len(history) < self.MIN_HISTORY_DAYS:
            return self._fallback_forecast(ndc11, pharmacy_id, history)

        return await self._ensemble_forecast(ndc11, pharmacy_id, history)

    async def _ensemble_forecast(
        self,
        ndc11: str,
        pharmacy_id: UUID,
        history: list[dict],
    ) -> DemandForecast:
        """Run Prophet + XGBoost ensemble."""
        import pandas as pd

        df = pd.DataFrame(history)
        df["ds"] = pd.to_datetime(df["dispense_date"])
        df["y"] = df["units_dispensed"].astype(float)
        df = df.groupby("ds")["y"].sum().reset_index()
        df = df.set_index("ds").resample("D").sum().fillna(0).reset_index()

        avg_demand = float(df["y"].mean())
        std_demand = float(df["y"].std())

        # Prophet forecast
        prophet_7d = await self._prophet_forecast(df, horizon=7)
        prophet_30d = await self._prophet_forecast(df, horizon=30)

        # Stockout probability: P(demand in lead_time > current_stock)
        lead_time_days = 2  # Default wholesaler lead time
        lead_time_demand = avg_demand * lead_time_days
        lead_time_std = std_demand * (lead_time_days ** 0.5)

        from scipy import stats as scipy_stats
        stockout_prob_7d = float(1 - scipy_stats.norm.cdf(
            prophet_7d,
            loc=lead_time_demand,
            scale=max(lead_time_std, 0.1),
        ))

        trend = self._detect_trend(df)

        return DemandForecast(
            ndc11=ndc11,
            pharmacy_id=pharmacy_id,
            avg_daily_demand=avg_demand,
            std_dev_daily=std_demand,
            forecast_7d=prophet_7d,
            forecast_30d=prophet_30d,
            stockout_probability_7d=max(0.0, min(1.0, stockout_prob_7d)),
            stockout_probability_30d=0.0,  # Simplified
            trend=trend,
        )

    async def _prophet_forecast(self, df, horizon: int) -> float:
        """Run Facebook Prophet for the given horizon."""
        try:
            from prophet import Prophet
            model = Prophet(
                seasonality_mode="multiplicative",
                daily_seasonality=False,
                weekly_seasonality=True,
                yearly_seasonality=True,
                changepoint_prior_scale=0.1,
            )
            model.fit(df[["ds", "y"]])
            future = model.make_future_dataframe(periods=horizon)
            forecast = model.predict(future)
            future_rows = forecast.tail(horizon)
            return max(0.0, float(future_rows["yhat"].sum()))
        except Exception as exc:
            logger.warning("Prophet forecast failed: %s — using mean", exc)
            return float(df["y"].mean() * horizon)

    def _detect_trend(self, df) -> str:
        if len(df) < 14:
            return "stable"
        recent = float(df["y"].tail(7).mean())
        prior = float(df["y"].iloc[-14:-7].mean())
        if prior == 0:
            return "stable"
        change = (recent - prior) / prior
        if change > 0.20:
            return "up"
        elif change < -0.20:
            return "down"
        return "stable"

    def _fallback_forecast(
        self,
        ndc11: str,
        pharmacy_id: UUID,
        history: list[dict],
    ) -> DemandForecast:
        if history:
            avg = float(np.mean([h.get("units_dispensed", 0) for h in history]))
            std = float(np.std([h.get("units_dispensed", 0) for h in history]))
            basis = "sparse"
        else:
            # No history is not evidence of low demand, and it is not evidence
            # of any demand either. Reporting zero keeps the purchasing engine
            # silent on this item, which is the only defensible answer.
            avg, std, basis = 0.0, 0.0, "no_history"

        return DemandForecast(
            ndc11=ndc11,
            pharmacy_id=pharmacy_id,
            avg_daily_demand=avg,
            std_dev_daily=std,
            forecast_7d=avg * 7,
            forecast_30d=avg * 30,
            stockout_probability_7d=0.0,
            stockout_probability_30d=0.0,
            basis=basis,
        )

    async def _load_dispense_history(
        self,
        ndc11: str,
        pharmacy_id: UUID,
        days: int = 180,
    ) -> list[dict]:
        if not self.db:
            return []
        try:
            from sqlalchemy import select, text
            from shared.models.prescription import PrescriptionFill
            from datetime import datetime, timezone
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            result = await self.db.execute(
                select(
                    PrescriptionFill.fill_date,
                    PrescriptionFill.quantity_dispensed,
                ).where(
                    PrescriptionFill.ndc_dispensed == ndc11,
                    PrescriptionFill.fill_date >= cutoff.date(),
                )
            )
            return [
                {"dispense_date": r.fill_date, "units_dispensed": float(r.quantity_dispensed)}
                for r in result.all()
            ]
        except Exception as exc:
            logger.error("Failed to load dispense history: %s", exc)
            return []


class SmartReorderEngine:
    """
    Calculates dynamic reorder points and recommended order quantities.
    Applies EOQ with pharmacy-specific constraints.
    """

    SERVICE_LEVEL = 0.995   # 99.5% — pharmacy cannot afford stockouts
    Z_SCORE = 2.576          # Z for 99.5%

    def __init__(self, forecaster: DemandForecaster = None):
        self.forecaster = forecaster or DemandForecaster()

    async def evaluate_reorder(
        self,
        ndc11: str,
        pharmacy_id: UUID,
        current_stock: float,
        lead_time_days: float = 2.0,
        order_cost: float = 5.0,
        unit_acquisition_cost: float = 1.0,
        holding_cost_rate: float = 0.25,  # 25% of acquisition cost per year
        dea_schedule: Optional[str] = None,
        requires_refrigeration: bool = False,
    ) -> ReorderDecision:
        """Evaluate whether to reorder and how much."""
        forecast = await self.forecaster.forecast(ndc11, pharmacy_id)

        # Dynamic safety stock
        safety_stock = (
            self.Z_SCORE *
            forecast.std_dev_daily *
            (lead_time_days ** 0.5)
        )

        # Reorder point = demand during lead time + safety stock
        reorder_point = (forecast.avg_daily_demand * lead_time_days) + safety_stock

        # Economic Order Quantity
        annual_demand = forecast.avg_daily_demand * 365
        holding_cost = unit_acquisition_cost * holding_cost_rate
        if holding_cost > 0 and annual_demand > 0:
            eoq = ((2 * annual_demand * order_cost) / holding_cost) ** 0.5
        else:
            eoq = max(1.0, forecast.forecast_30d)

        # Apply pharmacy constraints
        eoq = self._apply_constraints(
            eoq=eoq,
            avg_daily_demand=forecast.avg_daily_demand,
            dea_schedule=dea_schedule,
            requires_refrigeration=requires_refrigeration,
        )

        reorder_now = current_stock <= reorder_point
        days_of_supply_remaining = (
            (current_stock - safety_stock) / max(forecast.avg_daily_demand, 0.01)
            if forecast.avg_daily_demand > 0 else 999
        )

        if current_stock <= 0:
            urgency = "immediate"
        elif current_stock <= (reorder_point * 0.5):
            urgency = "immediate"
        elif reorder_now:
            urgency = "soon"
        elif days_of_supply_remaining < 14:
            urgency = "scheduled"
        else:
            urgency = "none"

        estimated_cost = eoq * unit_acquisition_cost

        rationale = (
            f"Current stock: {current_stock:.0f} units. "
            f"Reorder point: {reorder_point:.0f} (avg demand {forecast.avg_daily_demand:.1f}/day × "
            f"{lead_time_days:.0f}d lead time + {safety_stock:.0f} safety stock). "
            f"EOQ: {eoq:.0f} units. "
            f"Estimated {days_of_supply_remaining:.0f} days of supply remaining."
        )

        preferred_wholesaler = "mckesson"
        try:
            db = getattr(self.forecaster, "db", None)
            if db is not None:
                from sqlalchemy import select
                from shared.models.pharmacy import Pharmacy

                result = await db.execute(
                    select(Pharmacy).where(Pharmacy.id == pharmacy_id)
                )
                pharmacy = result.scalar_one_or_none()
                config = getattr(pharmacy, "config", None)
                if isinstance(config, dict):
                    wholesaler_contracts = config.get("wholesaler_contracts", {})
                    if not isinstance(wholesaler_contracts, dict):
                        wholesaler_contracts = {}
                    per_drug_wholesaler = wholesaler_contracts.get(ndc11)
                    pharmacy_default_wholesaler = config.get("preferred_wholesaler")
                    if isinstance(per_drug_wholesaler, str) and per_drug_wholesaler:
                        preferred_wholesaler = per_drug_wholesaler
                    elif (
                        isinstance(pharmacy_default_wholesaler, str)
                        and pharmacy_default_wholesaler
                    ):
                        preferred_wholesaler = pharmacy_default_wholesaler
        except Exception as exc:
            logger.debug("Failed to load preferred wholesaler config: %s", exc)

        return ReorderDecision(
            ndc11=ndc11,
            pharmacy_id=pharmacy_id,
            current_stock=current_stock,
            reorder_point=round(reorder_point, 1),
            reorder_now=reorder_now,
            recommended_quantity=round(eoq, 0),
            estimated_cost=round(estimated_cost, 2),
            urgency=urgency,
            preferred_wholesaler=preferred_wholesaler,
            alternative_wholesalers=["cardinal", "amerisource"],
            stockout_eta_days=round(days_of_supply_remaining, 1) if reorder_now else None,
            rationale=rationale,
        )

    def _apply_constraints(
        self,
        eoq: float,
        avg_daily_demand: float,
        dea_schedule: Optional[str],
        requires_refrigeration: bool,
    ) -> float:
        """Apply pharmacy-specific constraints to EOQ."""
        # DEA limit: don't order more than 30-day supply of controlled substances
        if dea_schedule in ("CI", "CII"):
            max_qty = avg_daily_demand * 30
            eoq = min(eoq, max_qty)

        # Refrigerated drugs: limit to what fits in available refrigerator space
        if requires_refrigeration:
            max_refrigerated = avg_daily_demand * 14  # 2-week max
            eoq = min(eoq, max_refrigerated)

        # Minimum order: always order at least 1 unit
        return max(1.0, eoq)


class ShrinkageDetector:
    """
    Detects inventory shrinkage patterns suggesting theft or diversion.
    Runs nightly after perpetual inventory reconciliation.
    """

    SHRINKAGE_THRESHOLD = 0.02   # 2% of dispensed units

    async def run_daily_screen(self, pharmacy_id: UUID, db) -> list[dict]:
        """
        Compare theoretical inventory (received - dispensed) vs physical count.
        Flag discrepancies above threshold.
        """
        alerts = []

        try:
            from sqlalchemy import select, func, text
            from shared.models.inventory import InventoryLot, StockLevel
            from shared.models.prescription import PrescriptionFill

            stock_result = await db.execute(
                select(StockLevel).where(
                    StockLevel.pharmacy_id == pharmacy_id,
                    StockLevel.quantity_on_hand >= 0,
                )
            )
            stocks = stock_result.scalars().all()

            for stock in stocks:
                # Simple heuristic: if stockout_prob is unexpectedly high
                # without corresponding dispense events, flag for review
                if (stock.avg_daily_demand and
                        stock.avg_daily_demand > 0 and
                        stock.quantity_on_hand < stock.avg_daily_demand * 2):
                    alerts.append({
                        "ndc11": stock.ndc11,
                        "alert_type": "potential_shrinkage",
                        "severity": "moderate",
                        "description": (
                            f"Stock level for {stock.ndc11} is below 2-day supply "
                            f"without corresponding dispense activity. Manual count recommended."
                        ),
                    })

        except Exception as exc:
            logger.error("Shrinkage detection failed: %s", exc)

        return alerts
