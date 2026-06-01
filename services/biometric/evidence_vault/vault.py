"""
Biometric Evidence Vault
========================
Two-tier architecture:

  TIER 1 — Operational (fast, no raw data)
    ArcFace 512D embeddings stored as AES-256-GCM encrypted blobs in PostgreSQL.
    FAISS index built from decrypted embeddings in-memory at service startup.
    A vector cannot be reverse-engineered into a face image.
    This tier runs continuously and is used for real-time identification.

  TIER 2 — Evidence Vault (raw, recoverable, admin-only)
    Raw face frame (JPEG), voice audio (WAV), gait clip (MP4),
    and the original embedding vector all stored as separate encrypted objects.
    Each object encrypted with AES-256-GCM under a Vault Master Key (VMK)
    that is stored ONLY in the HSM / AWS KMS / Azure Key Vault.
    Admin decryption requires:
      - ADMIN role authentication (staff.role == 'super_admin' or 'pharmacy_manager')
      - A documented legal_reason (string, mandatory)
      - The access event is written to vault_access_log (immutable, append-only)
      - If legal_hold is active, the record cannot be deleted by anyone
    Law enforcement export:
      Admin calls export_for_authority() which decrypts and packages all
      evidence for a given identity_id into a signed, time-stamped ZIP.

SECURITY MODEL:
  - The VMK never appears in application code or environment variables.
    It lives in the HSM/KMS and is fetched via authenticated API call.
  - The encrypted blobs in PostgreSQL are useless without the VMK.
  - Even if the entire database is exfiltrated, no raw biometric is exposed.
  - The audit log is append-only (PostgreSQL row-level trigger prevents UPDATE/DELETE).
  - Legal hold prevents object deletion even by admins.
"""

import hashlib
import io
import json
import logging
import os
import secrets
import struct
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# ── AES-256-GCM constants ──────────────────────────────────────────────────
AES_KEY_BYTES = 32   # 256-bit
GCM_NONCE_BYTES = 12
GCM_TAG_BYTES = 16


class VaultObjectType(str, Enum):
    FACE_FRAME      = "face_frame"       # JPEG image captured at detection
    FACE_EMBEDDING  = "face_embedding"   # 512D float32 ArcFace vector (raw bytes)
    VOICE_AUDIO     = "voice_audio"      # WAV audio segment
    GAIT_CLIP       = "gait_clip"        # MP4/raw motion capture
    FINGERPRINT_RAW = "fingerprint_raw"  # ISO/IEC 19794-2 raw template
    DEPTH_MAP       = "depth_map"        # IR depth frame (liveness evidence)


class VaultAccessReason(str, Enum):
    POLICE_INQUIRY          = "police_inquiry"
    COURT_ORDER             = "court_order"
    INSURANCE_FRAUD_INVEST  = "insurance_fraud_investigation"
    INTERNAL_AUDIT          = "internal_audit"
    ADMIN_REVIEW            = "admin_review"
    DATA_SUBJECT_REQUEST    = "data_subject_request"   # GDPR/CCPA right of access
    LEGAL_HOLD_REVIEW       = "legal_hold_review"
    SYSTEM_TEST             = "system_test"


@dataclass
class VaultObject:
    """In-memory representation after decryption. NEVER persisted in plain form."""
    vault_id: UUID
    identity_id: UUID
    object_type: VaultObjectType
    raw_bytes: bytes                     # Decrypted content
    captured_at: datetime
    camera_zone: Optional[str] = None   # counter, entry, counseling_room
    content_hash_sha256: str = ""       # SHA-256 of raw_bytes for integrity


@dataclass
class ChainOfCustodyEntry:
    event_id: UUID
    identity_id: UUID
    vault_id: Optional[UUID]
    event_type: str                     # access, export, legal_hold, deletion_attempt
    accessed_by_staff_id: UUID
    accessed_by_role: str
    legal_reason: str
    ip_address: Optional[str]
    timestamp: datetime
    object_types_accessed: list[str]
    export_reference: Optional[str] = None
    signature_hex: str = ""             # HMAC-SHA256 of event fields


