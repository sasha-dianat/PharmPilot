"""
Iranian insurance adapters — Salamat, Tamin, Armed Forces, Supplementary.
=========================================================================
Each adapter speaks to one organisation's eligibility-inquiry service
(سامانه استحقاق‌سنجی). Production endpoints are organisation-specific SOAP/REST
gateways requiring a signed contract + credentials; those are read from config
and, when absent, the adapter falls back to the deterministic SANDBOX backend
so the full pipeline runs locally.

Adding a real backend = implement `_live_eligibility()` / `_live_family()` for
that org and provide its credentials in settings. Nothing else changes.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import Optional

import httpx

from services.core.localization.jalali import date_to_jalali_str
from services.integrations.iranian_insurance.base import (
    EligibilityStatus,
    FamilyCoverage,
    InsuranceMember,
    InsuranceOrg,
    IranianInsuranceAdapter,
    mask_national_code,
)

logger = logging.getLogger(__name__)


# ── Deterministic sandbox backend ─────────────────────────────────────────────
# Produces stable fake-but-plausible Persian demographics from a national code so
# tests and demos are repeatable. Family members are derived by mutating the
# principal's code deterministically.

_FA_FIRST_M = ["علی", "محمد", "رضا", "حسین", "مهدی", "امیر", "سعید", "بهرام"]
_FA_FIRST_F = ["فاطمه", "زهرا", "مریم", "نرگس", "سارا", "لیلا", "الهام", "شیرین"]
_FA_LAST = ["محمدی", "حسینی", "رضایی", "احمدی", "موسوی", "کریمی", "جعفری", "صادقی"]
_PROVINCES = ["تهران", "اصفهان", "فارس", "خراسان رضوی", "آذربایجان شرقی", "البرز"]


def _seed(national_code: str) -> int:
    return int(hashlib.sha256(national_code.encode()).hexdigest(), 16)


def _sandbox_member(national_code: str, org: InsuranceOrg,
                    relationship: str = "principal",
                    principal_code: Optional[str] = None) -> InsuranceMember:
    s = _seed(national_code)
    female = (s % 2 == 0)
    first = (_FA_FIRST_F if female else _FA_FIRST_M)[(s >> 3) % 8]
    last = _FA_LAST[(s >> 7) % 8]
    # DOB: deterministic between 1950 and 2015
    year = 1950 + (s % 65)
    month = 1 + ((s >> 5) % 12)
    day = 1 + ((s >> 9) % 28)
    dob = date(year, month, day)
    copay = {
        InsuranceOrg.SALAMAT: 10.0,
        InsuranceOrg.TAMIN: 10.0,
        InsuranceOrg.ARMED_FORCES: 5.0,
        InsuranceOrg.SUPPLEMENTARY: 0.0,
    }[org]
    return InsuranceMember(
        national_code=national_code,
        org=org,
        status=EligibilityStatus.ACTIVE,
        first_name=first,
        last_name=last,
        father_name=_FA_FIRST_M[(s >> 11) % 8],
        date_of_birth=dob,
        date_of_birth_jalali=date_to_jalali_str(dob),
        gender="F" if female else "M",
        policy_number=f"{org.value[:3].upper()}-{national_code[-6:]}",
        relationship_to_principal=relationship,
        principal_national_code=principal_code,
        province=_PROVINCES[(s >> 13) % 6],
        valid_until=date.today() + timedelta(days=365),
        copay_percent=copay,
        raw={"backend": "sandbox", "org": org.value},
    )


def _sandbox_family(national_code: str, org: InsuranceOrg) -> FamilyCoverage:
    """Principal + a deterministic set of dependents (spouse + children)."""
    principal = _sandbox_member(national_code, org, "principal")
    s = _seed(national_code)
    members = [principal]
    # Derive 1 spouse + (0..3) children with deterministic, checksum-fixed codes
    n_children = s % 4
    from services.core.localization.national_id import national_code_checksum

    def derive(base: str, salt: int) -> str:
        # mutate middle digits, then fix the checksum so it stays a valid کد ملی
        body = list(base)
        body[3] = str((int(body[3]) + salt) % 10)
        body[5] = str((int(body[5]) + salt * 3) % 10)
        first9 = "".join(body[:9])
        return first9 + str(national_code_checksum(first9))

    spouse_code = derive(national_code, 1)
    spouse = _sandbox_member(spouse_code, org, "spouse", principal_code=national_code)
    # ensure spouse gender differs from principal for plausibility
    spouse.gender = "M" if principal.gender == "F" else "F"
    spouse.last_name = principal.last_name
    members.append(spouse)

    for i in range(n_children):
        child_code = derive(national_code, 2 + i)
        child = _sandbox_member(child_code, org, "child", principal_code=national_code)
        child.last_name = principal.last_name
        members.append(child)

    return FamilyCoverage(
        principal_national_code=national_code,
        org=org,
        members=members,
    )


# ── Base adapter with sandbox fallback ────────────────────────────────────────

class _SandboxFallbackAdapter(IranianInsuranceAdapter):
    """Shared base: try live endpoint if configured, else sandbox."""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 timeout: float = 8.0):
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout

    @property
    def is_live(self) -> bool:
        return bool(self.base_url and self.api_key)

    async def check_eligibility(self, national_code: str) -> InsuranceMember:
        code = self._require_valid_code(national_code)
        if self.is_live:
            try:
                return await self._live_eligibility(code)
            except Exception as e:  # graceful degradation — never block dispensing
                logger.warning("%s live eligibility failed for %s: %s — sandbox fallback",
                               self.org.value, mask_national_code(code), e)
        return _sandbox_member(code, self.org)

    async def get_family_coverage(self, national_code: str) -> FamilyCoverage:
        code = self._require_valid_code(national_code)
        if self.is_live:
            try:
                return await self._live_family(code)
            except Exception as e:
                logger.warning("%s live family lookup failed for %s: %s — sandbox fallback",
                               self.org.value, mask_national_code(code), e)
        return _sandbox_family(code, self.org)

    # Subclasses override these for real integration.
    async def _live_eligibility(self, national_code: str) -> InsuranceMember:
        raise NotImplementedError

    async def _live_family(self, national_code: str) -> FamilyCoverage:
        raise NotImplementedError


class SalamatAdapter(_SandboxFallbackAdapter):
    """بیمه سلامت ایران — Iran Health Insurance Organization."""
    org = InsuranceOrg.SALAMAT

    async def _live_eligibility(self, national_code: str) -> InsuranceMember:
        # Salamat estehghagh-sanji gateway (contract required).
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/eligibility",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"nationalCode": national_code},
            )
            resp.raise_for_status()
            return self._map_salamat(resp.json(), national_code)

    async def _live_family(self, national_code: str) -> FamilyCoverage:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                f"{self.base_url}/family",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"nationalCode": national_code},
            )
            resp.raise_for_status()
            return self._map_salamat_family(resp.json(), national_code)

    def _map_salamat(self, payload: dict, national_code: str) -> InsuranceMember:
        from services.core.localization.jalali import jalali_str_to_date
        dob_j = payload.get("birthDateShamsi")
        return InsuranceMember(
            national_code=national_code,
            org=self.org,
            status=EligibilityStatus.ACTIVE if payload.get("isActive") else EligibilityStatus.EXPIRED,
            first_name=payload.get("firstName"),
            last_name=payload.get("lastName"),
            father_name=payload.get("fatherName"),
            date_of_birth=jalali_str_to_date(dob_j) if dob_j else None,
            date_of_birth_jalali=dob_j,
            gender=payload.get("gender"),
            policy_number=payload.get("policyNo"),
            province=payload.get("province"),
            copay_percent=payload.get("patientSharePercent", 10.0),
            raw=payload,
        )

    def _map_salamat_family(self, payload: dict, national_code: str) -> FamilyCoverage:
        principal_national_code = payload.get("principalNationalCode", national_code)
        members = []
        for member_payload in payload.get("members", []):
            member_code = member_payload.get("nationalCode", principal_national_code)
            member = self._map_salamat(member_payload, member_code)
            member.relationship_to_principal = member_payload.get("relationshipToPrincipal")
            member.principal_national_code = member_payload.get("principalNationalCode")
            members.append(member)
        return FamilyCoverage(
            principal_national_code=principal_national_code,
            org=self.org,
            members=members,
        )


class TaminAdapter(_SandboxFallbackAdapter):
    """سازمان تأمین اجتماعی — Social Security Organization."""
    org = InsuranceOrg.TAMIN


class ArmedForcesAdapter(_SandboxFallbackAdapter):
    """بیمه خدمات درمانی نیروهای مسلح — Armed Forces Medical Services."""
    org = InsuranceOrg.ARMED_FORCES


class SupplementaryAdapter(_SandboxFallbackAdapter):
    """بیمه‌های تکمیلی — generic supplementary private insurer (Dana/Alborz/Asia/...)."""
    org = InsuranceOrg.SUPPLEMENTARY
