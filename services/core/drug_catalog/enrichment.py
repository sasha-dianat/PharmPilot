"""هوش‌یار دارو — the canonical drug-enrichment reference.

Web research (Mistral/Claude) produces SUGGESTED rows; only owner-APPROVED rows
are ever loaded into the linker/ingest paths, keeping ingestion deterministic.
Approved rows also export to data/reference/drug_enrichments.json — the
version-controlled canonical artifact that re-seeds any environment.
"""
from __future__ import annotations

import json
from pathlib import Path

from services.ai.clinical_decision_support.normalizer import normalize
from .schema import canonical_ingredient

REFERENCE_PATH = Path("data/reference/drug_enrichments.json")

SUGGESTION_FIELDS = ("generic", "brand", "manufacturer", "country",
                     "dosage_form", "strengths", "confidence", "sources", "notes")


def enrich_key(name) -> str:
    """Spelling-proof identity for a drug name: Arabic yeh/kaf → Persian,
    ZWNJ/dashes → space, whitespace folded, salt-stripped via the clinical
    normalizer, lay-name canonicalized. Same drug ⇒ same key, forever."""
    if not name:
        return ""
    s = str(name).replace("ي", "ی").replace("ك", "ک").replace("‌", " ")
    s = s.replace("-", " ").replace("–", " ")
    s = " ".join(s.split()).strip().lower()
    n = normalize(s) or s
    return canonical_ingredient(n) or n


def validate_suggestion(d: dict) -> tuple[dict, list[str]]:
    """Keep only known fields with sane shapes. → (clean, errors)."""
    d = d or {}
    errors: list[str] = []
    clean: dict = {}
    for f in SUGGESTION_FIELDS:
        v = d.get(f)
        if v in (None, "", [], {}):
            continue
        if f == "confidence":
            try:
                v = float(v)
            except (TypeError, ValueError):
                errors.append("confidence must be a number")
                continue
            if not 0.0 <= v <= 1.0:
                errors.append("confidence out of [0,1]")
                continue
        elif f in ("strengths", "sources"):
            if not isinstance(v, list):
                errors.append(f"{f} must be a list")
                continue
            v = [str(x).strip() for x in v if str(x).strip()]
        else:
            v = str(v).strip()
        clean[f] = v
    return clean, errors
