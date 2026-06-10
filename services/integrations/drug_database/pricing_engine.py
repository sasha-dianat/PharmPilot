"""
Drug Pricing Engine
====================
Handles AWP / WAC / AAC / MAC pricing calculations and DIR fee impact modeling.

Industry pricing terminology:
  AWP  — Average Wholesale Price       (benchmark; typically WAC × 1.20 branded, WAC × 1.02–1.10 generic)
  WAC  — Wholesale Acquisition Cost    (manufacturer list price to wholesalers)
  AAC  — Actual Acquisition Cost       (invoice price the pharmacy actually pays)
  MAC  — Maximum Allowable Cost        (PBM ceiling for generic reimbursement)
  U&C  — Usual & Customary             (pharmacy's retail cash price)
  DIR  — Direct & Indirect Remuneration (PBM retroactive fee clawback, typically 3–15% of dispensing revenue)

Production note:
  Live AWP/WAC data requires a Medi-Span or Red Book data subscription.
  This engine provides fully correct calculation logic with deterministic
  demo pricing seeded from NDC hashes. Plug in a live feed by overriding
  get_price() to query the licensed database instead.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# ── Industry Standard Multipliers ─────────────────────────────────────────────
AWP_MULTIPLIER_BRANDED  = Decimal("1.20")  # branded AWP = WAC × 1.20
AWP_MULTIPLIER_GENERIC  = Decimal("1.07")  # generic AWP ≈ WAC × 1.02–1.10 (use 1.07 midpoint)
DEFAULT_DISPENSING_FEE  = Decimal("2.00")  # typical Medicaid/MAC dispensing fee
STANDARD_AWP_DISCOUNT   = Decimal("0.17")  # common PBM reimbursement: AWP − 17% for generics
BRAND_AWP_DISCOUNT      = Decimal("0.22")  # AWP − 22% for branded drugs

# DIR fee range bands
DIR_FEE_BANDS: dict[str, Decimal] = {
    "low":    Decimal("0.03"),   # 3%  — low-performing network tier
    "medium": Decimal("0.07"),   # 7%  — average DIR exposure
    "high":   Decimal("0.12"),   # 12% — aggressive PBM contract
    "zero":   Decimal("0.00"),   # DIR-exempt (cash, OTC, 340B)
}

# ── Known seed prices for common generics (WAC $/unit, package size, drug name) ─
# Source: approximate public market data; replace with Medi-Span for production
_KNOWN_PRICES: dict[str, tuple[str, float, float]] = {
    # labeler prefix → (drug_name, wac_per_unit, package_size)
    "00093": ("Teva Generics",          0.052,  90),
    "00378": ("Mylan",                  0.085,  30),
    "00781": ("Sandoz",                 0.118,  30),
    "00185": ("Ivax",                   0.180,  30),
    "00555": ("Barr",                   0.215,  30),
    "00228": ("Actavis",                0.092,  30),
    "60505": ("Apotex",                 0.063,  90),
    "16714": ("NovaBay",                0.148,  30),
    "00603": ("Qualitest",              0.248,  30),
    "00054": ("Roxane",                 1.195,  30),
    "00143": ("West-Ward",              0.175,  30),
    "00904": ("Major",                  0.105,  30),
    "45963": ("Amneal",                 0.090,  30),
    "68084": ("American Health Pkg",    0.220,  30),
    "43386": ("Rising",                 0.130,  30),
}


@dataclass
class DrugPrice:
    """
    Complete pricing record for a single NDC at a given quantity.
    All monetary values are Decimal for precision.
    """
    ndc:               str
    drug_name:         str
    manufacturer:      str
    package_size:      float          # units per commercial package
    is_generic:        bool

    unit_price_awp:    Decimal        # AWP per unit
    unit_price_wac:    Decimal        # WAC per unit
    unit_price_aac:    Decimal        # Actual Acquisition Cost per unit (invoice)
    dispensing_fee:    Decimal = field(default=DEFAULT_DISPENSING_FEE)

    brand_name:        Optional[str] = None
    generic_name:      Optional[str] = None

    # ── Reimbursement Calculations ────────────────────────────────────────────

    def reimbursement(self, qty: float, awp_discount: Optional[Decimal] = None) -> Decimal:
        """
        PBM/Medicaid reimbursement estimate.
        Formula: (AWP × (1 − discount%)) × qty + dispensing_fee
        Default discount: 17% generic, 22% branded.
        """
        discount = awp_discount or (
            STANDARD_AWP_DISCOUNT if self.is_generic else BRAND_AWP_DISCOUNT
        )
        per_unit = self.unit_price_awp * (1 - discount)
        return (per_unit * Decimal(str(qty)) + self.dispensing_fee).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def cogs(self, qty: float) -> Decimal:
        """Cost of goods sold: AAC × qty (what the pharmacy pays the wholesaler)."""
        return (self.unit_price_aac * Decimal(str(qty))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def gross_margin(self, qty: float) -> Decimal:
        """Gross margin = reimbursement − COGS (before DIR)."""
        return self.reimbursement(qty) - self.cogs(qty)

    def gross_margin_pct(self, qty: float) -> float:
        """Gross margin as a percentage of reimbursement."""
        reimb = self.reimbursement(qty)
        if reimb == 0:
            return 0.0
        return float((self.gross_margin(qty) / reimb * 100).quantize(Decimal("0.1")))

    def dir_fee(self, qty: float, tier: str = "medium") -> Decimal:
        """
        Estimated retroactive DIR fee clawback.
        DIR is applied AFTER dispensing by the PBM — it reduces net revenue.
        """
        rate = DIR_FEE_BANDS.get(tier, DIR_FEE_BANDS["medium"])
        return (self.reimbursement(qty) * rate).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    def net_margin(self, qty: float, dir_tier: str = "medium") -> Decimal:
        """Net margin = gross margin − DIR fee (true profitability)."""
        return self.gross_margin(qty) - self.dir_fee(qty, dir_tier)

    def net_margin_pct(self, qty: float, dir_tier: str = "medium") -> float:
        """Net margin as a percentage of reimbursement."""
        reimb = self.reimbursement(qty)
        if reimb == 0:
            return 0.0
        return float(
            (self.net_margin(qty, dir_tier) / reimb * 100).quantize(Decimal("0.1"))
        )

    def usual_and_customary(self, qty: float, markup: float = 1.35) -> Decimal:
        """
        Usual & Customary cash price = AAC × markup × qty.
        Default 35% markup is typical for independent pharmacies.
        """
        return (self.unit_price_aac * Decimal(str(markup)) * Decimal(str(qty))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    # ── Serialization ─────────────────────────────────────────────────────────

    def summary(self, qty: float = 30, dir_tier: str = "medium") -> dict:
        """Return a pricing summary dict for the given dispense quantity."""
        return {
            "ndc":              self.ndc,
            "drug_name":        self.drug_name,
            "brand_name":       self.brand_name,
            "generic_name":     self.generic_name,
            "manufacturer":     self.manufacturer,
            "is_generic":       self.is_generic,
            "package_size":     self.package_size,
            # Unit prices
            "unit_awp":         float(self.unit_price_awp),
            "unit_wac":         float(self.unit_price_wac),
            "unit_aac":         float(self.unit_price_aac),
            # 30-day fill economics
            "qty":              qty,
            "reimbursement":    float(self.reimbursement(qty)),
            "cogs":             float(self.cogs(qty)),
            "gross_margin":     float(self.gross_margin(qty)),
            "gross_margin_pct": self.gross_margin_pct(qty),
            "dir_fee":          float(self.dir_fee(qty, dir_tier)),
            "dir_tier":         dir_tier,
            "net_margin":       float(self.net_margin(qty, dir_tier)),
            "net_margin_pct":   self.net_margin_pct(qty, dir_tier),
            "cash_uc_price":    float(self.usual_and_customary(qty)),
            "dispensing_fee":   float(self.dispensing_fee),
        }


class DrugPricingEngine:
    """
    Pharmacy pricing engine.

    In production: integrate a Medi-Span or Red Book data feed and override
    get_price() to query the licensed NDC price table instead of the seed table.
    All calculation logic (margins, DIR, U&C) remains unchanged.
    """

    def get_price(self, ndc: str, drug_name: str = "Unknown Drug") -> DrugPrice:
        """
        Return a DrugPrice for the given NDC.
        Uses seed table for known labeler prefixes; derives prices deterministically
        for unknown NDCs so the engine always returns usable data.
        """
        ndc_clean = ndc.replace("-", "")
        prefix    = ndc_clean[:5]

        if prefix in _KNOWN_PRICES:
            manufacturer, wac_float, pkg_size = _KNOWN_PRICES[prefix]
        else:
            # Deterministic hash → realistic price range ($0.03 – $9.99 / unit)
            h          = int(hashlib.sha256(ndc_clean.encode()).hexdigest(), 16)
            wac_float  = round(0.03 + (h % 997) / 100, 4)
            pkg_size   = [14, 20, 28, 30, 60, 90, 100][h % 7]
            manufacturer = "Generic Manufacturer"

        wac = Decimal(str(wac_float))
        awp = (wac * AWP_MULTIPLIER_GENERIC).quantize(Decimal("0.0001"), ROUND_HALF_UP)

        # Wholesaler discount 5–15% off WAC (driven by volume tier)
        h2       = int(hashlib.md5(ndc_clean.encode()).hexdigest(), 16)
        discount = Decimal(str(0.05 + (h2 % 11) / 100))
        aac      = (wac * (1 - discount)).quantize(Decimal("0.0001"), ROUND_HALF_UP)

        return DrugPrice(
            ndc=ndc,
            drug_name=drug_name,
            manufacturer=manufacturer,
            package_size=float(pkg_size),
            is_generic=True,
            unit_price_awp=awp,
            unit_price_wac=wac,
            unit_price_aac=aac,
        )

    def batch_summary(
        self,
        ndc_name_pairs: list[tuple[str, str]],
        qty: float = 30,
        dir_tier: str = "medium",
    ) -> list[dict]:
        """Return pricing summaries for a list of (ndc, drug_name) pairs."""
        return [
            self.get_price(ndc, name).summary(qty=qty, dir_tier=dir_tier)
            for ndc, name in ndc_name_pairs
        ]

    def dir_impact_report(
        self,
        fills: list[dict],
        dir_tier: str = "medium",
    ) -> dict:
        """
        Calculate aggregate DIR fee exposure across a batch of dispensing records.

        fills: list of dicts with keys:
            ndc, drug_name, qty_dispensed, reimbursement_amount

        Returns total DIR exposure, per-drug breakdown (sorted by highest impact),
        and net revenue after DIR.
        """
        rate        = DIR_FEE_BANDS.get(dir_tier, DIR_FEE_BANDS["medium"])
        total_reimb = Decimal("0")
        total_dir   = Decimal("0")
        breakdown: list[dict] = []

        for fill in fills:
            reimb   = Decimal(str(fill.get("reimbursement_amount", 0)))
            dir_fee = (reimb * rate).quantize(Decimal("0.01"), ROUND_HALF_UP)
            total_reimb += reimb
            total_dir   += dir_fee
            breakdown.append({
                "ndc":            fill.get("ndc"),
                "drug_name":      fill.get("drug_name"),
                "qty":            fill.get("qty_dispensed"),
                "reimbursement":  float(reimb),
                "dir_fee":        float(dir_fee),
                "net":            float(reimb - dir_fee),
            })

        breakdown.sort(key=lambda x: x["dir_fee"], reverse=True)

        return {
            "dir_tier":            dir_tier,
            "dir_rate_pct":        float(rate * 100),
            "fill_count":          len(fills),
            "total_reimbursement": float(total_reimb),
            "total_dir_exposure":  float(total_dir),
            "net_after_dir":       float(total_reimb - total_dir),
            "dir_pct_of_revenue":  (
                float(total_dir / total_reimb * 100) if total_reimb else 0.0
            ),
            "top_dir_drugs":       breakdown[:20],
        }

    def margin_analysis_report(
        self,
        ndc_name_pairs: list[tuple[str, str]],
        qty: float = 30,
        dir_tier: str = "medium",
    ) -> dict:
        """
        Comprehensive margin analysis for a formulary/inventory list.
        Returns sorted breakdown with profitability flags.
        """
        summaries = self.batch_summary(ndc_name_pairs, qty=qty, dir_tier=dir_tier)

        # Flag unprofitable fills
        for s in summaries:
            s["profitable"] = s["net_margin"] > 0
            s["margin_flag"] = (
                "loss"    if s["net_margin"] < 0      else
                "thin"    if s["net_margin_pct"] < 5  else
                "healthy" if s["net_margin_pct"] < 20 else
                "strong"
            )

        summaries_sorted = sorted(summaries, key=lambda x: x["net_margin"])
        unprofitable     = [s for s in summaries if not s["profitable"]]
        thin_margin      = [s for s in summaries if s["margin_flag"] == "thin"]

        total_reimb = sum(s["reimbursement"] for s in summaries)
        total_net   = sum(s["net_margin"]    for s in summaries)

        return {
            "drug_count":        len(summaries),
            "qty_per_fill":      qty,
            "dir_tier":          dir_tier,
            "total_reimbursement": round(total_reimb, 2),
            "total_net_margin":    round(total_net,   2),
            "portfolio_margin_pct": round(
                total_net / total_reimb * 100 if total_reimb else 0.0, 1
            ),
            "unprofitable_count":  len(unprofitable),
            "thin_margin_count":   len(thin_margin),
            "drugs":               summaries_sorted,
        }