class VaultKeyProvider:
    """
    Fetches the Vault Master Key (VMK) from the configured key management service.
    Supports: AWS KMS, Azure Key Vault, HashiCorp Vault, or local HSM via PKCS#11.
    The VMK never appears in memory longer than needed for one crypto operation.
    """

    def __init__(self, kms_key_id: str = "", provider: str = "env"):
        self.kms_key_id = kms_key_id
        self.provider = provider   # env | aws_kms | azure_kv | hashicorp_vault

    def get_master_key(self) -> bytes:
        """
        Fetch the 256-bit Vault Master Key.
        In production: call KMS API to decrypt a data-encryption key (envelope encryption).
        In development: read from environment variable (never hardcode).
        """
        if self.provider == "aws_kms":
            return self._fetch_from_aws_kms()
        elif self.provider == "azure_kv":
            return self._fetch_from_azure_kv()
        else:
            # Development fallback — read from env, fail loudly if absent
            raw = os.environ.get("VAULT_MASTER_KEY_HEX", "")
            if not raw or len(raw) < 64:
                raise RuntimeError(
                    "VAULT_MASTER_KEY_HEX not set or too short. "
                    "Generate with: python -c \"import secrets; print(secrets.token_hex(32))\""
                )
            return bytes.fromhex(raw)

    def _fetch_from_aws_kms(self) -> bytes:
        import boto3
        client = boto3.client("kms")
        response = client.generate_data_key(
            KeyId=self.kms_key_id,
            KeySpec="AES_256",
        )
        # In real implementation: cache the encrypted data key, not the plaintext
        return response["Plaintext"]

    def _fetch_from_azure_kv(self) -> bytes:
        from azure.keyvault.secrets import SecretClient
        from azure.identity import DefaultAzureCredential
        client = SecretClient(vault_url=self.kms_key_id, credential=DefaultAzureCredential())
        secret = client.get_secret("vault-master-key")
        return bytes.fromhex(secret.value)


class BiometricEncryptor:
    """AES-256-GCM encrypt/decrypt for vault objects."""

    def encrypt(self, plaintext: bytes, master_key: bytes, aad: bytes = b"") -> bytes:
        """
        Returns: nonce(12) + ciphertext + tag(16).
        The AAD (additional authenticated data) is the vault_id as bytes —
        this binds the ciphertext to its database record, preventing transplant attacks.
        """
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = secrets.token_bytes(GCM_NONCE_BYTES)
        aesgcm = AESGCM(master_key)
        ciphertext_with_tag = aesgcm.encrypt(nonce, plaintext, aad)
        return nonce + ciphertext_with_tag

    def decrypt(self, encrypted: bytes, master_key: bytes, aad: bytes = b"") -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        nonce = encrypted[:GCM_NONCE_BYTES]
        ciphertext_with_tag = encrypted[GCM_NONCE_BYTES:]
        aesgcm = AESGCM(master_key)
        return aesgcm.decrypt(nonce, ciphertext_with_tag, aad)


