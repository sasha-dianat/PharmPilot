"""
Automatic Patient Resolver
==========================
Resolves which PharmPilot patient a prescription belongs to WITHOUT requiring
the pharmacist to manually search. The pharmacist never sees the search box
in the happy path.

Signal sources (combined into a confidence score):
  1. Biometric match  — customer already identified at entry via face recognition
  2. OCR extraction   — name + DOB parsed from handwritten/scanned prescription image
  3. Insurance ID     — BIN + member_id matched against patient_insurances table
  4. Transcript NLP   — name + DOB spoken at counter, extracted from audio transcript
  5. Online Rx ID     — prescription linked to patient account via portal session
  6. Prescriber + Drug correlation — same prescriber + same drug for known patient

Confidence tiers (Phase 32 SOP):
  HIGH   (≥ 0.88) → Auto-load patient profile. No pharmacist action needed.
  MEDIUM (0.60–0.87) → Load best candidate with a "confirm identity" banner.
  LOW    (< 0.60)  → Route to reception verification queue.
  CONFLICT         → Multiple strong candidates → reception queue + alert.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

HIGH_CONFIDENCE   = 0.88
MEDIUM_CONFIDENCE = 0.60


@dataclass
class ResolutionSignal:
    source: str           # biometric | ocr | insurance | transcript | portal | prescriber_drug
    matched_patient_id: Optional[UUID]
    confidence: float
    evidence: dict        # raw evidence details for audit


@dataclass
class PatientResolutionResult:
    resolved_patient_id: Optional[UUID]
    resolution_tier: str               # high | medium | low | conflict
    overall_confidence: float
    signals: list[ResolutionSignal]
    best_candidates: list[dict]        # [{patient_id, name, dob, confidence, signals}]
    requires_pharmacist_action: bool
    action_message: str                # What to show the pharmacist
    auto_loaded: bool = False


class PrescriptionOCRExtractor:
    """
    Extracts patient identity fields from a prescription image.
    Handles handwritten prescriptions, fax images, and printed labels.
    """

    NAME_PATTERNS = [
        r"(?:patient|for|name)[:\s]+([A-Z][a-z]+[\s,]+[A-Z][a-z]+)",
        r"^([A-Z][a-z]+,\s*[A-Z][a-z]+)",           # Last, First
        r"(?:Rx for|prescribed to)[:\s]+([A-Z].+?)(?:\n|DOB|date)",
    ]
    DOB_PATTERNS = [
        r"(?:DOB|Date of Birth|Birth Date)[:\s]*([\d/\-\.]{8,12})",
        r"(?:D\.O\.B\.)[:\s]*([\d/\-\.]{8,12})",
        r"\b(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\b",
    ]
    PHONE_PATTERNS = [
        r"(?:phone|tel|ph)[:\s]*(\(?\d{3}\)?[\s\-]\d{3}[\-]\d{4})",
        r"\b(\d{3}[\s\-\.]\d{3}[\s\-\.]\d{4})\b",
    ]
    ADDRESS_PATTERNS = [
        r"(\d+\s+[A-Za-z\s]+(?:St|Ave|Blvd|Dr|Rd|Lane|Way|Ct)[.,]?\s*\n?[A-Za-z\s]+,\s*[A-Z]{2}\s+\d{5})",
    ]

    def extract_from_text(self, text: str) -> dict:
        """Extract patient identity fields from OCR'd prescription text."""
        result = {}

        # Name extraction
        for pattern in self.NAME_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
            if m:
                result["name_raw"] = m.group(1).strip()
                result["name_parts"] = self._parse_name(result["name_raw"])
                break

        # DOB extraction
        for pattern in self.DOB_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                dob_raw = m.group(1).strip()
                result["dob_raw"] = dob_raw
                result["dob_normalized"] = self._normalize_date(dob_raw)
                break

        # Phone
        for pattern in self.PHONE_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE)
            if m:
                result["phone"] = re.sub(r"[^\d]", "", m.group(1))
                break

        # Address
        for pattern in self.ADDRESS_PATTERNS:
            m = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if m:
                result["address"] = m.group(1).strip()
                break

        return result

    def _parse_name(self, name_raw: str) -> dict:
        """Parse 'Last, First' or 'First Last' into components."""
        name_raw = name_raw.strip()
        if "," in name_raw:
            parts = [p.strip() for p in name_raw.split(",", 1)]
            return {"last": parts[0], "first": parts[1] if len(parts) > 1 else ""}
        parts = name_raw.split()
        if len(parts) >= 2:
            return {"first": parts[0], "last": parts[-1]}
        return {"first": name_raw, "last": ""}

    def _normalize_date(self, date_str: str) -> Optional[str]:
        """Normalize various date formats to YYYY-MM-DD."""
        import re
        date_str = date_str.strip()
        # Try common formats
        for fmt in ("%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y", "%m/%d/%y", "%Y-%m-%d"):
            try:
                from datetime import datetime
                dt = datetime.strptime(date_str, fmt)
                # Handle 2-digit years: 00-29 → 2000-2029, 30-99 → 1930-1999
                if dt.year < 100:
                    dt = dt.replace(year=dt.year + (2000 if dt.year < 30 else 1900))
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue
        return None


