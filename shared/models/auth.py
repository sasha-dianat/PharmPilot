from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import AuditedBase, TimestampedBase


class StaffRole(str, Enum):
    PHARMACIST = "pharmacist"
    PHARMACY_TECHNICIAN = "pharmacy_technician"
    PHARMACY_INTERN = "pharmacy_intern"
    CASHIER = "cashier"
    PHARMACY_MANAGER = "pharmacy_manager"
    INVENTORY_STAFF = "inventory_staff"
    SUPER_ADMIN = "super_admin"


# Permission set per role
ROLE_PERMISSIONS: dict[StaffRole, list[str]] = {
    StaffRole.SUPER_ADMIN: ["*"],
    StaffRole.PHARMACY_MANAGER: [
        "rx:read", "rx:write", "rx:verify", "rx:dispense",
        "patient:read", "patient:write",
        "inventory:read", "inventory:write", "inventory:order",
        "inventory:approve",
        # Setting a shelf price is an OWNER act, not a stocking act. Inventory
        # staff receive goods and record what they cost; what the customer is
        # charged is a commercial decision, and the same maker-checker logic
        # that keeps a requester from approving their own write-off keeps a
        # receiver from repricing the shelf.
        "inventory:price",
        # The owner administers the biometric gallery: enrolment, retirement and
        # calibration. Read alone cannot change who the system can recognise.
        "biometric:write",
        "claims:read", "claims:submit",
        "reports:read", "staff:read", "staff:write",
        "biometric:read", "audio:read",
    ],
    StaffRole.PHARMACIST: [
        "rx:read", "rx:write", "rx:verify", "rx:dispense",
        "rx:override_dur", "rx:controlled_substance",
        "patient:read", "patient:write",
        "claims:read", "claims:submit",
        # A pharmacist approves write-offs but does not request them: the
        # maker-checker split only works if the two sets of people differ.
        "inventory:read", "inventory:approve", "reports:read",
        "biometric:read", "audio:read",
        "clinical:read", "clinical:write",
    ],
    StaffRole.PHARMACY_INTERN: [
        "rx:read", "rx:write",
        "patient:read", "patient:write",
        "claims:read",
        "inventory:read",
    ],
    StaffRole.PHARMACY_TECHNICIAN: [
        "rx:read", "rx:write",
        "patient:read",
        "claims:read", "claims:submit",
        "inventory:read",
    ],
    StaffRole.CASHIER: [
        "rx:read",
        "patient:read",
        "claims:read",
    ],
    StaffRole.INVENTORY_STAFF: [
        # Deliberately NOT inventory:approve — inventory staff request stock
        # write-offs, and a requester who can approve their own write-off is
        # the control failing silently.
        "inventory:read", "inventory:write", "inventory:order",
        "rx:read",
    ],
}


class Staff(AuditedBase):
    __tablename__ = "staff"

    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True
    )

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    role: Mapped[StaffRole] = mapped_column(String(50), nullable=False)

    # Professional credentials
    pharmacist_license_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    pharmacist_license_state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    npi: Mapped[str | None] = mapped_column(String(10), nullable=True)
    dea_number: Mapped[str | None] = mapped_column(String(15), nullable=True)

    # EPCS — biometric 2FA for controlled substance prescribing/dispensing
    epcs_enrolled: Mapped[bool] = mapped_column(Boolean, default=False)
    epcs_identity_proofed: Mapped[bool] = mapped_column(Boolean, default=False)
    epcs_biometric_identity_id: Mapped[UUID | None] = mapped_column(nullable=True)
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)  # encrypted

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_login_attempts: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Custom permission overrides (additive to role defaults)
    extra_permissions: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)

    sessions: Mapped[list["StaffSession"]] = relationship(back_populates="staff")

    def has_permission(self, permission: str) -> bool:
        role_perms = ROLE_PERMISSIONS.get(self.role, [])
        if "*" in role_perms:
            return True
        if permission in self.extra_permissions:
            return True
        # Wildcard match: "rx:*" grants "rx:read", "rx:write", etc.
        perm_prefix = permission.split(":")[0] + ":*"
        return permission in role_perms or perm_prefix in role_perms


class StaffSession(TimestampedBase):
    __tablename__ = "staff_sessions"

    staff_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff.id"), nullable=False, index=True
    )
    token_jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    workstation_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    staff: Mapped["Staff"] = relationship(back_populates="sessions")