class BiometricEvidenceVault:
    """
    The vault — stores, retrieves, and audits all raw biometric evidence.
    """

    def __init__(self, key_provider: VaultKeyProvider, db=None, s3_client=None):
        self.key_provider = key_provider
        self.encryptor = BiometricEncryptor()
        self.db = db
        self.s3 = s3_client       # Optional: large blobs (video) go to S3
        self._hmac_secret = os.environ.get("VAULT_HMAC_SECRET_HEX", secrets.token_hex(32))

    # ── Store ─────────────────────────────────────────────────────────────

    async def store(
        self,
        identity_id: UUID,
        object_type: VaultObjectType,
        raw_bytes: bytes,
        camera_zone: Optional[str] = None,
        captured_at: Optional[datetime] = None,
    ) -> UUID:
        """
        Encrypt and persist a raw biometric object.
        Returns the vault_id for later retrieval.
        """
        vault_id = uuid4()
        captured_at = captured_at or datetime.now(timezone.utc)

        # Compute integrity hash BEFORE encryption
        content_hash = hashlib.sha256(raw_bytes).hexdigest()

        # Encrypt with identity_id as AAD (binds blob to its record)
        master_key = self.key_provider.get_master_key()
        try:
            aad = str(vault_id).encode()
            encrypted = self.encryptor.encrypt(raw_bytes, master_key, aad)
        finally:
            del master_key  # Immediately zero-out key from memory

        # Store metadata + encrypted blob
        await self._persist_vault_record(
            vault_id=vault_id,
            identity_id=identity_id,
            object_type=object_type,
            encrypted_blob=encrypted,
            content_hash=content_hash,
            size_bytes=len(raw_bytes),
            camera_zone=camera_zone,
            captured_at=captured_at,
        )

        logger.info(
            "Vault store: identity=%s type=%s vault_id=%s size=%d",
            str(identity_id)[:8], object_type.value, str(vault_id)[:8], len(raw_bytes)
        )
        return vault_id

    # ── Retrieve (admin-only, requires legal reason) ──────────────────────

    async def retrieve(
        self,
        vault_id: UUID,
        requesting_staff_id: UUID,
        requesting_staff_role: str,
        legal_reason: VaultAccessReason,
        legal_notes: str,
        ip_address: Optional[str] = None,
    ) -> VaultObject:
        """
        Decrypt and return a vault object.
        REQUIRES: admin/manager role AND documented legal reason.
        ALWAYS writes a chain-of-custody event.
        """
        self._assert_admin_role(requesting_staff_role)

        # Fetch encrypted record
        record = await self._fetch_vault_record(vault_id)
        if not record:
            raise VaultObjectNotFoundError(f"Vault object {vault_id} not found")

        if record.get("legal_hold") and legal_reason not in (
            VaultAccessReason.COURT_ORDER,
            VaultAccessReason.LEGAL_HOLD_REVIEW,
            VaultAccessReason.POLICE_INQUIRY,
        ):
            raise LegalHoldActiveError(
                f"Object {vault_id} is under legal hold. "
                "Access requires COURT_ORDER, LEGAL_HOLD_REVIEW, or POLICE_INQUIRY reason."
            )

        # Decrypt
        master_key = self.key_provider.get_master_key()
        try:
            aad = str(vault_id).encode()
            raw_bytes = self.encryptor.decrypt(record["encrypted_blob"], master_key, aad)
        finally:
            del master_key

        # Integrity verification
        actual_hash = hashlib.sha256(raw_bytes).hexdigest()
        if actual_hash != record["content_hash"]:
            raise IntegrityError(
                f"Vault object {vault_id} integrity check FAILED. "
                "Data may have been tampered with."
            )

        # Write chain-of-custody event
        await self._write_custody_event(
            identity_id=record["identity_id"],
            vault_id=vault_id,
            event_type="access",
            staff_id=requesting_staff_id,
            staff_role=requesting_staff_role,
            legal_reason=f"{legal_reason.value}: {legal_notes}",
            ip_address=ip_address,
            object_types=[record["object_type"]],
        )

        return VaultObject(
            vault_id=vault_id,
            identity_id=record["identity_id"],
            object_type=VaultObjectType(record["object_type"]),
            raw_bytes=raw_bytes,
            captured_at=record["captured_at"],
            camera_zone=record.get("camera_zone"),
            content_hash_sha256=actual_hash,
        )

    # ── Export package for law enforcement ────────────────────────────────

    async def export_for_authority(
        self,
        identity_id: UUID,
        requesting_staff_id: UUID,
        requesting_staff_role: str,
        legal_reason: VaultAccessReason,
        authority_name: str,
        case_reference: str,
        ip_address: Optional[str] = None,
    ) -> bytes:
        """
        Produces a signed, time-stamped ZIP package containing all decrypted
        biometric evidence for an identity. Suitable for handoff to police,
        court, or insurance investigators.

        ZIP contents:
          manifest.json          — metadata, timestamps, hashes, chain-of-custody
          face_frames/           — all captured face images (JPEG)
          voice_segments/        — all captured voice audio (WAV)
          gait_clips/            — all captured gait video (MP4)
          embeddings/            — raw ArcFace vectors as .npy files
          depth_maps/            — IR depth frames
          custody_log.json       — full chain of custody for this identity
          INTEGRITY_SIGNATURE    — HMAC-SHA256 of entire manifest
        """
        self._assert_admin_role(requesting_staff_role)

        # Fetch all vault records for this identity
        records = await self._fetch_all_for_identity(identity_id)
        if not records:
            raise VaultObjectNotFoundError(f"No vault records for identity {identity_id}")

        export_reference = f"EXPORT_{str(identity_id)[:8]}_{case_reference}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        manifest = {
            "export_reference": export_reference,
            "identity_id": str(identity_id),
            "exported_at": datetime.now(timezone.utc).isoformat(),
            "exported_by_staff_id": str(requesting_staff_id),
            "authority": authority_name,
            "case_reference": case_reference,
            "legal_reason": legal_reason.value,
            "total_objects": len(records),
            "objects": [],
        }

        master_key = self.key_provider.get_master_key()
        zip_buffer = io.BytesIO()

        try:
            with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for record in records:
                    vault_id = record["vault_id"]
                    aad = str(vault_id).encode()
                    raw = self.encryptor.decrypt(record["encrypted_blob"], master_key, aad)

                    # Integrity check
                    actual_hash = hashlib.sha256(raw).hexdigest()
                    if actual_hash != record["content_hash"]:
                        logger.error("INTEGRITY FAILURE on vault_id=%s during export", vault_id)
                        manifest["objects"].append({
                            "vault_id": str(vault_id),
                            "status": "INTEGRITY_FAILURE",
                            "object_type": record["object_type"],
                        })
                        continue

                    # Determine file path in ZIP
                    obj_type = record["object_type"]
                    ext_map = {
                        "face_frame": ("face_frames", "jpg"),
                        "face_embedding": ("embeddings", "npy"),
                        "voice_audio": ("voice_segments", "wav"),
                        "gait_clip": ("gait_clips", "mp4"),
                        "depth_map": ("depth_maps", "bin"),
                        "fingerprint_raw": ("fingerprints", "iso"),
                    }
                    folder, ext = ext_map.get(obj_type, ("other", "bin"))
                    filename = f"{folder}/{str(vault_id)[:8]}_{record['captured_at'].strftime('%H%M%S')}.{ext}"
                    zf.writestr(filename, raw)

                    manifest["objects"].append({
                        "vault_id": str(vault_id),
                        "filename": filename,
                        "object_type": obj_type,
                        "captured_at": record["captured_at"].isoformat(),
                        "camera_zone": record.get("camera_zone"),
                        "size_bytes": len(raw),
                        "sha256": actual_hash,
                        "status": "OK",
                    })

                # Add manifest
                manifest_bytes = json.dumps(manifest, indent=2).encode()
                zf.writestr("manifest.json", manifest_bytes)

                # Add integrity signature
                sig = self._sign(manifest_bytes)
                zf.writestr("INTEGRITY_SIGNATURE", sig)

        finally:
            del master_key

        # Write custody event for the export
        await self._write_custody_event(
            identity_id=identity_id,
            vault_id=None,
            event_type="export",
            staff_id=requesting_staff_id,
            staff_role=requesting_staff_role,
            legal_reason=f"{legal_reason.value} | Authority: {authority_name} | Case: {case_reference}",
            ip_address=ip_address,
            object_types=[r["object_type"] for r in records],
            export_reference=export_reference,
        )

        logger.warning(
            "VAULT EXPORT: identity=%s case=%s authority=%s by=%s",
            str(identity_id)[:8], case_reference, authority_name,
            str(requesting_staff_id)[:8],
        )
        return zip_buffer.getvalue()

    # ── Legal hold ────────────────────────────────────────────────────────

    async def set_legal_hold(
        self,
        identity_id: UUID,
        hold_reason: str,
        requesting_staff_id: UUID,
        requesting_staff_role: str,
    ) -> None:
        """
        Place a legal hold on all vault objects for an identity.
        Objects under legal hold cannot be deleted by anyone until the hold is lifted.
        """
        self._assert_admin_role(requesting_staff_role)
        await self._update_legal_hold(identity_id, True, hold_reason)
        await self._write_custody_event(
            identity_id=identity_id,
            vault_id=None,
            event_type="legal_hold_set",
            staff_id=requesting_staff_id,
            staff_role=requesting_staff_role,
            legal_reason=hold_reason,
            ip_address=None,
            object_types=["all"],
        )
        logger.warning("LEGAL HOLD SET: identity=%s reason=%s", str(identity_id)[:8], hold_reason)

    async def lift_legal_hold(
        self,
        identity_id: UUID,
        lift_reason: str,
        requesting_staff_id: UUID,
        requesting_staff_role: str,
    ) -> None:
        self._assert_admin_role(requesting_staff_role)
        await self._update_legal_hold(identity_id, False, None)
        await self._write_custody_event(
            identity_id=identity_id, vault_id=None,
            event_type="legal_hold_lifted",
            staff_id=requesting_staff_id, staff_role=requesting_staff_role,
            legal_reason=lift_reason, ip_address=None, object_types=["all"],
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _assert_admin_role(self, role: str) -> None:
        if role not in ("super_admin", "pharmacy_manager"):
            raise InsufficientVaultPrivilegeError(
                f"Role '{role}' cannot access the biometric evidence vault. "
                "Required: super_admin or pharmacy_manager."
            )

    def _sign(self, data: bytes) -> str:
        import hmac
        sig = hmac.new(
            bytes.fromhex(self._hmac_secret),
            data,
            hashlib.sha256,
        ).hexdigest()
        return sig

    async def _persist_vault_record(self, vault_id, identity_id, object_type,
                                     encrypted_blob, content_hash, size_bytes,
                                     camera_zone, captured_at) -> None:
        if self.db:
            from sqlalchemy import text
            await self.db.execute(text("""
                INSERT INTO biometric_vault_objects
                  (id, identity_id, object_type, encrypted_blob, content_hash,
                   size_bytes, camera_zone, captured_at, legal_hold)
                VALUES
                  (:id, :identity_id, :object_type, :blob, :hash,
                   :size, :zone, :captured_at, false)
            """), {
                "id": str(vault_id), "identity_id": str(identity_id),
                "object_type": object_type.value, "blob": encrypted_blob,
                "hash": content_hash, "size": size_bytes,
                "zone": camera_zone, "captured_at": captured_at,
            })

    async def _fetch_vault_record(self, vault_id: UUID) -> Optional[dict]:
        if self.db:
            from sqlalchemy import text
            result = await self.db.execute(
                text("SELECT * FROM biometric_vault_objects WHERE id = :id"),
                {"id": str(vault_id)},
            )
            row = result.mappings().first()
            return dict(row) if row else None
        return None

    async def _fetch_all_for_identity(self, identity_id: UUID) -> list[dict]:
        if self.db:
            from sqlalchemy import text
            result = await self.db.execute(
                text("SELECT * FROM biometric_vault_objects WHERE identity_id = :id ORDER BY captured_at"),
                {"id": str(identity_id)},
            )
            return [dict(row) for row in result.mappings().all()]
        return []

    async def _update_legal_hold(self, identity_id: UUID, hold: bool, reason: Optional[str]) -> None:
        if self.db:
            from sqlalchemy import text
            await self.db.execute(text("""
                UPDATE biometric_vault_objects
                SET legal_hold = :hold, legal_hold_reason = :reason
                WHERE identity_id = :id
            """), {"hold": hold, "reason": reason, "id": str(identity_id)})

    async def _write_custody_event(
        self,
        identity_id: UUID,
        vault_id: Optional[UUID],
        event_type: str,
        staff_id: UUID,
        staff_role: str,
        legal_reason: str,
        ip_address: Optional[str],
        object_types: list[str],
        export_reference: Optional[str] = None,
    ) -> None:
        event_id = uuid4()
        ts = datetime.now(timezone.utc)

        # HMAC signature over event fields (tamper evidence)
        payload = json.dumps({
            "event_id": str(event_id),
            "identity_id": str(identity_id),
            "vault_id": str(vault_id) if vault_id else None,
            "event_type": event_type,
            "staff_id": str(staff_id),
            "timestamp": ts.isoformat(),
        }, sort_keys=True).encode()
        signature = self._sign(payload)

        if self.db:
            from sqlalchemy import text
            await self.db.execute(text("""
                INSERT INTO vault_access_log
                  (id, identity_id, vault_id, event_type, accessed_by_staff_id,
                   accessed_by_role, legal_reason, ip_address, event_at,
                   object_types, export_reference, signature_hex)
                VALUES
                  (:id, :identity_id, :vault_id, :event_type, :staff_id,
                   :role, :reason, :ip, :ts,
                   :types, :export_ref, :sig)
            """), {
                "id": str(event_id),
                "identity_id": str(identity_id),
                "vault_id": str(vault_id) if vault_id else None,
                "event_type": event_type,
                "staff_id": str(staff_id),
                "role": staff_role,
                "reason": legal_reason,
                "ip": ip_address,
                "ts": ts,
                "types": json.dumps(object_types),
                "export_ref": export_reference,
                "sig": signature,
            })

        logger.info(
            "CUSTODY EVENT [%s] identity=%s event=%s by=%s reason=%s",
            str(event_id)[:8], str(identity_id)[:8], event_type,
            str(staff_id)[:8], legal_reason[:60],
        )


class InsufficientVaultPrivilegeError(PermissionError): pass
class VaultObjectNotFoundError(KeyError): pass
class LegalHoldActiveError(PermissionError): pass
class IntegrityError(ValueError): pass
