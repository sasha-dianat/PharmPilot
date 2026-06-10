"""
Drug Database Integration Services
===================================
RxNorm  — NLM drug normalization (NDC ↔ RxCUI, name resolution, therapeutic alternatives)
OpenFDA — FDA drug labels, active recalls, FAERS adverse events
Pricing — AWP / WAC / AAC / MAC margin calculations + DIR fee impact modeling
Coupon  — GoodRx-style discount card comparison + manufacturer assistance matching
"""
from .rxnorm_client import RxNormClient
from .openfda_client import OpenFDAClient
from .pricing_engine import DrugPricingEngine, DrugPrice
from .coupon_service import CouponService

__all__ = [
    "RxNormClient",
    "OpenFDAClient",
    "DrugPricingEngine",
    "DrugPrice",
    "CouponService",
]
