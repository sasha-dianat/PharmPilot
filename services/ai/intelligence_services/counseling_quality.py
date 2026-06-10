"""
#11 — Counseling Quality Assessment  (offline-first)
====================================================
Scores a consultation transcript against a coverage rubric, reads patient
comprehension/sentiment, and feeds both QA and the adherence engine.

LOCAL BRAIN (always available — no model download needed):
  • RUBRIC COVERAGE — deterministic keyword/phrase detection for each required
    counseling element (indication, dose, timing, side-effects, storage,
    missed-dose, when-to-call). This is the always-on floor.
  • SENTIMENT — a small lexicon scorer over the patient's speech segments.
  • COMPREHENSION — heuristics: did the patient ask questions / paraphrase back?
  • Optional Ollama pass refines the rubric judgement when available.

CLOUD BRAIN (when online):
  • Cloud LLM nuanced rubric scoring + coaching feedback prose. Offline → the
    checkmarks + scores come from local detection, no prose rationale.
"""
from __future__ import annotations

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

SERVICE = "counseling_quality"

# ─── Rubric: element → trigger phrases (deterministic detection) ──────────────

RUBRIC: dict[str, dict] = {
    "indication":  {"label": "Indication / what it's for",
                    "patterns": [r"\bfor your\b", r"\btreat", r"\bhelps? with\b", r"\bused for\b",
                                 r"\bblood pressure\b", r"\bcholesterol\b", r"\binfection\b", r"\bpain\b",
                                 r"\bwhat it'?s for\b", r"\bcondition\b"]},
    "dose":        {"label": "Dose / how much",
                    "patterns": [r"\bone tablet\b", r"\btwo tablets?\b", r"\bmilligram", r"\bmg\b",
                                 r"\bone capsule\b", r"\bhalf\b", r"\bhow much\b", r"\bdose\b"]},
    "timing":      {"label": "Timing / how often",
                    "patterns": [r"\bonce a day\b", r"\btwice a day\b", r"\bthree times\b",
                                 r"\bevery\b", r"\bmorning\b", r"\bnight\b", r"\bbedtime\b",
                                 r"\bwith food\b", r"\bbefore\b", r"\bafter meal", r"\bdaily\b"]},
    "side_effects":{"label": "Side effects",
                    "patterns": [r"\bside effect", r"\bdizz", r"\bnausea\b", r"\bdrows", r"\bupset stomach\b",
                                 r"\bmight feel\b", r"\bcould cause\b", r"\bwatch for\b"]},
    "storage":     {"label": "Storage",
                    "patterns": [r"\bstore\b", r"\brefrigerat", r"\bkeep it\b", r"\broom temperature\b",
                                 r"\baway from\b", r"\bout of reach\b"]},
    "missed_dose": {"label": "Missed dose",
                    "patterns": [r"\bmiss a dose\b", r"\bforget\b", r"\bskip\b", r"\bif you don'?t take\b",
                                 r"\bremember\b"]},
    "when_to_call":{"label": "When to call / seek help",
                    "patterns": [r"\bcall (?:us|your doctor|the pharmacy)\b", r"\bif it gets worse\b",
                                 r"\bseek help\b", r"\bemergency\b", r"\bcontact\b", r"\bany questions\b"]},
}


@dataclass
class RubricElement:
    key:       str
    label:     str
    covered:   bool
    evidence:  str


@dataclass
class CounselingScore:
    rubric:            list[RubricElement]
    coverage_pct:      float
    sentiment:         str           # positive | neutral | anxious | confused
    sentiment_score:   float         # -1..1
    comprehension:     str           # confirmed | partial | unclear
    patient_questions: int
    duration_seconds:  int
    flags:             list[str]


# ─── Sentiment lexicon (tiny, offline) ────────────────────────────────────────

_NEG = {"worried", "anxious", "scared", "afraid", "confused", "don't understand",
        "unsure", "nervous", "pain", "worse", "bad", "side effect", "problem"}
_POS = {"thank", "thanks", "great", "good", "understand", "got it", "okay", "ok",
        "clear", "helpful", "appreciate", "yes"}
_CONFUSION = {"confused", "don't understand", "what do you mean", "not sure",
              "can you repeat", "again", "lost", "huh"}


def _speaker_segments(transcript: str, diarized: Optional[list]) -> tuple[str, str]:
    """Return (pharmacist_text, patient_text). Falls back to whole transcript."""
    if diarized and isinstance(diarized, list):
        pharm, pat = [], []
        for seg in diarized:
            spk = str(seg.get("speaker", "")).lower()
            txt = seg.get("text", "")
            if "patient" in spk or spk in ("b", "1", "customer"):
                pat.append(txt)
            else:
                pharm.append(txt)
        return " ".join(pharm), " ".join(pat)
    return transcript, transcript   # no diarization → search whole text


def _detect_rubric(pharmacist_text: str) -> list[RubricElement]:
    out = []
    low = pharmacist_text.lower()
    for key, spec in RUBRIC.items():
        evidence = ""
        for pat in spec["patterns"]:
            m = re.search(pat, low)
            if m:
                start = max(0, m.start() - 20)
                evidence = pharmacist_text[start:m.end() + 20].strip()
                break
        out.append(RubricElement(key=key, label=spec["label"],
                                 covered=bool(evidence), evidence=evidence))
    return out


