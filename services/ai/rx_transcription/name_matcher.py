"""
Patient Name Matcher & Relationship Tree Enricher
==================================================
Takes the patient name extracted from a scanned Rx and:

  1. Normalises both names (lowercase, strip titles, accents → ASCII approximation)
  2. Computes similarity using difflib SequenceMatcher (always available) +
     Jaro-Winkler via jellyfish (if installed)
  3. Classifies the match:
       SAME       ≥ 0.88  — same person, possible spelling variant
       LIKELY     0.70–0.87 — same person, strong candidate
       POSSIBLE   0.50–0.69 — different person, may be related
       DIFFERENT  < 0.50  — clearly different person

  4. If the person picking up (visitor_patient_id) is DIFFERENT from the name
     on the Rx (rx_patient_id), creates an undirected link in `person_links`
     with source="rx_scan" so the relationship tree is enriched for future visits.

No relationship *type* is stored (per original design: "it does not matter
to find the relationship — just link the two people").
Staff can optionally add a label (parent/child/spouse/caregiver) at the UI.
"""
from __future__ import annotations

import re
import unicodedata
import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Optional, List
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.biometric.identity_resolution.person_links import (
    PersonLinkGraph,
    patient_ref,
)

logger = logging.getLogger(__name__)


# ─── Name normalisation ───────────────────────────────────────────────────────

_HONORIFICS = re.compile(
    r"^(?:mr\.?|mrs\.?|ms\.?|miss|dr\.?|prof\.?|eng\.?|"
    r"آقای|خانم|دکتر|مهندس|استاد)\s+",
    re.IGNORECASE,
)

_PUNCTUATION = re.compile(r"[^\w\s]")


def _normalise(name: str) -> str:
    """
    Lower-case, strip titles, collapse whitespace, transliterate accents.
    Works for both Latin and Persian/Arabic text.
    """
    name = name.strip()
    # Remove diacritics (ā → a, é → e, etc.)
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    # Strip honorifics
    name = _HONORIFICS.sub("", name)
    # Remove punctuation
    name = _PUNCTUATION.sub(" ", name)
    # Collapse whitespace, lower
    return " ".join(name.lower().split())


def _jaro_winkler(a: str, b: str) -> float:
    """Jaro-Winkler similarity via jellyfish if available, else 0."""
    try:
        import jellyfish
        return jellyfish.jaro_winkler_similarity(a, b)
    except ImportError:
        return 0.0


def fuzzy_name_match(name_a: str, name_b: str) -> float:
    """
    Returns similarity score 0–1 between two person names.
    Uses the best of SequenceMatcher and Jaro-Winkler.
    """
    if not name_a or not name_b:
        return 0.0

    na = _normalise(name_a)
    nb = _normalise(name_b)

    if na == nb:
        return 1.0

    seq  = SequenceMatcher(None, na, nb).ratio()
    jw   = _jaro_winkler(na, nb)

    return max(seq, jw)


# ─── Classification ───────────────────────────────────────────────────────────

SAME_THRESHOLD      = 0.88
LIKELY_THRESHOLD    = 0.70
POSSIBLE_THRESHOLD  = 0.50


def classify(score: float) -> str:
    if score >= SAME_THRESHOLD:     return "same"
    if score >= LIKELY_THRESHOLD:   return "likely"
    if score >= POSSIBLE_THRESHOLD: return "possible"
    return "different"


# ─── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class PatientMatch:
    patient_id:   str
    full_name:    str
    national_id:  Optional[str]
    similarity:   float
    classification: str           # same | likely | possible | different


@dataclass
class NameMatchResult:
    extracted_name:    str
    visitor_name:      Optional[str]
    similarity:        float
    classification:    str
    db_candidates:     List[PatientMatch]
    link_created:      bool
    link_relationship: Optional[str]


# ─── Database patient search ──────────────────────────────────────────────────

async def _search_patients_by_name(
    name: str,
    pharmacy_id: str,
    db: AsyncSession,
    limit: int = 10,
) -> List[PatientMatch]:
    """
    Pull all patients for this pharmacy and score them client-side with
    fuzzy matching (avoids requiring pg_trgm extension).
    For large pharmacies (>50k patients) this could be paginated.
    """
    result = await db.execute(text("""
        SELECT id, first_name, last_name, national_id
        FROM   patients
        WHERE  pharmacy_id = :pharmacy_id
          AND  status NOT IN ('deceased', 'transferred')
        LIMIT  2000
    """), {"pharmacy_id": pharmacy_id})

    candidates: List[PatientMatch] = []
    for row in result.mappings():
        full_name = f"{row['first_name']} {row['last_name']}"
        score     = fuzzy_name_match(name, full_name)
        if score >= POSSIBLE_THRESHOLD:
            candidates.append(PatientMatch(
                patient_id=str(row["id"]),
                full_name=full_name,
                national_id=row.get("national_id"),
                similarity=round(score, 3),
                classification=classify(score),
            ))

    candidates.sort(key=lambda c: -c.similarity)
    return candidates[:limit]


