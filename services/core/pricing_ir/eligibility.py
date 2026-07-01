"""نسخه الکترونیک استعلام — insurance eligibility / covered-price inquiry.

At the counter, Iranian pharmacy software calls the insurer's e-prescription
system by the patient's کد ملی to get the AUTHORITATIVE سهم بیمار / سهم بیمه for
the basket (rather than only computing it locally). Those APIs (تأمین اجتماعی,
بیمه سلامت, نیروهای مسلح) require per-pharmacy credentials and are not publicly
reachable, so this module defines the integration seam + a null provider used
until real credentials are wired. The quote flow degrades gracefully: when no
provider is configured it falls back to the local engine computation.

To wire a real insurer: subclass EligibilityProvider, implement inquire(), and
select it via PRICE_ELIGIBILITY_PROVIDER.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class EligibilityLine:
    irc: str
    covered: bool
    reference_price: int | None      # insurer-committed price (قیمت تعهد) per unit
    patient_share: int | None        # سهم بیمار for this line (Rial), if the API returns it
    insurer_share: int | None        # سهم بیمه for this line (Rial), if returned


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    insurer: str
    tracking_code: str | None        # رهگیری code from the e-prescription system
    lines: list[EligibilityLine]
    message: str | None = None


class EligibilityProvider(ABC):
    """Adapter to an insurer's e-prescription استعلام API."""
    code: str = "base"

    @abstractmethod
    async def inquire(self, *, national_id: str, insurer: str,
                      ircs: list[str]) -> EligibilityResult | None:
        """Return the insurer's authoritative eligibility/split, or None when the
        inquiry can't be made (not configured / offline) so callers fall back."""
        raise NotImplementedError


class NullEligibilityProvider(EligibilityProvider):
    """Default: no live استعلام. Always returns None → local computation is used."""
    code = "null"

    async def inquire(self, *, national_id: str, insurer: str,
                      ircs: list[str]) -> EligibilityResult | None:
        return None


_PROVIDERS: dict[str, type[EligibilityProvider]] = {"null": NullEligibilityProvider}


def get_eligibility_provider() -> EligibilityProvider:
    """Resolve the configured provider (PRICE_ELIGIBILITY_PROVIDER), default null."""
    code = os.getenv("PRICE_ELIGIBILITY_PROVIDER", "null").strip().lower()
    return _PROVIDERS.get(code, NullEligibilityProvider)()
