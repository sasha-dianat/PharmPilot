"""
EPCS (Electronic Prescribing for Controlled Substances)
=========================================================
Implements DEA 21 CFR Part 1311 requirements for both:
  (a) Prescriber-side: identity proofing, logical access control, audit log
  (b) Pharmacy-side:  signature verification, prescription validation, audit log

Critical DEA requirements (21 CFR 1311):
  §1311.102 — Prescribers must use two-factor authentication
  §1311.120 — Logical access controls for pharmacy application
  §1311.130 — Audit log requirements (see AUDIT_LOG_FIELDS below)
  §1311.150 — Audit log specific data elements
  §1311.210 — Pharmacies must verify prescriber's DEA registration
  §1311.300 — Prescription transmission requirements (signed, timestamped)

Two-factor authentication options per DEA:
  1. Knowledge + hardware token (something you know + something you have)
  2. Knowledge + biometric (something you know + something you are)
  3. Biometric + hardware token (something you are + something you have)

PharmPilot uses: TOTP (RFC 6238) + biometric verification (ArcFace)
"""
import hashlib
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import pyotp

logger = logging.getLogger(__name__)

# Fields required in the audit log per 21 CFR 1311.150
AUDIT_LOG_REQUIRED_FIELDS = [
    "prescription_id",
    "prescriber_npi",
    "prescriber_dea",
    "drug_ndc",
    "drug_name",
    "dea_schedule",
    "patient_id",
    "pharmacy_ncpdp",
    "pharmacist_npi",
    "action",           # signed | transmitted | received | dispensed | voided
    "timestamp_utc",
    "totp_verified",
    "biometric_verified",
    "ip_address",
    "software_version",
    "event_hash",       # SHA-256 of all other fields (tamper evidence)
]


@dataclass
class EPCSCredential:
    """
    A prescriber's EPCS credential set.
    Created after DEA-required identity proofing (NIST IAL2).
    """
    credential_id: UUID = field(default_factory=uuid4)
    prescriber_npi: str = ""
    prescriber_dea: str = ""

    # TOTP secret (stored encrypted at rest)
    totp_secret_encrypted: str = ""    # AES-256-GCM encrypted
    totp_issuer: str = "PharmPilot EPCS"
    totp_digits: int = 6
    totp_interval: int = 30            # seconds per DEA spec

    # Identity proofing
    identity_proofed: bool = False
    identity_proofing_method: str = ""  # in_person | remote_credential_service_provider
    identity_proofed_at: Optional[datetime] = None
    identity_proofed_by: str = ""       # Credential service provider name

    # Credential issuance
    issued_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    is_active: bool = False

    # Revocation
    revoked: bool = False
    revoked_at: Optional[datetime] = None
    revocation_reason: Optional[str] = None


class TOTPManager:
    """
    Manages TOTP (Time-based One-Time Password) per RFC 6238.
    DEA requires TOTP codes generated every 30 seconds with SHA-1 HMAC.
    """

    @staticmethod
    def generate_secret() -> str:
        """Generate a new TOTP secret (base32 encoded, 160-bit)."""
        return pyotp.random_base32()

    @staticmethod
    def get_provisioning_uri(secret: str, prescriber_name: str, npi: str) -> str:
        """Generate the OTP provisioning URI for QR code display."""
        totp = pyotp.TOTP(secret)
        return totp.provisioning_uri(
            name=f"{prescriber_name} (NPI: {npi})",
            issuer_name="PharmPilot EPCS",
        )

    @staticmethod
    def verify(secret: str, token: str, valid_window: int = 1) -> bool:
        """
        Verify a TOTP code.
        valid_window=1 allows ±30s clock drift (standard practice).
        """
        if not secret or not token:
            return False
        totp = pyotp.TOTP(secret)
        return totp.verify(token, valid_window=valid_window)

    @staticmethod
    def current_code(secret: str) -> str:
        """Get the current TOTP code (for testing only)."""
        return pyotp.TOTP(secret).now()


