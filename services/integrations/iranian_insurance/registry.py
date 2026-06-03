"""
Iranian insurance registry + cross-insurer aggregator.
======================================================
Single entry point the rest of the platform uses. It:

  1. Holds one adapter per configured organisation.
  2. Resolves a person's coverage across ALL insurers (a person may have
     تأمین اجتماعی as primary + a تکمیلی supplementary plan).
  3. Aggregates family coverage from every insurer into one de-duplicated set
     of (national_code → person) — the raw material the identity layer uses to
     auto-link 2+ persons without any manual entry.

The default identity system is Iranian (national code primary). The American
PBM path remains available via the existing NCPDP adjudication engine and is
selected by configuration, not hard-coded.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from services.integrations.iranian_insurance.adapters import (
    ArmedForcesAdapter,
    SalamatAdapter,
    SupplementaryAdapter,
    TaminAdapter,
)
from services.integrations.iranian_insurance.base import (
    EligibilityStatus,
    FamilyCoverage,
    InsuranceMember,
    InsuranceOrg,
    IranianInsuranceAdapter,
)

logger = logging.getLogger(__name__)

_ADAPTER_CLASSES = {
    InsuranceOrg.SALAMAT: SalamatAdapter,
    InsuranceOrg.TAMIN: TaminAdapter,
    InsuranceOrg.ARMED_FORCES: ArmedForcesAdapter,
    InsuranceOrg.SUPPLEMENTARY: SupplementaryAdapter,
}


@dataclass
class AggregatedCoverage:
    """A person's full coverage picture + everyone linked to them by insurance."""
    national_code: str
    primary: Optional[InsuranceMember] = None
    all_memberships: list[InsuranceMember] = field(default_factory=list)
    # Every distinct person found under any of this person's family policies.
    linked_persons: list[InsuranceMember] = field(default_factory=list)

    @property
    def linked_national_codes(self) -> list[str]:
        seen, out = set(), []
        for m in self.linked_persons:
            if m.national_code not in seen and m.national_code != self.national_code:
                seen.add(m.national_code)
                out.append(m.national_code)
        return out


class IranianInsuranceRegistry:
    def __init__(self, adapters: Optional[dict[InsuranceOrg, IranianInsuranceAdapter]] = None):
        self.adapters: dict[InsuranceOrg, IranianInsuranceAdapter] = adapters or {}

    @classmethod
    def from_settings(cls, settings) -> "IranianInsuranceRegistry":
        """Build registry from app settings; sandbox where no credentials present."""
        enabled = getattr(settings, "IRANIAN_INSURERS_ENABLED", None) or [
            o.value for o in InsuranceOrg
        ]
        adapters: dict[InsuranceOrg, IranianInsuranceAdapter] = {}
        for org in InsuranceOrg:
            if org.value not in enabled:
                continue
            cls_ = _ADAPTER_CLASSES[org]
            base_url = getattr(settings, f"IRAN_{org.value.upper()}_URL", "") or None
            api_key = getattr(settings, f"IRAN_{org.value.upper()}_API_KEY", "") or None
            adapters[org] = cls_(base_url=base_url, api_key=api_key)
        return cls(adapters)

    async def check_all(self, national_code: str) -> list[InsuranceMember]:
        """Eligibility across every configured insurer (concurrent)."""
        results = await asyncio.gather(
            *[a.check_eligibility(national_code) for a in self.adapters.values()],
            return_exceptions=True,
        )
        members: list[InsuranceMember] = []
        for r in results:
            if isinstance(r, InsuranceMember) and r.status != EligibilityStatus.NOT_FOUND:
                members.append(r)
        return members

    async def aggregate_coverage(self, national_code: str) -> AggregatedCoverage:
        """
        Full coverage + insurance-derived person links for one national code.
        Runs eligibility + family lookup across all insurers concurrently.
        """
        elig_task = self.check_all(national_code)
        fam_tasks = [a.get_family_coverage(national_code) for a in self.adapters.values()]
        memberships, *families = await asyncio.gather(elig_task, *fam_tasks,
                                                       return_exceptions=True)

        memberships = memberships if isinstance(memberships, list) else []
        agg = AggregatedCoverage(national_code=national_code, all_memberships=memberships)

        # Primary = prefer Salamat, then Tamin, then armed forces, then first available
        priority = [InsuranceOrg.SALAMAT, InsuranceOrg.TAMIN,
                    InsuranceOrg.ARMED_FORCES, InsuranceOrg.SUPPLEMENTARY]
        for org in priority:
            match = next((m for m in memberships if m.org == org), None)
            if match:
                agg.primary = match
                break
        if agg.primary is None and memberships:
            agg.primary = memberships[0]

        # Collect every linked person from every insurer's family coverage.
        seen: set[str] = set()
        for fam in families:
            if not isinstance(fam, FamilyCoverage):
                continue
            for member in fam.members:
                if member.national_code not in seen:
                    seen.add(member.national_code)
                    agg.linked_persons.append(member)

        logger.info(
            "Aggregated coverage: %d memberships, %d linked persons",
            len(memberships), len(agg.linked_persons),
        )
        return agg
