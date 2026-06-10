"""
#8 — Personalized Label Language Simplification  (offline-first)
================================================================
Two outputs from one engine:

  1. PATIENT-FACING default instructions — the SIG rewritten in plain, patient-
     appropriate language in the patient's preferred language. This becomes the
     DEFAULT administration instruction printed on the label.

  2. PHARMACIST-FACING reference dosing — authoritative textbook/reference dosing
     for the drug, retrieved from the trainable Reference Corpus (§1.5), shown so
     the pharmacist can verify the SIG against standard dosing.

LOCAL BRAIN (always available):
  • Deterministic SIG-expansion grammar (sig abbreviation → words) ALWAYS yields a
    readable instruction even with no LLM.
  • Ollama then rewrites it fluently in the target language (template-guided).
  • Reference dosing via LOCAL PubMedBERT RAG over ingested dosing tables.

CLOUD BRAIN (when online):
  • Cloud LLM for idiomatic phrasing in less-common languages, pictogram hints,
    literacy-graded wording. Offline → local rewrite + retrieved dosing chunks.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.ai.intelligence_core import (
    Tier, IntelligenceTier, build_envelope, generate, ReferenceCorpus,
)

logger = logging.getLogger(__name__)

SERVICE = "label_simplifier"
CORPUS_NAMESPACE = "label_dosing"

# ─── SIG abbreviation grammar (deterministic, always offline) ─────────────────
# Latin sig codes → plain English fragments. Order-independent token expansion.

_SIG_TOKENS: dict[str, str] = {
    # frequency
    "qd": "once daily", "qday": "once daily", "od": "once daily",
    "bid": "twice daily", "tid": "three times daily", "qid": "four times daily",
    "qhs": "at bedtime", "hs": "at bedtime", "qam": "every morning", "qpm": "every evening",
    "qod": "every other day", "q4h": "every 4 hours", "q6h": "every 6 hours",
    "q8h": "every 8 hours", "q12h": "every 12 hours", "prn": "as needed",
    "stat": "immediately", "ac": "before meals", "pc": "after meals",
    # route
    "po": "by mouth", "pr": "rectally", "sl": "under the tongue",
    "top": "to the skin", "inh": "by inhalation", "ou": "in both eyes",
    "od_eye": "in the right eye", "os": "in the left eye",
    "au": "in both ears", "ad": "in the right ear", "as": "in the left ear",
    "im": "into the muscle", "iv": "into the vein", "sq": "under the skin", "subq": "under the skin",
    # form / unit
    "tab": "tablet", "tabs": "tablets", "cap": "capsule", "caps": "capsules",
    "gtt": "drop", "gtts": "drops", "ml": "mL", "mg": "mg", "supp": "suppository",
    # qualifiers
    "ud": "as directed", "utd": "as directed", "wa": "while awake",
}

_NUM_WORD = {"1": "one", "2": "two", "3": "three", "4": "four", "5": "five", "6": "six"}


def expand_sig(sig: str) -> str:
    """Deterministically expand a coded SIG into plain English. Always works."""
    if not sig:
        return ""
    s = sig.strip()
    # "i", "ii", "iii" roman numerals for quantity → numbers.
    s = re.sub(r"\b(i{1,3})\b", lambda m: {"i": "1", "ii": "2", "iii": "3"}[m.group(1)], s, flags=re.IGNORECASE)

    tokens = re.split(r"(\s+|[,;])", s)
    out: list[str] = []
    for tok in tokens:
        key = tok.lower().strip(".")
        if key in _SIG_TOKENS:
            out.append(_SIG_TOKENS[key])
        elif key in _NUM_WORD:
            out.append(_NUM_WORD[key])
        else:
            out.append(tok)
    text_out = "".join(out)
    text_out = re.sub(r"\s+", " ", text_out).strip(" ,;")
    # Capitalise first letter, ensure it reads as an instruction.
    if text_out and not re.match(r"^(take|use|apply|inhale|place|inject|give)", text_out, re.IGNORECASE):
        text_out = "Take " + text_out
    return text_out[:1].upper() + text_out[1:] if text_out else ""


@dataclass
class ReferenceDosing:
    text:   str
    source: str
    score:  float


async def simplify(
    db: AsyncSession,
    *,
    sig: str,
    drug_name: str,
    language: str = "en",
    patient_age: Optional[int] = None,
    force_tier: Optional[Tier] = None,
) -> dict:
    """
    Produce {patient_instructions, reference_dosing[], expanded_sig}. §1.2 envelope.
    The deterministic expansion guarantees a usable instruction even with no LLM.
    """
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)
    prefer_local = (tier == Tier.LOCAL)

    # 1. Deterministic expansion (the floor — always present).
    expanded = expand_sig(sig)

    options_active  = ["sig_grammar"]
    options_offline: list[str] = []
    degraded = False

    # 2. LLM fluent rewrite (in target language).
    patient_instructions = expanded
    lang_name = _language_name(language)
    if expanded:
        system = (
            "You rewrite a medication instruction into clear, simple language a "
            f"patient can follow. Write it in {lang_name}. Keep it short, one or two "
            "sentences. Do not add medical advice beyond the instruction. "
            "Output ONLY the instruction."
        )
        prompt = f"Drug: {drug_name}\nInstruction: {expanded}"
        if patient_age and patient_age >= 75:
            prompt += "\n(Patient is elderly — use especially plain wording.)"
        try:
            res = await generate(prompt, system=system, max_tokens=120, temperature=0.2,
                                 prefer_local=prefer_local, phi=False, task="general")
            if res.text.strip():
                patient_instructions = res.text.strip()
            if res.tier.value == "cloud" and not res.degraded:
                tier = Tier.HYBRID
                options_active.append("fluent_rewrite")
            else:
                options_offline.append("fluent_rewrite")
                if res.degraded:
                    degraded = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[label_simplifier] rewrite failed (%s) → using grammar expansion", exc)
            options_offline.append("fluent_rewrite")
            degraded = True
    else:
        options_offline.append("fluent_rewrite")

    if tier == Tier.LOCAL:
        degraded = True

    # 3. Reference dosing via RAG over the trainable corpus (local-capable).
    reference_dosing: list[ReferenceDosing] = []
    try:
        corpus = ReferenceCorpus(CORPUS_NAMESPACE)
        hits = corpus.retrieve(f"{drug_name} dosing", k=3)
        for h in hits:
            reference_dosing.append(ReferenceDosing(
                text=h.chunk, source=h.metadata.get("source", h.doc_id), score=round(h.score, 3),
            ))
        options_active.append("reference_rag")
    except Exception as exc:  # noqa: BLE001
        logger.warning("[label_simplifier] reference retrieval failed (%s)", exc)
        options_offline.append("reference_rag")

    confidence = 0.85 if not degraded else 0.7
    return build_envelope(
        {
            "expanded_sig":         expanded,
            "patient_instructions": patient_instructions,
            "language":             language,
            "reference_dosing":     [r.__dict__ for r in reference_dosing],
            "has_reference":        len(reference_dosing) > 0,
        },
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="label_simplifier_v1",
    )


async def add_reference(
    *,
    drug_name: str,
    dosing_text: str,
    source: str = "pharmacist",
) -> dict:
    """
    Grow the trainable reference corpus with a dosing reference (textbook table,
    package insert, formulary entry). Embedded locally → available offline.
    """
    corpus = ReferenceCorpus(CORPUS_NAMESPACE)
    doc_id = f"{drug_name.lower().replace(' ', '_')}_{abs(hash(dosing_text)) % 100000}"
    count = corpus.ingest(doc_id, dosing_text, metadata={"drug_name": drug_name, "source": source})
    return {"ingested_chunks": count, "doc_id": doc_id, "namespace": CORPUS_NAMESPACE}


def _language_name(code: str) -> str:
    return {
        "en": "English", "fa": "Persian (Farsi)", "ar": "Arabic", "es": "Spanish",
        "fr": "French", "de": "German", "ru": "Russian", "zh": "Chinese",
        "tr": "Turkish", "ur": "Urdu", "hi": "Hindi",
    }.get((code or "en").lower(), "English")
