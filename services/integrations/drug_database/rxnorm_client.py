"""
RxNorm REST API Client
======================
Uses NLM's free public RxNav API — no credentials required.
Docs: https://lhncbc.nlm.nih.gov/RxNav/APIs/RxNormAPIs.html

Key capabilities:
  - NDC  → RxCUI (canonical drug concept ID)
  - Name → RxCUI (fuzzy brand/generic name lookup)
  - RxCUI → drug info (brand name, generic name, drug class, strength)
  - RxCUI → therapeutic alternatives (same active ingredient, different products)
  - Interaction check between two RxCUIs
"""
from __future__ import annotations

from typing import Optional
import httpx
import structlog

log = structlog.get_logger(__name__)

RXNAV_BASE = "https://rxnav.nlm.nih.gov/REST"
_TIMEOUT = 12.0


class RxNormClient:
    """
    Async client for the NLM RxNorm REST API.

    Usage (async context manager):
        async with RxNormClient() as client:
            rxcui = await client.ndc_to_rxcui("00093-7096-56")
    """

    def __init__(self, timeout: float = _TIMEOUT):
        self._client = httpx.AsyncClient(
            base_url=RXNAV_BASE,
            timeout=timeout,
            headers={"Accept": "application/json"},
            follow_redirects=True,
        )

    # ── NDC / Name → RxCUI ────────────────────────────────────────────────────

    async def ndc_to_rxcui(self, ndc: str) -> Optional[str]:
        """
        Resolve NDC (any format: 11-digit, 10-digit, dashed) to RxCUI.
        Returns None if the NDC is not found in RxNorm.
        """
        ndc_clean = ndc.replace("-", "").strip()
        try:
            r = await self._client.get(
                "/ndcproperties.json",
                params={"id": ndc_clean, "ndcstatus": ""},
            )
            r.raise_for_status()
            props = (
                r.json()
                .get("ndcPropertyList", {})
                .get("ndcProperty", [])
            )
            if props:
                rxcui = props[0].get("rxcui")
                log.debug("rxnorm_ndc_resolved", ndc=ndc, rxcui=rxcui)
                return rxcui
        except Exception as exc:
            log.warning("rxnorm_ndc_lookup_failed", ndc=ndc, error=str(exc))
        return None

    async def name_to_rxcui(self, name: str, search_mode: int = 2) -> Optional[str]:
        """
        Fuzzy drug name → best RxCUI match.
        search_mode=2 is "approximate" (handles misspellings, abbreviations).
        """
        try:
            r = await self._client.get(
                "/rxcui.json",
                params={"name": name, "search": search_mode},
            )
            r.raise_for_status()
            ids = r.json().get("idGroup", {}).get("rxnormId", [])
            if ids:
                log.debug("rxnorm_name_resolved", name=name, rxcui=ids[0])
                return ids[0]
        except Exception as exc:
            log.warning("rxnorm_name_lookup_failed", name=name, error=str(exc))
        return None

    # ── RxCUI → Drug Information ──────────────────────────────────────────────

    async def rxcui_to_info(self, rxcui: str) -> dict:
        """
        Return structured drug info for a given RxCUI.
        Fields: rxcui, rxnorm_name, brand_name, synonym, tty (term type).
        """
        result: dict = {"rxcui": rxcui}
        try:
            r = await self._client.get(
                f"/rxcui/{rxcui}/allProperties.json",
                params={"prop": "NAMES"},
            )
            r.raise_for_status()
            props = (
                r.json()
                .get("propConceptGroup", {})
                .get("propConcept", [])
            )
            for p in props:
                pname = p.get("propName", "")
                pval  = p.get("propValue", "")
                if pname == "RxNorm Name":
                    result["rxnorm_name"] = pval
                elif pname == "BRAND_NAME" and "brand_name" not in result:
                    result["brand_name"] = pval
                elif pname == "SYNONYM" and "synonym" not in result:
                    result["synonym"] = pval
        except Exception as exc:
            log.warning("rxnorm_rxcui_info_failed", rxcui=rxcui, error=str(exc))
        return result

    async def rxcui_drug_class(self, rxcui: str) -> list[str]:
        """
        Return pharmacological class names for a RxCUI (EPC / MOA / PE).
        Uses the RxClass API endpoint.
        """
        try:
            r = await self._client.get(
                f"/rxcui/{rxcui}/classes.json",
                params={"classTypes": "EPC"},
            )
            r.raise_for_status()
            classes = (
                r.json()
                .get("rxclassDrugInfoList", {})
                .get("rxclassDrugInfo", [])
            )
            return [c.get("rxclassMinConceptItem", {}).get("className", "") for c in classes if c]
        except Exception as exc:
            log.warning("rxnorm_class_lookup_failed", rxcui=rxcui, error=str(exc))
            return []

    # ── Therapeutic Alternatives ──────────────────────────────────────────────

    async def get_therapeutic_alternatives(self, rxcui: str, limit: int = 15) -> list[dict]:
        """
        Return drugs with the same active ingredient (SBD = branded, SCD = generic).
        Used for shortage-driven substitution suggestions.
        """
        try:
            r = await self._client.get(
                f"/rxcui/{rxcui}/related.json",
                params={"tty": "SBD+SCD"},
            )
            r.raise_for_status()
            groups = (
                r.json()
                .get("relatedGroup", {})
                .get("conceptGroup", [])
            )
            results: list[dict] = []
            for group in groups:
                for concept in group.get("conceptProperties", []):
                    results.append({
                        "rxcui":    concept.get("rxcui"),
                        "name":     concept.get("name"),
                        "tty":      concept.get("tty"),  # SBD=branded, SCD=generic
                        "suppress": concept.get("suppress"),
                    })
            return results[:limit]
        except Exception as exc:
            log.warning("rxnorm_alternatives_failed", rxcui=rxcui, error=str(exc))
            return []

    # ── Drug Interactions ─────────────────────────────────────────────────────

    async def check_interactions(self, rxcuis: list[str]) -> list[dict]:
        """
        Check for known interactions among a list of RxCUIs.
        Returns list of interaction pairs from NLM interaction data.
        """
        if len(rxcuis) < 2:
            return []
        try:
            r = await self._client.get(
                "/interaction/list.json",
                params={"rxcuis": "+".join(rxcuis)},
            )
            r.raise_for_status()
            full = r.json().get("fullInteractionTypeGroup", [])
            results: list[dict] = []
            for group in full:
                for itype in group.get("fullInteractionType", []):
                    for pair in itype.get("interactionPair", []):
                        results.append({
                            "severity":     pair.get("severity"),
                            "description":  pair.get("description"),
                            "drugs":        [
                                c.get("minConcept", {}).get("name")
                                for c in pair.get("interactionConcept", [])
                            ],
                            "source": group.get("sourceName"),
                        })
            return results
        except Exception as exc:
            log.warning("rxnorm_interactions_failed", rxcuis=rxcuis, error=str(exc))
            return []

    # ── Convenience: Full NDC Lookup ──────────────────────────────────────────

    async def full_drug_lookup(self, ndc: str) -> dict:
        """
        One-shot lookup: NDC → RxCUI → full drug info + drug class.
        """
        rxcui = await self.ndc_to_rxcui(ndc)
        if not rxcui:
            return {"ndc": ndc, "rxcui": None, "found": False}
        info   = await self.rxcui_to_info(rxcui)
        classes = await self.rxcui_drug_class(rxcui)
        return {
            "ndc":         ndc,
            "found":       True,
            "rxcui":       rxcui,
            "rxnorm_name": info.get("rxnorm_name"),
            "brand_name":  info.get("brand_name"),
            "drug_classes": classes,
        }

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    async def close(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "RxNormClient":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
