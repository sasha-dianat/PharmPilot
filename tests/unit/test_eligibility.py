"""نسخه الکترونیک استعلام provider seam — default is null (local fallback)."""
import asyncio

from services.core.pricing_ir.eligibility import (
    get_eligibility_provider, NullEligibilityProvider,
)


def test_default_provider_is_null():
    p = get_eligibility_provider()
    assert isinstance(p, NullEligibilityProvider) and p.code == "null"


def test_null_provider_returns_none_so_callers_fall_back():
    p = get_eligibility_provider()
    res = asyncio.run(p.inquire(national_id="0012345678", insurer="tamin", ircs=["IRC1"]))
    assert res is None