def _sentiment(patient_text: str) -> tuple[str, float]:
    low = patient_text.lower()
    neg = sum(low.count(w) for w in _NEG)
    pos = sum(low.count(w) for w in _POS)
    total = neg + pos
    score = ((pos - neg) / total) if total else 0.0
    if any(c in low for c in _CONFUSION):
        return "confused", min(score, -0.2)
    if score <= -0.3:
        return "anxious", score
    if score >= 0.3:
        return "positive", score
    return "neutral", score


def _comprehension(patient_text: str) -> tuple[str, int]:
    low = patient_text.lower()
    questions = low.count("?") + sum(low.count(q) for q in
                                     ["what", "how", "why", "when", "can you", "do i"])
    confirms = sum(low.count(c) for c in ["got it", "understand", "okay", "i see", "makes sense"])
    confusion = any(c in low for c in _CONFUSION)
    if confusion:
        return "unclear", questions
    if confirms >= 1:
        return "confirmed", questions
    return "partial", questions


async def _load_transcript(db: AsyncSession, transcript_id: str) -> Optional[dict]:
    try:
        res = await db.execute(text("""
            SELECT full_transcript, diarized_segments, duration_seconds, patient_id
            FROM audio_transcripts WHERE id = :id
        """), {"id": transcript_id})
        row = res.mappings().first()
        return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("[counseling_quality] transcript load failed (%s)", exc)
        return None


async def assess(
    db: AsyncSession,
    *,
    transcript_id: Optional[str] = None,
    transcript_text: Optional[str] = None,
    diarized_segments: Optional[list] = None,
    duration_seconds: int = 0,
    is_complex: bool = False,
    force_tier: Optional[Tier] = None,
) -> dict:
    """Assess counseling quality. §1.2 envelope."""
    tier = IntelligenceTier.resolve(SERVICE, force=force_tier)

    transcript = transcript_text or ""
    diarized = diarized_segments
    if not transcript and transcript_id:
        row = await _load_transcript(db, transcript_id)
        if row:
            transcript = row.get("full_transcript") or ""
            diarized = row.get("diarized_segments")
            duration_seconds = duration_seconds or int(row.get("duration_seconds") or 0)

    if not transcript.strip():
        return build_envelope(
            {"score": None, "message": "No transcript available to assess."},
            tier_used=Tier.LOCAL, confidence=0.0, degraded=True,
            options_active=[], options_offline=["rubric", "sentiment"],
            model_version="counseling_quality_v1",
        )

    pharmacist_text, patient_text = _speaker_segments(transcript, diarized)

    rubric = _detect_rubric(pharmacist_text)
    covered = sum(1 for r in rubric if r.covered)
    coverage_pct = round(covered / len(rubric) * 100, 1)
    sentiment, s_score = _sentiment(patient_text)
    comprehension, questions = _comprehension(patient_text)

    flags: list[str] = []
    if coverage_pct < 50:
        flags.append("low_rubric_coverage")
    if sentiment in ("anxious", "confused"):
        flags.append("patient_distress_followup")
    if comprehension == "unclear":
        flags.append("comprehension_followup")
    if is_complex and duration_seconds and duration_seconds < 120:
        flags.append("counseling_too_brief_for_complex_med")

    score = CounselingScore(
        rubric=rubric, coverage_pct=coverage_pct,
        sentiment=sentiment, sentiment_score=round(s_score, 2),
        comprehension=comprehension, patient_questions=questions,
        duration_seconds=duration_seconds, flags=flags,
    )

    options_active = ["rubric_detection", "sentiment_lexicon", "comprehension_heuristic"]
    options_offline: list[str] = []
    degraded = (tier == Tier.LOCAL)

    # Optional cloud coaching prose.
    coaching = ""
    if tier in (Tier.CLOUD, Tier.HYBRID):
        missing = [r.label for r in rubric if not r.covered]
        if missing:
            try:
                res = await generate(
                    f"Counseling missed these elements: {missing}. Write one short, "
                    "supportive coaching tip for the pharmacist.",
                    system="You are a pharmacy preceptor. One sentence, constructive.",
                    max_tokens=80, temperature=0.3, phi=False, task="summarize",
                )
                coaching = res.text.strip()
                if not res.degraded:
                    options_active.append("coaching_feedback")
                    tier = Tier.HYBRID
            except Exception:  # noqa: BLE001
                options_offline.append("coaching_feedback")
    else:
        options_offline.append("coaching_feedback")

    confidence = 0.7 if not degraded else 0.62
    return build_envelope(
        {"score": {
            "rubric": [r.__dict__ for r in rubric],
            "coverage_pct": coverage_pct,
            "sentiment": sentiment, "sentiment_score": score.sentiment_score,
            "comprehension": comprehension, "patient_questions": questions,
            "duration_seconds": duration_seconds, "flags": flags,
         },
         "coaching": coaching},
        tier_used=tier, confidence=confidence, degraded=degraded,
        options_active=options_active, options_offline=options_offline,
        model_version="counseling_quality_v1",
    )