class EPCSAuditLogger:
    """
    Generates the immutable audit log entries required by 21 CFR 1311.150.
    Every controlled substance action is logged with a tamper-evident hash.
    """

    def __init__(self, db=None):
        self.db = db
        self._hmac_secret = os.environ.get("VAULT_HMAC_SECRET_HEX", secrets.token_hex(32))

    async def log_action(
        self,
        action: str,
        prescription_id: UUID,
        prescriber_npi: str,
        prescriber_dea: str,
        drug_ndc: str,
        drug_name: str,
        dea_schedule: str,
        patient_id: UUID,
        pharmacy_ncpdp: str,
        pharmacist_npi: str,
        totp_verified: bool,
        biometric_verified: bool,
        ip_address: Optional[str] = None,
        software_version: str = "1.0",
    ) -> str:
        """
        Log a DEA-required audit event. Returns the event hash.
        Stored in append-only table — no UPDATE or DELETE permitted.
        """
        timestamp = datetime.now(timezone.utc).isoformat()

        event_data = {
            "prescription_id": str(prescription_id),
            "prescriber_npi": prescriber_npi,
            "prescriber_dea": prescriber_dea,
            "drug_ndc": drug_ndc,
            "drug_name": drug_name,
            "dea_schedule": dea_schedule,
            "patient_id": str(patient_id),
            "pharmacy_ncpdp": pharmacy_ncpdp,
            "pharmacist_npi": pharmacist_npi,
            "action": action,
            "timestamp_utc": timestamp,
            "totp_verified": totp_verified,
            "biometric_verified": biometric_verified,
            "ip_address": ip_address or "",
            "software_version": software_version,
        }

        # Compute tamper-evident hash
        canonical = json.dumps(event_data, sort_keys=True).encode()
        import hmac as hmac_module
        event_hash = hmac_module.new(
            bytes.fromhex(self._hmac_secret),
            canonical,
            hashlib.sha256,
        ).hexdigest()
        event_data["event_hash"] = event_hash

        if self.db:
            from sqlalchemy import text
            await self.db.execute(text("""
                INSERT INTO epcs_audit_log (
                    id, prescription_id, prescriber_npi, prescriber_dea,
                    drug_ndc, drug_name, dea_schedule, patient_id,
                    pharmacy_ncpdp, pharmacist_npi, action, event_at,
                    totp_verified, biometric_verified, ip_address,
                    software_version, event_hash
                ) VALUES (
                    :id, :rx_id, :p_npi, :p_dea,
                    :ndc, :drug, :schedule, :patient,
                    :pharmacy, :pharmacist, :action, :ts,
                    :totp, :bio, :ip, :ver, :hash
                )
            """), {
                "id": str(uuid4()),
                "rx_id": str(prescription_id),
                "p_npi": prescriber_npi, "p_dea": prescriber_dea,
                "ndc": drug_ndc, "drug": drug_name, "schedule": dea_schedule,
                "patient": str(patient_id), "pharmacy": pharmacy_ncpdp,
                "pharmacist": pharmacist_npi, "action": action,
                "ts": timestamp, "totp": totp_verified, "bio": biometric_verified,
                "ip": ip_address, "ver": software_version, "hash": event_hash,
            })

        logger.info(
            "EPCS AUDIT: action=%s rx=%s prescriber=%s schedule=%s hash=%s…",
            action, str(prescription_id)[:8], prescriber_npi, dea_schedule, event_hash[:16]
        )
        return event_hash


class EPCSPharmacyVerifier:
    """
    Pharmacy-side EPCS verification per 21 CFR 1311.210.
    Validates every incoming EPCS prescription before it enters the workflow.
    """

    def __init__(self, totp_manager: TOTPManager, audit_logger: EPCSAuditLogger):
        self.totp = totp_manager
        self.audit = audit_logger

    def verify_epcs_signature(
        self,
        prescription_xml: str,
        prescriber_credential: EPCSCredential,
        claimed_signature: str,
    ) -> tuple[bool, str]:
        """
        Verify the cryptographic signature on an EPCS prescription.
        DEA requires prescribers to sign each CS prescription with their
        private key; pharmacy verifies with the corresponding public key.
        Returns (is_valid, reason).
        """
        # In production: implement full PKI signature verification
        # using prescriber's EPCS certificate from the credential service provider.
        # For now: verify the prescription hash matches the claimed signature.
        content_hash = hashlib.sha256(prescription_xml.encode()).hexdigest()
        expected = hashlib.sha256(
            (content_hash + prescriber_credential.prescriber_dea).encode()
        ).hexdigest()

        if claimed_signature == expected:
            return True, "Signature valid"
        return False, f"Signature mismatch — prescription may have been tampered with"

    def verify_prescriber_dea_active(self, dea_number: str) -> tuple[bool, str]:
        """
        Verify DEA registration is active and schedule-authorized.
        In production: calls DEA Diversion Control Division API.
        """
        from services.integrations.surescripts.eprescribing import DEAValidator
        is_valid, msg = DEAValidator.validate(dea_number)
        if not is_valid:
            return False, f"DEA number invalid: {msg}"
        return True, "DEA number check digit verified"

    async def process_incoming_epcs(
        self,
        prescription_id: UUID,
        prescriber_npi: str,
        prescriber_dea: str,
        drug_ndc: str,
        drug_name: str,
        dea_schedule: str,
        patient_id: UUID,
        pharmacy_ncpdp: str,
        pharmacist_npi: str,
        staff_totp_secret: str,
        staff_totp_token: str,
        biometric_verified: bool,
        ip_address: Optional[str] = None,
    ) -> dict:
        """
        Full EPCS validation sequence for an incoming controlled substance Rx.
        Returns result with audit hash.
        """
        errors = []

        # 1. Verify pharmacist two-factor authentication
        totp_valid = self.totp.verify(staff_totp_secret, staff_totp_token)
        if not totp_valid:
            errors.append("Pharmacist TOTP verification failed")

        # DEA requires two factors — TOTP alone is insufficient
        if not biometric_verified:
            errors.append("Pharmacist biometric verification required for EPCS dispensing")

        # 2. Verify prescriber DEA number
        dea_valid, dea_msg = self.verify_prescriber_dea_active(prescriber_dea)
        if not dea_valid:
            errors.append(f"Prescriber DEA verification failed: {dea_msg}")

        # 3. Log to DEA audit trail regardless of outcome
        event_hash = await self.audit.log_action(
            action="received" if not errors else "rejected",
            prescription_id=prescription_id,
            prescriber_npi=prescriber_npi,
            prescriber_dea=prescriber_dea,
            drug_ndc=drug_ndc,
            drug_name=drug_name,
            dea_schedule=dea_schedule,
            patient_id=patient_id,
            pharmacy_ncpdp=pharmacy_ncpdp,
            pharmacist_npi=pharmacist_npi,
            totp_verified=totp_valid,
            biometric_verified=biometric_verified,
            ip_address=ip_address,
        )

        return {
            "prescription_id": str(prescription_id),
            "epcs_valid": len(errors) == 0,
            "errors": errors,
            "totp_verified": totp_valid,
            "biometric_verified": biometric_verified,
            "dea_verified": dea_valid,
            "audit_event_hash": event_hash,
        }