class PatientMatcher:
    """
    Database-level patient matching across multiple signal types.
    Uses PostgreSQL pg_trgm for fuzzy name matching.
    """

    def __init__(self, db):
        self.db = db

    async def match_by_name_dob(
        self,
        pharmacy_id: UUID,
        first_name: str,
        last_name: str,
        dob: Optional[str] = None,
    ) -> list[dict]:
        """Fuzzy name + exact DOB match."""
        from sqlalchemy import text
        query = """
            SELECT
                p.id,
                p.first_name,
                p.last_name,
                p.date_of_birth,
                p.phone_primary,
                similarity(lower(p.last_name), lower(:last_name)) AS last_sim,
                similarity(lower(p.first_name), lower(:first_name)) AS first_sim
            FROM patients p
            WHERE
                p.pharmacy_id = :pharmacy_id
                AND p.is_deleted = false
                AND similarity(lower(p.last_name), lower(:last_name)) > 0.3
        """
        params = {
            "pharmacy_id": str(pharmacy_id),
            "last_name": last_name,
            "first_name": first_name,
        }
        if dob:
            query += " AND p.date_of_birth = :dob"
            params["dob"] = dob

        query += " ORDER BY (last_sim + first_sim) DESC LIMIT 5"

        result = await self.db.execute(text(query), params)
        rows = result.mappings().all()
        return [
            {
                "patient_id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "dob": str(row["date_of_birth"]),
                "phone": row["phone_primary"],
                "name_similarity": float(row["last_sim"] + row["first_sim"]) / 2,
            }
            for row in rows
        ]

    async def match_by_insurance(
        self,
        pharmacy_id: UUID,
        bin_number: str,
        member_id: str,
        group_number: Optional[str] = None,
    ) -> list[dict]:
        """Exact match on insurance BIN + member ID."""
        from sqlalchemy import text
        query = """
            SELECT p.id, p.first_name, p.last_name, p.date_of_birth
            FROM patients p
            JOIN patient_insurances pi ON pi.patient_id = p.id
            WHERE
                p.pharmacy_id = :pharmacy_id
                AND p.is_deleted = false
                AND pi.bin_number = :bin
                AND pi.member_id = :member_id
                AND pi.is_active = true
        """
        params = {"pharmacy_id": str(pharmacy_id), "bin": bin_number, "member_id": member_id}
        if group_number:
            query += " AND pi.group_number = :group"
            params["group"] = group_number

        result = await self.db.execute(text(query), params)
        rows = result.mappings().all()
        return [
            {
                "patient_id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "dob": str(row["date_of_birth"]),
                "match_source": "insurance_exact",
                "name_similarity": 1.0,
            }
            for row in rows
        ]

    async def match_by_phone(self, pharmacy_id: UUID, phone: str) -> list[dict]:
        """Phone number match (digits only)."""
        from sqlalchemy import text
        phone_digits = re.sub(r"\D", "", phone)
        result = await self.db.execute(text("""
            SELECT id, first_name, last_name, date_of_birth
            FROM patients
            WHERE pharmacy_id = :pharmacy_id
              AND is_deleted = false
              AND (regexp_replace(phone_primary, '[^0-9]', '', 'g') = :phone
                OR regexp_replace(phone_secondary, '[^0-9]', '', 'g') = :phone)
        """), {"pharmacy_id": str(pharmacy_id), "phone": phone_digits})
        rows = result.mappings().all()
        return [
            {
                "patient_id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "dob": str(row["date_of_birth"]),
                "match_source": "phone_exact",
                "name_similarity": 0.9,
            }
            for row in rows
        ]

    async def match_by_biometric_identity(self, biometric_identity_id: UUID) -> list[dict]:
        """Get linked patient from a biometric identity match."""
        from sqlalchemy import text
        result = await self.db.execute(text("""
            SELECT p.id, p.first_name, p.last_name, p.date_of_birth
            FROM patients p
            WHERE p.biometric_identity_id = :bio_id AND p.is_deleted = false
        """), {"bio_id": str(biometric_identity_id)})
        rows = result.mappings().all()
        return [
            {
                "patient_id": str(row["id"]),
                "name": f"{row['first_name']} {row['last_name']}",
                "dob": str(row["date_of_birth"]),
                "match_source": "biometric",
                "name_similarity": 1.0,
            }
            for row in rows
        ]


