"""
Identity Orchestrator — automatic, zero-manual-entry patient identification.
============================================================================
Implements the three requirements:

  1. ARCHIVE BIOMETRICS for returning-customer recognition (loyalty + safety):
     every recognised/observed customer is archived; on return we match the
     archived biometric and bump loyalty + visit counters.

  2. UNTYPED PERSON-LINKING: a recognised customer fans out to ALL linked
     patient profiles (self + family) via the person-link graph, so the
     pharmacist is offered every possible identity to browse — without us
     caring what the relationship is.

  3. NO MANUAL ENTRY: identity is assembled from
       (a) reception-booth conversation transcript,
       (b) photo of the manual prescription (OCR),
       (c) the Iranian insurance eligibility platform (authoritative),
     and patient records are auto-created/updated from those sources.

Default identity system is Iranian (national code primary, Jalali dual-store).
American mode is available by configuration.

Pharmacist control: AI assembles and proposes; the pharmacist browses the
candidate set and picks. Nothing here auto-dispenses.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.core.localization.iranian_extract import (
    ExtractedIdentity,
    IranianIdentityExtractor,
)
from services.core.localization.jalali import date_to_jalali_str
from services.integrations.iranian_insurance.base import InsuranceMember
from services.integrations.iranian_insurance.registry import (
    AggregatedCoverage,
    IranianInsuranceRegistry,
)
from services.biometric.identity_resolution.person_links import (
    PersonLinkGraph,
    customer_ref,
    patient_ref,
)

logger = logging.getLogger(__name__)


@dataclass
class CandidateProfile:
    """One browsable identity candidate with history summaries."""
    patient_id: str
    national_id: Optional[str]
    name: str
    name_fa: Optional[str]
    dob: Optional[str]
    dob_jalali: Optional[str]
    gender: Optional[str]
    relationship_hint: Optional[str]       # optional; "self" for the principal
    is_self: bool
    confidence: float
    # history summaries
    active_rx_count: int = 0
    recent_fills: list[dict] = field(default_factory=list)
    allergies: list[str] = field(default_factory=list)
    conditions: list[str] = field(default_factory=list)
    loyalty_points: int = 0
    total_visits: int = 0


@dataclass
class IdentityResolution:
    customer_id: Optional[str]
    primary_candidate_id: Optional[str]
    candidates: list[CandidateProfile]
    extracted: dict
    insurance_summary: dict
    is_returning_customer: bool
    auto_loaded: bool
    message: str


class IdentityOrchestrator:
    def __init__(
        self,
        db: AsyncSession,
        insurance_registry: IranianInsuranceRegistry,
        identity_system: str = "iranian",
    ):
        self.db = db
        self.insurance = insurance_registry
        self.identity_system = identity_system
        self.extractor = IranianIdentityExtractor()
        self.links = PersonLinkGraph(db)

    # ── public entry point ────────────────────────────────────────────────────

    async def identify(
        self,
        pharmacy_id: UUID,
        *,
        transcript_text: Optional[str] = None,
        prescription_ocr_text: Optional[str] = None,
        national_code: Optional[str] = None,
        biometric_identity_id: Optional[UUID] = None,
        biometric_confidence: float = 0.0,
    ) -> IdentityResolution:
        """
        Assemble identity from all available automatic sources and return the
        full candidate set for the pharmacist to browse.
        """
        # ── Step 1: extract identity from transcript + Rx photo ───────────────
        extracted = self._merge_extractions(transcript_text, prescription_ocr_text)
        nc = national_code or extracted.national_code

        # ── Step 2: insurance lookup (authoritative demographics + family) ────
        coverage: Optional[AggregatedCoverage] = None
        if nc and self.identity_system == "iranian":
            try:
                coverage = await self.insurance.aggregate_coverage(nc)
            except Exception as e:
                logger.warning("Insurance lookup failed for masked code: %s", e)

        # ── Step 3: resolve/create the principal patient ──────────────────────
        principal_member = coverage.primary if coverage else None
        principal_id = await self._upsert_patient(
            pharmacy_id, nc, extracted, principal_member, auto_created=True,
        )

        # ── Step 4: resolve/create + link every insurance-linked family member ─
        if coverage and principal_id:
            for member in coverage.linked_persons:
                if member.national_code == nc:
                    continue
                linked_pid = await self._upsert_patient(
                    pharmacy_id, member.national_code, None, member, auto_created=True,
                )
                if linked_pid:
                    await self.links.link(
                        pharmacy_id,
                        patient_ref(principal_id),
                        patient_ref(linked_pid),
                        relationship=member.relationship_to_principal,
                        confidence=0.95,
                        source="insurance",
                    )

        # ── Step 5: biometric archival + returning-customer recognition ───────
        customer_id, is_returning = await self._archive_customer(
            pharmacy_id, biometric_identity_id, nc, extracted, principal_id,
        )

        # ── Step 6: fan out to the full candidate set via the link graph ──────
        candidate_ids = await self._candidate_patient_ids(principal_id, customer_id)
        candidates = await self._build_candidates(candidate_ids, principal_id, coverage)

        auto_loaded = bool(principal_id) and (
            (extracted.national_code_valid) or biometric_confidence >= 0.86 or is_returning
        )
        msg = self._message(candidates, is_returning, auto_loaded)

        return IdentityResolution(
            customer_id=str(customer_id) if customer_id else None,
            primary_candidate_id=str(principal_id) if principal_id else None,
            candidates=candidates,
            extracted=self._extracted_to_dict(extracted),
            insurance_summary=self._insurance_summary(coverage),
            is_returning_customer=is_returning,
            auto_loaded=auto_loaded,
            message=msg,
        )

    # ── extraction merge ──────────────────────────────────────────────────────

    def _merge_extractions(
        self, transcript: Optional[str], ocr: Optional[str]
    ) -> ExtractedIdentity:
        t = self.extractor.extract(transcript, "transcript") if transcript else ExtractedIdentity()
        o = self.extractor.extract(ocr, "ocr") if ocr else ExtractedIdentity()
        # Prefer whichever has the valid national code; merge field-by-field.
        primary, secondary = (o, t) if o.national_code_valid and not t.national_code_valid else (t, o)
        for fld in ("national_code", "national_code_valid", "first_name", "last_name",
                    "father_name", "dob", "dob_jalali", "gender", "phone", "full_name_raw"):
            if not getattr(primary, fld):
                val = getattr(secondary, fld)
                if val:
                    setattr(primary, fld, val)
        primary.confidence = max(t.confidence, o.confidence)
        return primary

    # ── patient upsert (auto-created from sources) ────────────────────────────

    async def _upsert_patient(
        self,
        pharmacy_id: UUID,
        national_code: Optional[str],
        extracted: Optional[ExtractedIdentity],
        member: Optional[InsuranceMember],
        auto_created: bool,
    ) -> Optional[str]:
        """Find a patient by national code (or create one). Insurance data wins."""
        # Demographic source of truth: insurance > extraction.
        first = (member.first_name if member else None) or (extracted.first_name if extracted else None)
        last = (member.last_name if member else None) or (extracted.last_name if extracted else None)
        father = (member.father_name if member else None) or (extracted.father_name if extracted else None)
        dob: Optional[date] = (member.date_of_birth if member else None) or (extracted.dob if extracted else None)
        dob_j = (member.date_of_birth_jalali if member else None) or (extracted.dob_jalali if extracted else None)
        if dob and not dob_j:
            dob_j = date_to_jalali_str(dob)
        gender = (member.gender if member else None) or (extracted.gender if extracted else None)

        if not national_code and not (first and last):
            return None  # nothing to anchor on

        # Look up existing patient by national code first.
        if national_code:
            row = await self.db.execute(
                text("SELECT id FROM patients WHERE pharmacy_id=:p AND national_id=:nc AND is_deleted=false LIMIT 1"),
                {"p": str(pharmacy_id), "nc": national_code},
            )
            existing = row.scalar()
            if existing:
                # enrich missing fields
                await self.db.execute(
                    text("""
                        UPDATE patients SET
                          first_name = COALESCE(NULLIF(first_name,''), :f, first_name),
                          last_name  = COALESCE(NULLIF(last_name,''), :l, last_name),
                          father_name = COALESCE(father_name, :fa),
                          date_of_birth_jalali = COALESCE(date_of_birth_jalali, :dj),
                          updated_at = now()
                        WHERE id = :id
                    """),
                    {"f": first, "l": last, "fa": father, "dj": dob_j, "id": str(existing)},
                )
                return str(existing)

        # Create a new auto-extracted patient.
        if not (first and last and dob):
            # Insufficient to safely create a clinical record; require name+dob.
            return None
        pid = str(uuid4())
        provenance = {
            "auto_created": True,
            "sources": [s for s, v in (("insurance", member), ("extraction", extracted)) if v],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        import json
        await self.db.execute(
            text("""
                INSERT INTO patients
                  (id, pharmacy_id, first_name, last_name, date_of_birth, gender,
                   national_id, date_of_birth_jalali, father_name, identity_system,
                   auto_created, identity_provenance, status, created_at, updated_at)
                VALUES
                  (:id, :p, :f, :l, :dob, :g, :nc, :dj, :fa, :sys,
                   true, CAST(:prov AS JSONB), 'active', now(), now())
            """),
            {"id": pid, "p": str(pharmacy_id), "f": first, "l": last, "dob": dob,
             "g": gender or "U", "nc": national_code, "dj": dob_j, "fa": father,
             "sys": self.identity_system, "prov": json.dumps(provenance)},
        )
        # Seed insurance record from the member if present.
        if member and member.policy_number:
            await self.db.execute(
                text("""
                    INSERT INTO patient_insurances
                      (id, patient_id, bin_number, member_id, priority, is_active, created_at, updated_at)
                    VALUES (:id, :pid, :bin, :mid, 1, true, now(), now())
                    ON CONFLICT DO NOTHING
                """),
                {"id": str(uuid4()), "pid": pid,
                 "bin": (member.org.value[:6].upper()), "mid": national_code or member.policy_number},
            )
        logger.info("Auto-created patient %s from %s", pid, provenance["sources"])
        return pid

    # ── biometric archival + recognition ──────────────────────────────────────

    async def _archive_customer(
        self,
        pharmacy_id: UUID,
        biometric_identity_id: Optional[UUID],
        national_code: Optional[str],
        extracted: ExtractedIdentity,
        principal_id: Optional[str],
    ) -> tuple[Optional[str], bool]:
        """
        Archive (or recognise) the customer for loyalty + medical-safety.
        Returns (customer_id, is_returning_customer).
        """
        is_returning = False
        customer_id: Optional[str] = None

        # Match an archived customer by biometric identity or national code.
        if biometric_identity_id:
            row = await self.db.execute(
                text("SELECT id, visit_count FROM customer_identities WHERE pharmacy_id=:p AND biometric_identity_id=:b LIMIT 1"),
                {"p": str(pharmacy_id), "b": str(biometric_identity_id)},
            )
            hit = row.mappings().first()
            if hit:
                customer_id = str(hit["id"])
                is_returning = True
        if not customer_id and national_code:
            row = await self.db.execute(
                text("SELECT id, visit_count FROM customer_identities WHERE pharmacy_id=:p AND national_id=:nc LIMIT 1"),
                {"p": str(pharmacy_id), "nc": national_code},
            )
            hit = row.mappings().first()
            if hit:
                customer_id = str(hit["id"])
                is_returning = True

        name_fa = extracted.full_name_raw
        display = f"{extracted.first_name or ''} {extracted.last_name or ''}".strip() or None
        import json
        demo = json.dumps({
            "national_code": national_code,
            "dob_jalali": extracted.dob_jalali,
            "gender": extracted.gender,
            "phone": extracted.phone,
        })

        if customer_id:
            # Returning: bump loyalty + visit counters.
            await self.db.execute(
                text("""
                    UPDATE customer_identities SET
                      visit_count = visit_count + 1,
                      loyalty_points = loyalty_points + 10,
                      last_seen_at = now(),
                      national_id = COALESCE(national_id, :nc),
                      display_name = COALESCE(display_name, :dn),
                      display_name_fa = COALESCE(display_name_fa, :dnf)
                    WHERE id = :id
                """),
                {"id": customer_id, "nc": national_code, "dn": display, "dnf": name_fa},
            )
        else:
            # First sight: archive a new customer identity.
            customer_id = str(uuid4())
            await self.db.execute(
                text("""
                    INSERT INTO customer_identities
                      (id, pharmacy_id, biometric_identity_id, national_id, display_name,
                       display_name_fa, extracted_demographics, visit_count, loyalty_points,
                       is_archived, is_temporary, first_seen_at, last_seen_at, created_at)
                    VALUES (:id, :p, :b, :nc, :dn, :dnf, CAST(:demo AS JSONB),
                            1, 10, true, false, now(), now(), now())
                """),
                {"id": customer_id, "p": str(pharmacy_id),
                 "b": str(biometric_identity_id) if biometric_identity_id else None,
                 "nc": national_code, "dn": display, "dnf": name_fa, "demo": demo},
            )

        # Link the customer to the principal patient (untyped).
        if principal_id:
            await self.links.link(
                pharmacy_id, customer_ref(customer_id), patient_ref(principal_id),
                relationship="self", confidence=1.0, source="resolution",
            )
        return customer_id, is_returning

    # ── candidate assembly ────────────────────────────────────────────────────

    async def _candidate_patient_ids(
        self, principal_id: Optional[str], customer_id: Optional[str]
    ) -> list[str]:
        """All linked patient ids (self + relations), deduped, principal first."""
        ids: list[str] = []
        start = customer_ref(customer_id) if customer_id else (
            patient_ref(principal_id) if principal_id else None)
        if start:
            refs = await self.links.connected_component(start, max_depth=3)
            ids = self.links.patient_ids_from_refs(refs)
        if principal_id and principal_id not in ids:
            ids.insert(0, principal_id)
        # principal first
        if principal_id and principal_id in ids:
            ids.remove(principal_id)
            ids.insert(0, principal_id)
        return ids

    async def _build_candidates(
        self,
        patient_ids: list[str],
        principal_id: Optional[str],
        coverage: Optional[AggregatedCoverage],
    ) -> list[CandidateProfile]:
        candidates: list[CandidateProfile] = []
        for pid in patient_ids:
            prow = await self.db.execute(
                text("""
                    SELECT id, first_name, last_name, national_id, date_of_birth,
                           date_of_birth_jalali, gender
                    FROM patients WHERE id = :id AND is_deleted = false
                """),
                {"id": pid},
            )
            p = prow.mappings().first()
            if not p:
                continue
            # history summaries
            rx_count = (await self.db.execute(
                text("SELECT count(*) FROM prescriptions WHERE patient_id=:id AND status NOT IN ('cancelled','dispensed')"),
                {"id": pid})).scalar() or 0
            fills_rows = (await self.db.execute(
                text("""SELECT r.drug_name AS drug_name, f.fill_date AS fill_date
                        FROM prescription_fills f
                        JOIN prescriptions r ON r.id = f.prescription_id
                        WHERE r.patient_id = :id ORDER BY f.fill_date DESC LIMIT 5"""),
                {"id": pid})).mappings().all()
            allergies = [r["allergen_name"] for r in (await self.db.execute(
                text("SELECT allergen_name FROM patient_allergies WHERE patient_id=:id LIMIT 8"),
                {"id": pid})).mappings().all()]
            loyalty = 0
            visits = 0
            crow = (await self.db.execute(
                text("""SELECT loyalty_points, visit_count FROM customer_identities
                        WHERE id IN (
                          SELECT split_part(person_a_ref,':',2)::uuid FROM person_links
                          WHERE person_b_ref = :pref AND person_a_ref LIKE 'customer:%'
                          UNION
                          SELECT split_part(person_b_ref,':',2)::uuid FROM person_links
                          WHERE person_a_ref = :pref AND person_b_ref LIKE 'customer:%'
                        ) LIMIT 1"""),
                {"pref": patient_ref(pid)})).mappings().first()
            if crow:
                loyalty = crow["loyalty_points"] or 0
                visits = crow["visit_count"] or 0

            member = None
            if coverage:
                member = next((m for m in coverage.linked_persons
                               if m.national_code == p["national_id"]), None)

            candidates.append(CandidateProfile(
                patient_id=str(p["id"]),
                national_id=p["national_id"],
                name=f"{p['first_name']} {p['last_name']}".strip(),
                name_fa=f"{p['first_name']} {p['last_name']}".strip(),
                dob=str(p["date_of_birth"]) if p["date_of_birth"] else None,
                dob_jalali=p["date_of_birth_jalali"],
                gender=p["gender"],
                relationship_hint=("self" if str(p["id"]) == str(principal_id)
                                   else (member.relationship_to_principal if member else None)),
                is_self=(str(p["id"]) == str(principal_id)),
                confidence=1.0 if str(p["id"]) == str(principal_id) else 0.9,
                active_rx_count=int(rx_count),
                recent_fills=[{"drug": r["drug_name"], "date": str(r["fill_date"])} for r in fills_rows],
                allergies=allergies,
                loyalty_points=loyalty,
                total_visits=visits,
            ))
        return candidates

    # ── helpers ───────────────────────────────────────────────────────────────

    def _message(self, candidates, is_returning, auto_loaded) -> str:
        if not candidates:
            return "هیچ پروفایلی شناسایی نشد — شناسایی دستی لازم است. (No profile identified.)"
        n = len(candidates)
        prefix = "مشتری بازگشتی شناسایی شد. " if is_returning else ""
        if n == 1:
            return f"{prefix}پروفایل بارگذاری شد: {candidates[0].name}."
        return (f"{prefix}{n} پروفایل مرتبط یافت شد (خود فرد + بستگان). "
                f"لطفاً بیمار را انتخاب کنید. ({n} linked profiles — please select the patient.)")

    def _extracted_to_dict(self, e: ExtractedIdentity) -> dict:
        return {
            "national_code": e.national_code,
            "national_code_valid": e.national_code_valid,
            "first_name": e.first_name,
            "last_name": e.last_name,
            "father_name": e.father_name,
            "dob": str(e.dob) if e.dob else None,
            "dob_jalali": e.dob_jalali,
            "gender": e.gender,
            "phone": e.phone,
            "confidence": e.confidence,
            "provenance": e.provenance,
        }

    def _insurance_summary(self, coverage: Optional[AggregatedCoverage]) -> dict:
        if not coverage:
            return {"available": False}
        return {
            "available": True,
            "primary_org": coverage.primary.org.value if coverage.primary else None,
            "memberships": [m.org.value for m in coverage.all_memberships],
            "linked_count": len(coverage.linked_persons),
        }
