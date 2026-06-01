from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import AuditedBase


class Pharmacy(AuditedBase):
    __tablename__ = "pharmacies"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    npi: Mapped[str | None] = mapped_column(String(10), unique=True, nullable=True, index=True)
    ncpdp_id: Mapped[str | None] = mapped_column(String(10), unique=True, nullable=True)
    dea_number: Mapped[str | None] = mapped_column(String(15), nullable=True)
    nabp_number: Mapped[str | None] = mapped_column(String(10), nullable=True)

    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(100), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(20), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    timezone: Mapped[str] = mapped_column(String(50), default="America/New_York")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Operational config (feature flags, state-specific settings)
    config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    # e.g. {"pdmp_required_schedules": ["CII","CIII"], "epcs_enabled": true,
    #        "compounding_enabled": false, "state": "TX"}