class PatientResolver:
    """
    Orchestrates all signal sources and produces a confident resolution.
    The pharmacist sees a pre-loaded patient profile — never a search box.
    """

    def __init__(self, db, ocr_extractor: PrescriptionOCRExtractor = None):
        self.db = db
        self.matcher = PatientMatcher(db)
        self.ocr = ocr_extractor or PrescriptionOCRExtractor()

    async def resolve_from_prescription(
        self,
        pharmacy_id: UUID,
        # Signal inputs — provide whatever is available
        ocr_text: Optional[str] = None,
        insurance_bin: Optional[str] = None,
        insurance_member_id: Optional[str] = None,
        insurance_group: Optional[str] = None,
        biometric_identity_id: Optional[UUID] = None,
        biometric_confidence: float = 0.0,
        transcript_text: Optional[str] = None,
        portal_patient_id: Optional[UUID] = None,
    ) -> PatientResolutionResult:
        """
        Gather all available signals, score them, and return a resolution.
        """
        signals: list[ResolutionSignal] = []
        all_candidates: dict[str, dict] = {}  # patient_id → candidate dict

        # ── Signal 1: Portal (highest priority — explicit link) ────────────
        if portal_patient_id:
            signals.append(ResolutionSignal(
                source="portal",
                matched_patient_id=portal_patient_id,
                confidence=0.99,
                evidence={"portal_patient_id": str(portal_patient_id)},
            ))
            all_candidates[str(portal_patient_id)] = {
                "patient_id": str(portal_patient_id),
                "confidence": 0.99,
                "signals": ["portal"],
            }

        # ── Signal 2: Biometric ────────────────────────────────────────────
        if biometric_identity_id and biometric_confidence >= 0.80:
            bio_matches = await self.matcher.match_by_biometric_identity(biometric_identity_id)
            for m in bio_matches:
                pid = m["patient_id"]
                bio_score = biometric_confidence * 0.95  # Slight discount for indirect link
                signals.append(ResolutionSignal(
                    source="biometric",
                    matched_patient_id=UUID(pid),
                    confidence=bio_score,
                    evidence={"biometric_identity_id": str(biometric_identity_id), "face_confidence": biometric_confidence},
                ))
                self._merge_candidate(all_candidates, pid, bio_score, "biometric", m)

        # ── Signal 3: Insurance (exact match) ────────────────────────────
        if insurance_bin and insurance_member_id:
            ins_matches = await self.matcher.match_by_insurance(
                pharmacy_id, insurance_bin, insurance_member_id, insurance_group
            )
            for m in ins_matches:
                pid = m["patient_id"]
                signals.append(ResolutionSignal(
                    source="insurance",
                    matched_patient_id=UUID(pid),
                    confidence=0.95,
                    evidence={"bin": insurance_bin, "member_id": insurance_member_id},
                ))
                self._merge_candidate(all_candidates, pid, 0.95, "insurance", m)

        # ── Signal 4: OCR ──────────────────────────────────────────────────
        if ocr_text:
            extracted = self.ocr.extract_from_text(ocr_text)
            name_parts = extracted.get("name_parts", {})
            first = name_parts.get("first", "")
            last = name_parts.get("last", "")
            dob = extracted.get("dob_normalized")
            phone = extracted.get("phone")

            if last and first:
                ocr_matches = await self.matcher.match_by_name_dob(
                    pharmacy_id, first, last, dob
                )
                for m in ocr_matches:
                    pid = m["patient_id"]
                    # Confidence scales with name similarity and DOB presence
                    conf = m["name_similarity"] * (0.85 if dob else 0.65)
                    # Boost if DOB matched exactly
                    if dob and dob == m.get("dob"):
                        conf = min(0.95, conf + 0.15)
                    signals.append(ResolutionSignal(
                        source="ocr",
                        matched_patient_id=UUID(pid),
                        confidence=conf,
                        evidence={"name": f"{first} {last}", "dob": dob, "extracted": extracted},
                    ))
                    self._merge_candidate(all_candidates, pid, conf, "ocr", m)

            if phone and not all_candidates:
                phone_matches = await self.matcher.match_by_phone(pharmacy_id, phone)
                for m in phone_matches:
                    pid = m["patient_id"]
                    self._merge_candidate(all_candidates, pid, 0.70, "ocr_phone", m)

        # ── Signal 5: Transcript NLP ──────────────────────────────────────
        if transcript_text:
            # Re-use OCR extractor on transcript text (same patterns)
            extracted = self.ocr.extract_from_text(transcript_text)
            name_parts = extracted.get("name_parts", {})
            first = name_parts.get("first", "")
            last = name_parts.get("last", "")
            dob = extracted.get("dob_normalized")

            if last and first:
                trans_matches = await self.matcher.match_by_name_dob(
                    pharmacy_id, first, last, dob
                )
                for m in trans_matches:
                    pid = m["patient_id"]
                    conf = m["name_similarity"] * 0.80  # Spoken name slightly less reliable
                    if dob and dob == m.get("dob"):
                        conf = min(0.92, conf + 0.12)
                    self._merge_candidate(all_candidates, pid, conf, "transcript", m)

        return self._build_resolution(all_candidates, signals)

    def _merge_candidate(
        self,
        candidates: dict,
        patient_id: str,
        confidence: float,
        source: str,
        metadata: dict,
    ) -> None:
        if patient_id not in candidates:
            candidates[patient_id] = {
                "patient_id": patient_id,
                "name": metadata.get("name", ""),
                "dob": metadata.get("dob", ""),
                "confidence": confidence,
                "signals": [source],
            }
        else:
            existing = candidates[patient_id]
            # Combine confidences: C_combined = 1 - (1-C1)*(1-C2) (independent evidence)
            c1 = existing["confidence"]
            existing["confidence"] = min(0.99, 1 - (1 - c1) * (1 - confidence))
            if source not in existing["signals"]:
                existing["signals"].append(source)

    def _build_resolution(
        self,
        candidates: dict,
        signals: list[ResolutionSignal],
    ) -> PatientResolutionResult:
        if not candidates:
            return PatientResolutionResult(
                resolved_patient_id=None,
                resolution_tier="low",
                overall_confidence=0.0,
                signals=signals,
                best_candidates=[],
                requires_pharmacist_action=True,
                action_message="Patient could not be identified. Manual search required.",
            )

        sorted_candidates = sorted(
            candidates.values(),
            key=lambda c: c["confidence"],
            reverse=True,
        )
        best = sorted_candidates[0]
        runner_up = sorted_candidates[1] if len(sorted_candidates) > 1 else None

        confidence = best["confidence"]

        # Conflict detection: two strong candidates very close in confidence
        is_conflict = (
            runner_up is not None
            and runner_up["confidence"] >= MEDIUM_CONFIDENCE
            and (confidence - runner_up["confidence"]) < 0.15
        )

        if is_conflict:
            return PatientResolutionResult(
                resolved_patient_id=None,
                resolution_tier="conflict",
                overall_confidence=confidence,
                signals=signals,
                best_candidates=sorted_candidates[:3],
                requires_pharmacist_action=True,
                action_message=(
                    f"Multiple possible patients: {best['name']} ({confidence:.0%}) "
                    f"and {runner_up['name']} ({runner_up['confidence']:.0%}). "
                    "Receptionist verification required."
                ),
            )

        if confidence >= HIGH_CONFIDENCE:
            return PatientResolutionResult(
                resolved_patient_id=UUID(best["patient_id"]),
                resolution_tier="high",
                overall_confidence=confidence,
                signals=signals,
                best_candidates=sorted_candidates[:3],
                requires_pharmacist_action=False,
                action_message="",
                auto_loaded=True,
            )
        elif confidence >= MEDIUM_CONFIDENCE:
            return PatientResolutionResult(
                resolved_patient_id=UUID(best["patient_id"]),
                resolution_tier="medium",
                overall_confidence=confidence,
                signals=signals,
                best_candidates=sorted_candidates[:3],
                requires_pharmacist_action=True,
                action_message=(
                    f"Best match: {best['name']} ({confidence:.0%} confidence). "
                    "Please confirm patient identity before proceeding."
                ),
                auto_loaded=True,
            )
        else:
            return PatientResolutionResult(
                resolved_patient_id=UUID(best["patient_id"]) if best else None,
                resolution_tier="low",
                overall_confidence=confidence,
                signals=signals,
                best_candidates=sorted_candidates[:3],
                requires_pharmacist_action=True,
                action_message="Low-confidence match. Receptionist verification required.",
            )
