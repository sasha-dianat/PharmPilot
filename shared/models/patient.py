from datetime import date, datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Date, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import AuditedBase


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"
    OTHER = "O"
    UNKNOWN = "U"


class PatientStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    DECEASED = "deceased"
    TRANSFERRED = "transferred"


class Patient(AuditedBase):
    __tablename__ = "patients"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)

    # Demographics
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[Gender] = mapped_column(String(1), nullable=False)
    ssn_last4: Mapped[str | None] = mapped_column(String(4), nullable=True)

    # ── Iranian identity (migration 0003) ───────────────────────────────
    # کد ملی — 10-digit national ID (Luhn-validated at intake)
    national_id: Mapped[str | None] = mapped_column(String(10), nullable=True, index=True)
    # Jalali (Shamsi) date of birth for display, e.g. "1357/06/31"
    date_of_birth_jalali: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # "iranian" | "american"  (set by onboarding; controls which fields appear)
    identity_system: Mapped[str] = mapped_column(String(20), default="american")
    # Father's name — used in many Iranian ID documents
    father_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # Whether this patient record was auto-created from OCR/voice rather than manual entry
    auto_created: Mapped[bool] = mapped_column(default=False)
    # JSON blob: {"source": "voice", "confidence": 0.97, "matched_insurer": "salamat"}
    identity_provenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Contact
    phone_primary: Mapped[str | None] = mapped_column(String(20), nullable=True)
    phone_secondary: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    preferred_contact_method: Mapped[str] = mapped_column(String(20), default="phone")
    preferred_language: Mapped[str] = mapped_column(String(10), default="en")

    # Address
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Clinical flags
    status: Mapped[PatientStatus] = mapped_column(String(20), default=PatientStatus.ACTIVE)
    is_caregiver_account: Mapped[bool] = mapped_column(default=False)
    primary_patient_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patients.id"), nullable=True
    )
    weight_kg: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    pregnancy_status: Mapped[str | None] = mapped_column(String(30), nullable=True)
    renal_function: Mapped[str | None] = mapped_column(String(40), nullable=True)
    hepatic_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    conditions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # Biometric linkage
    biometric_identity_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    biometric_enrolled: Mapped[bool] = mapped_column(default=False)

    # AI-enriched fields (populated by audio transcription + clinical brain)
    ai_profile_notes: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    communication_preferences: Mapped[dict] = mapped_column(
        JSONB, default=dict, nullable=False
    )

    # Relationships
    allergies: Mapped[list["PatientAllergy"]] = relationship(back_populates="patient")
    medications: Mapped[list["Medication"]] = relationship(back_populates="patient")
    prescriptions: Mapped[list["Prescription"]] = relationship(back_populates="patient")
    insurance_plans: Mapped[list["PatientInsurance"]] = relationship(back_populates="patient")
    lab_results: Mapped[list["LabResult"]] = relationship(back_populates="patient")
    clinical_notes: Mapped[list["ClinicalNote"]] = relationship(back_populates="patient")
    audio_transcripts: Mapped[list["AudioTranscript"]] = relationship(back_populates="patient")
    visit_records: Mapped[list["PharmacyVisit"]] = relationship(back_populates="patient")


class PatientAllergy(AuditedBase):
    __tablename__ = "patient_allergies"

    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    allergen_type: Mapped[str] = mapped_column(String(20), nullable=False)  # drug, food, environmental
    allergen_name: Mapped[str] = mapped_column(String(255), nullable=False)
    allergen_ndc: Mapped[str | None] = mapped_column(String(11), nullable=True)
    allergen_rxcui: Mapped[str | None] = mapped_column(String(20), nullable=True)
    reaction: Mapped[str | None] = mapped_column(String(500), nullable=True)
    severity: Mapped[str] = mapped_column(String(20), default="unknown")  # mild, moderate, severe, fatal
    onset_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="patient_reported")  # patient_reported, chart, ai_extracted

    patient: Mapped["Patient"] = relationship(back_populates="allergies")


class LabResult(AuditedBase):
    __tablename__ = "lab_results"

    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)
    loinc_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    value: Mapped[str] = mapped_column(String(100), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reference_range: Mapped[str | None] = mapped_column(String(100), nullable=True)
    abnormal_flag: Mapped[str | None] = mapped_column(String(10), nullable=True)
    result_date: Mapped[datetime] = mapped_column(nullable=False)
    ordering_provider_npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    source: Mapped[str] = mapped_column(String(50), default="hl7_import")  # hl7_import, manual, ai_extracted

    patient: Mapped["Patient"] = relationship(back_populates="lab_results")


class ClinicalNote(AuditedBase):
    __tablename__ = "clinical_notes"

    patient_id: Mapped[UUID] = mapped_column(ForeignKey("patients.id"), nullable=False, index=True)
    note_type: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(50), default="pharmacist")
    ai_generated: Mapped[bool] = mapped_column(default=False)
    pharmacist_reviewed: Mapped[bool] = mapped_column(default=False)
    pharmacist_id: Mapped[UUID | None] = mapped_column(nullable=True)

    patient: Mapped["Patient"] = relationship(back_populates="clinical_notes")