# ─── Main matcher class ───────────────────────────────────────────────────────

class PatientNameMatcher:
    """
    Compare the name extracted from an Rx against:
      (a) the visitor (person who brought the Rx) — for direct comparison
      (b) the full patient DB — for candidate discovery

    If the visitor is a DIFFERENT person from the patient named on the Rx,
    an undirected link is created in person_links so future visits auto-fan-out.
    """

    def __init__(self, db: AsyncSession):
        self.db    = db
        self.graph = PersonLinkGraph(db)

    async def match_and_enrich(
        self,
        extracted_rx_patient_name: str,
        pharmacy_id:               str,
        *,
        visitor_patient_id:        Optional[str]  = None,
        rx_patient_id:             Optional[str]  = None,   # if already known from DB
        relationship_hint:         Optional[str]  = None,   # staff-supplied label (optional)
    ) -> NameMatchResult:
        """
        Core method.

        Parameters
        ----------
        extracted_rx_patient_name
            The name OCR-extracted from the physical prescription.
        pharmacy_id
            Current pharmacy — scopes DB search.
        visitor_patient_id
            The patient ID of the person who physically brought the Rx.
            If None, no link is created (no visitor identified yet).
        rx_patient_id
            If the Rx is already linked to a patient record, pass it here.
            Otherwise the system will search for the best match.
        relationship_hint
            Optional label the staff chose ("parent", "spouse", "caregiver", …).
        """
        if not extracted_rx_patient_name.strip():
            return NameMatchResult(
                extracted_name="", visitor_name=None,
                similarity=0.0, classification="different",
                db_candidates=[], link_created=False, link_relationship=None,
            )

        # ── Get visitor name ──
        visitor_name = None
        if visitor_patient_id:
            row = await self.db.execute(text("""
                SELECT first_name, last_name FROM patients WHERE id = :id
            """), {"id": visitor_patient_id})
            r = row.mappings().first()
            if r:
                visitor_name = f"{r['first_name']} {r['last_name']}"

        # ── Score vs visitor ──
        visitor_similarity = (
            fuzzy_name_match(extracted_rx_patient_name, visitor_name)
            if visitor_name else 0.0
        )
        visitor_classification = classify(visitor_similarity) if visitor_name else "unknown"

        # ── DB candidate search ──
        db_candidates = await _search_patients_by_name(
            extracted_rx_patient_name, pharmacy_id, self.db
        )

        # Determine effective rx_patient_id from best DB match
        effective_rx_patient_id = rx_patient_id
        if not effective_rx_patient_id and db_candidates:
            top = db_candidates[0]
            if top.classification in ("same", "likely"):
                effective_rx_patient_id = top.patient_id

        # ── Relationship enrichment ──
        link_created      = False
        link_relationship = relationship_hint

        if (
            visitor_patient_id
            and effective_rx_patient_id
            and visitor_patient_id != effective_rx_patient_id
            and visitor_classification in ("possible", "different")
        ):
            try:
                await self.graph.link(
                    pharmacy_id  = UUID(pharmacy_id),
                    ref_a        = patient_ref(visitor_patient_id),
                    ref_b        = patient_ref(effective_rx_patient_id),
                    relationship = relationship_hint,
                    confidence   = max(0.5, 1.0 - visitor_similarity),  # more different → higher link confidence
                    source       = "rx_scan",
                )
                await self.db.commit()
                link_created = True
                logger.info(
                    "[NameMatcher] Linked visitor=%s with Rx-patient=%s (similarity=%.2f)",
                    visitor_patient_id, effective_rx_patient_id, visitor_similarity,
                )
            except Exception as exc:
                logger.warning("[NameMatcher] Link creation failed: %s", exc)

        # If same person, still worth noting no new link needed
        if (
            visitor_patient_id
            and effective_rx_patient_id
            and visitor_patient_id == effective_rx_patient_id
        ):
            link_created = False  # already the same person

        return NameMatchResult(
            extracted_name    = extracted_rx_patient_name,
            visitor_name      = visitor_name,
            similarity        = round(visitor_similarity, 3),
            classification    = visitor_classification,
            db_candidates     = db_candidates,
            link_created      = link_created,
            link_relationship = link_relationship,
        )
