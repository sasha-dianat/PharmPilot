"""
OpenFDA REST API Client
========================
Uses FDA's free public API — optional API key for higher rate limits.
  Without key: 240 req/min   |   With key: 1,000 req/min
Docs: https://open.fda.gov/apis/

Endpoints used:
  /drug/label.json       — structured prescribing information (PI / package insert)
  /drug/enforcement.json — drug recall/enforcement actions (Class I/II/III)
  /drug/event.json       — FAERS adverse event reports
"""
from __future__ import annotations

from typing import Optional
import httpx
import structlog

log = structlog.get_logger(__name__)

OPENFDA_BASE = "https://api.fda.gov"
_TIMEOUT = 15.0


class OpenFDAClient:
    """
    Async client for the OpenFDA Drug API.

    Usage:
        async with OpenFDAClient(api_key=settings.OPENFDA_API_KEY) as fda:
            label = await fda.get_label_by_ndc("00093-7096-56")
            recalls = await fda.get_active_recalls(limit=25)
    """

    def __init__(self, api_key: Optional[str] = None, timeout: float = _TIMEOUT):
        self._default_params: dict = {}
        if api_key:
            self._default_params["api_key"] = api_key
        self._client = httpx.AsyncClient(
            base_url=OPENFDA_BASE,
            timeout=timeout,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    async def _get(self, path: str, **params) -> dict:
        merged = {**self._default_params, **params}
        try:
            r = await self._client.get(path, params=merged)
            if r.status_code == 404:
                return {}
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError as exc:
            log.warning(
                "openfda_http_error",
                path=path,
                status=exc.response.status_code,
                error=str(exc),
            )
            return {}
        except Exception as exc:
            log.warning("openfda_request_failed", path=path, error=str(exc))
            return {}

    # ── Drug Labels ────────────────────────────────────────────────────────────

    async def get_label_by_ndc(self, ndc: str) -> Optional[dict]:
        """
        Fetch FDA-approved prescribing information (package insert) by NDC.
        Returns a structured dict with indications, warnings, dosage, interactions, etc.
        Returns None if NDC not found in OpenFDA label database.
        """
        ndc_clean = ndc.replace("-", "").strip()
        data = await self._get(
            "/drug/label.json",
            search=f'openfda.product_ndc:"{ndc_clean}"',
            limit=1,
        )
        results = data.get("results", [])
        if not results:
            return None
        return self._parse_label(results[0])

    async def get_label_by_name(self, drug_name: str) -> Optional[dict]:
        """Fetch label by generic or brand name (fallback when NDC not found)."""
        data = await self._get(
            "/drug/label.json",
            search=f'openfda.generic_name:"{drug_name}" openfda.brand_name:"{drug_name}"',
            limit=1,
        )
        results = data.get("results", [])
        if not results:
            return None
        return self._parse_label(results[0])

    def _parse_label(self, raw: dict) -> dict:
        openfda = raw.get("openfda", {})

        def first(key: str) -> Optional[str]:
            val = openfda.get(key, [])
            return val[0] if val else None

        def section(key: str, max_chars: int = 2000) -> str:
            val = raw.get(key, [""])
            return (val[0] if val else "")[:max_chars]

        return {
            # Identity
            "brand_name":       first("brand_name"),
            "generic_name":     first("generic_name"),
            "manufacturer":     first("manufacturer_name"),
            "route":            openfda.get("route", []),
            "dosage_form":      openfda.get("dosage_form", []),
            "substance_name":   openfda.get("substance_name", []),
            "pharm_class_epc":  openfda.get("pharm_class_epc", []),
            "product_ndc":      openfda.get("product_ndc", []),
            "application_number": openfda.get("application_number", []),
            # Clinical sections (truncated for safety — full text can be very long)
            "indications_and_usage":       section("indications_and_usage"),
            "contraindications":           section("contraindications"),
            "warnings":                    section("warnings"),
            "warnings_and_cautions":       section("warnings_and_cautions"),
            "adverse_reactions":           section("adverse_reactions"),
            "drug_interactions":           section("drug_interactions"),
            "dosage_and_administration":   section("dosage_and_administration"),
            "overdosage":                  section("overdosage", 1000),
            "how_supplied":                section("how_supplied", 1000),
            "storage_and_handling":        section("storage_and_handling", 500),
            "use_in_specific_populations": section("use_in_specific_populations"),
            "pregnancy":                   section("pregnancy", 1000),
            "nursing_mothers":             section("nursing_mothers", 1000),
            "pediatric_use":               section("pediatric_use", 1000),
        }

    # ── Recalls & Enforcement ─────────────────────────────────────────────────

    async def get_active_recalls(self, limit: int = 50) -> list[dict]:
        """
        Fetch current active (Ongoing) drug recall/enforcement actions.
        Sorted by most recent report date.
        Class I = most severe (may cause serious health problems or death).
        Class II = may cause temporary / reversible adverse health consequences.
        Class III = unlikely to cause adverse health consequences.
        """
        data = await self._get(
            "/drug/enforcement.json",
            search='status:"Ongoing"',
            limit=limit,
            sort="report_date:desc",
        )
        return [self._parse_recall(r) for r in data.get("results", [])]

    async def get_recalls_by_ndc(self, ndc: str) -> list[dict]:
        """Check if a specific NDC or product family has any active recall."""
        ndc_clean = ndc.replace("-", "").strip()
        # OpenFDA product_ndc uses formatted NDC; try both
        data = await self._get(
            "/drug/enforcement.json",
            search=f'product_ndc:"{ndc_clean}" AND status:"Ongoing"',
            limit=10,
        )
        results = data.get("results", [])
        if not results:
            # Try broader search on product description
            ndc_prefix = ndc_clean[:7]
            data2 = await self._get(
                "/drug/enforcement.json",
                search=f'openfda.package_ndc:"{ndc_prefix}*" AND status:"Ongoing"',
                limit=5,
            )
            results = data2.get("results", [])
        return [self._parse_recall(r) for r in results]

    async def get_recalls_by_firm(self, firm_name: str, limit: int = 20) -> list[dict]:
        """Get all active recalls from a specific manufacturer/firm."""
        data = await self._get(
            "/drug/enforcement.json",
            search=f'recalling_firm:"{firm_name}" AND status:"Ongoing"',
            limit=limit,
            sort="report_date:desc",
        )
        return [self._parse_recall(r) for r in data.get("results", [])]

    def _parse_recall(self, raw: dict) -> dict:
        return {
            "recall_number":          raw.get("recall_number"),
            "recall_class":           raw.get("classification"),       # "Class I" / "Class II" / "Class III"
            "severity_level":         self._recall_severity(raw.get("classification", "")),
            "status":                 raw.get("status"),
            "voluntary_mandated":     raw.get("voluntary_mandated"),
            "reason":                 raw.get("reason_for_recall"),
            "product_description":    raw.get("product_description"),
            "product_quantity":       raw.get("product_quantity"),
            "lot_numbers":            raw.get("code_info"),
            "distribution_pattern":   raw.get("distribution_pattern"),
            "recalling_firm":         raw.get("recalling_firm"),
            "report_date":            raw.get("report_date"),          # YYYYMMDD
            "recall_initiation_date": raw.get("recall_initiation_date"),
            "termination_date":       raw.get("termination_date"),
            "more_code_info":         raw.get("more_code_info"),
        }

    @staticmethod
    def _recall_severity(classification: str) -> str:
        cls = classification.lower()
        if "class i" in cls and "ii" not in cls:
            return "critical"
        if "class ii" in cls and "iii" not in cls:
            return "high"
        if "class iii" in cls:
            return "low"
        return "unknown"

    # ── Adverse Events (FAERS) ────────────────────────────────────────────────

    async def get_adverse_events(
        self,
        drug_name: str,
        limit: int = 20,
        serious_only: bool = False,
    ) -> list[dict]:
        """
        Fetch recent FAERS adverse event reports for a drug.
        serious_only=True filters to hospitalizations/death/life-threatening.
        """
        q = f'patient.drug.medicinalproduct:"{drug_name}"'
        if serious_only:
            q += " AND serious:1"
        data = await self._get(
            "/drug/event.json",
            search=q,
            limit=limit,
            sort="receivedate:desc",
        )
        results = data.get("results", [])
        events = []
        for r in results:
            patient = r.get("patient", {})
            reactions = [
                rx.get("reactionmeddrapt", "Unknown")
                for rx in patient.get("reaction", [])
            ]
            # Outcome classification
            outcome = "other"
            if patient.get("patientdeath") == "1":
                outcome = "death"
            elif patient.get("patienthospitalization") == "1":
                outcome = "hospitalization"
            elif patient.get("patientlifethreatening") == "1":
                outcome = "life_threatening"
            elif patient.get("patientdisability") == "1":
                outcome = "disability"

            events.append({
                "receive_date":  r.get("receivedate"),
                "serious":       r.get("serious") == "1",
                "outcome":       outcome,
                "reactions":     reactions[:10],
                "reporter_type": r.get("primarysource", {}).get("qualification"),
                "country":       r.get("primarysource", {}).get("reportercountry"),
            })
        return events

    async def count_top_reactions(self, drug_name: str, limit: int = 15) -> list[dict]:
        """
        Return the top MedDRA reactions reported to FAERS for a drug,
        sorted by count descending.
        """
        data = await self._get(
            "/drug/event.json",
            search=f'patient.drug.medicinalproduct:"{drug_name}"',
            count="patient.reaction.reactionmeddrapt.exact",
            limit=limit,
        )
        return data.get("results", [])   # [{term, count}, ...]

    async def count_adverse_events_by_outcome(self, drug_name: str) -> dict:
        """Summary counts by outcome type for a drug."""
        total_data = await self._get(
            "/drug/event.json",
            search=f'patient.drug.medicinalproduct:"{drug_name}"',
            count="serious",
        )
        total_results = total_data.get("results", [])
        total = sum(r.get("count", 0) for r in total_results)

        death_data = await self._get(
            "/drug/event.json",
            search=f'patient.drug.medicinalproduct:"{drug_name}" AND patient.patientdeath:1',
            count="patient.patientdeath",
        )
        deaths = sum(r.get("count", 0) for r in death_data.get("results", []))

        return {
            "drug_name":    drug_name,
            "total_reports": total,
            "deaths":        deaths,
            "serious":       sum(
                r.get("count", 0) for r in total_results if r.get("term") == "1"
            ),
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "OpenFDAClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
