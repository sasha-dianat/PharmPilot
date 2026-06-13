"""
#18 — Automated Clinical Documentation Generation  (offline-first)
==================================================================
Auto-drafts structured SOAP and MTM (CMR/TMR/MAP) notes from a consultation
transcript + patient context. The pharmacist reviews and signs — AI drafts only.

LOCAL BRAIN (always available — Ollama structured extraction):
  • Pulls the diarized transcript (by id) or accepts raw text.
  • Local LLM extracts SOAP fields (Subjective / Objective / Assessment / Plan).
  • MTM templates are deterministically pre-filled from structured data even if
    the LLM is unavailable, so a draft always exists.

CLOUD BRAIN (when online — higher fidelity):
  • Cloud LLM produces a richer, guideline-aware Assessment/Plan narrative.
  Offline → local draft, clearly tagged "local draft — review carefully".

PHI: the transcript may contain patient speech. When OFFLINE the local Ollama
model never leaves the building. When ONLINE the provider_registry routes PHI to
BAA providers only. Either way, the pharmacist is the signer.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, generate,
)

logger = logging.getLogger(__name__)

SERVICE = "clinical_docs"


@dataclass
class SOAPNote:
    subjective: str = ""
    objective:  str = ""
    assessment: str = ""
    plan:       str = ""


async def _load_transcript(db: AsyncSession, transcript_id: str) -> Optional[dict]:
    try:
        res = await db.execute(text("""
            SELECT id, full_transcript, diarized_segments, duration_seconds,
                   extracted_medications, extracted_concerns, patient_id
            FROM   audio_transcripts WHERE id = :id
        """), {"id": transcript_id})
        row = res.mappings().first()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clinical_docs] transcript load failed (%s)", exc)
        return None


def _parse_soap(textout: str) -> SOAPNote:
    """Parse the LLM output into SOAP sections, tolerant of formatting."""
    note = SOAPNote()
    # Match 'Subjective:' style headers.
    patterns = {
        "subjective": r"(?:^|\n)\s*s(?:ubjective)?\s*[:\-]\s*(.+?)(?=\n\s*[soap][a-z]*\s*[:\-]|\Z)",
        "objective":  r"(?:^|\n)\s*o(?:bjective)?\s*[:\-]\s*(.+?)(?=\n\s*[soap][a-z]*\s*[:\-]|\Z)",
        "assessment": r"(?:^|\n)\s*a(?:ssessment)?\s*[:\-]\s*(.+?)(?=\n\s*[soap][a-z]*\s*[:\-]|\Z)",
        "plan":       r"(?:^|\n)\s*p(?:lan)?\s*[:\-]\s*(.+?)(?=\n\s*[soap][a-z]*\s*[:\-]|\Z)",
    }
    for field_name, pat in patterns.items():
        m = re.search(pat, textout, re.IGNORECASE | re.DOTALL)
        if m:
            setattr(note, field_name, m.group(1).strip())
    # Fallback: if nothing parsed, dump everything into subjective.
    if not any([note.subjective, note.objective, note.assessment, note.plan]):
        note.subjective = textout.strip()
    return note


async def draft_soap(
    db: AsyncSession,
    *,
    transcript_id: Optional[str] = None,
    transcript_text: Optional[str] = None,
    patient_context: Optional[str] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Draft a SOAP note. §1.2 envelope with {soap, is_draft}."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    prefer_local = (tier == Tier.LOCAL)

    # Resolve transcript text.
    transcript = transcript_text or ""
    if not transcript and transcript_id:
        row = await _load_transcript(db, transcript_id)
        if row:
            transcript = row.get("full_transcript") or ""
            meds = row.get("extracted_medications")
            if meds and not patient_context:
                patient_context = f"Medications mentioned: {meds}"

    if not transcript.strip():
        return build_envelope(
            {"soap": SOAPNote().__dict__, "is_draft": True,
             "message": "No transcript text available to draft from."},
            tier_used=Tier.LOCAL, confidence=0.0, degraded=True,
            options_active=[], options_offline=["soap_extraction"],
            model_version="clinical_docs_v1",
        )

    system = (
        "You are a clinical pharmacist's documentation assistant. From the "
        "consultation transcript, draft a SOAP note. Output EXACTLY four sections "
        "labelled 'Subjective:', 'Objective:', 'Assessment:', 'Plan:'. Be concise "
        "and factual. Do not invent clinical findings not present in the transcript."
    )
    prompt = f"Transcript:\n{transcript[:4000]}\n"
    if patient_context:
        prompt += f"\nPatient context:\n{patient_context[:800]}\n"
    prompt += "\nDraft the SOAP note now."

    degraded = (tier == Tier.LOCAL)
    options_active = ["soap_extraction"]
    options_offline: list[str] = []

    try:
        res = await generate(prompt, system=system, max_tokens=600, temperature=0.1,
                             prefer_local=prefer_local, phi=True, task="clinical")
        note = _parse_soap(res.text)
        if res.degraded:
            degraded = True
        if res.tier.value == "cloud" and not res.degraded:
            tier = Tier.HYBRID
            options_active.append("guideline_narrative")
        else:
            options_offline = ["guideline_narrative"]
    except Exception as exc:  # noqa: BLE001
        logger.warning("[clinical_docs] SOAP generation failed (%s)", exc)
        note = SOAPNote(subjective="[Automatic draft unavailable — please document manually.]")
        degraded = True
        options_active = []
        options_offline = ["soap_extraction", "guideline_narrative"]

    confidence = 0.72 if not degraded else 0.5
    return build_envelope(
        {"soap": note.__dict__, "is_draft": True,
         "draft_label": "AI draft — pharmacist must review and sign"},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="clinical_docs_v1",
    )


# ─── MTM templates (deterministic, always available) ──────────────────────────

MTM_TYPES = {
    "CMR": "Comprehensive Medication Review",
    "TMR": "Targeted Medication Review",
    "MAP": "Medication Action Plan",
}


async def draft_mtm(
    db: AsyncSession,
    *,
    mtm_type: str = "CMR",
    transcript_id: Optional[str] = None,
    transcript_text: Optional[str] = None,
    medication_list: Optional[list] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Draft an MTM document. The template structure is deterministic (always works
    offline); the local/cloud LLM fills the narrative fields when available.
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    prefer_local = (tier == Tier.LOCAL)
    mtm_type = mtm_type.upper()
    if mtm_type not in MTM_TYPES:
        mtm_type = "CMR"

    transcript = transcript_text or ""
    if not transcript and transcript_id:
        row = await _load_transcript(db, transcript_id)
        if row:
            transcript = row.get("full_transcript") or ""
            if not medication_list:
                medication_list = row.get("extracted_medications") or []

    # Deterministic skeleton — present even with no LLM.
    document = {
        "mtm_type":      mtm_type,
        "mtm_type_name": MTM_TYPES[mtm_type],
        "medication_list": medication_list or [],
        "summary":         "",
        "interventions":   [],
        "action_plan":     [],
        "follow_up":       "Recommend follow-up review in 90 days.",
    }

    degraded = (tier == Tier.LOCAL)
    options_active = ["mtm_template"]
    options_offline: list[str] = []

    if transcript.strip():
        system = (
            f"You are drafting a {MTM_TYPES[mtm_type]} for a pharmacist to review. "
            "From the transcript, write a 2-3 sentence summary and list concrete "
            "interventions and action-plan items. Return JSON with keys "
            "'summary' (string), 'interventions' (string array), 'action_plan' (string array)."
        )
        prompt = f"Transcript:\n{transcript[:3500]}\n\nMedications: {medication_list or 'n/a'}"
        try:
            res = await generate(prompt, system=system, max_tokens=500, temperature=0.1,
                                 prefer_local=prefer_local, phi=True, task="clinical")
            parsed = _safe_json(res.text)
            if parsed:
                document["summary"] = parsed.get("summary", "")
                document["interventions"] = parsed.get("interventions", []) or []
                document["action_plan"] = parsed.get("action_plan", []) or []
            if res.degraded:
                degraded = True
            if res.tier.value == "cloud" and not res.degraded:
                tier = Tier.HYBRID
                options_active.append("llm_narrative")
            else:
                options_offline = ["llm_narrative"]
        except Exception as exc:  # noqa: BLE001
            logger.warning("[clinical_docs] MTM narrative failed (%s)", exc)
            degraded = True
            options_offline = ["llm_narrative"]
    else:
        options_offline = ["llm_narrative"]

    confidence = 0.7 if not degraded else 0.5
    return build_envelope(
        {"document": document, "is_draft": True,
         "draft_label": "AI draft — pharmacist must review and sign"},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="clinical_docs_mtm_v1",
    )


def _safe_json(textout: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response."""
    m = re.search(r"\{.*\}", textout, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:  # noqa: BLE001
        return None
