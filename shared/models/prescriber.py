from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import AuditedBase


class Prescriber(AuditedBase):
    __tablename__ = "prescribers"

    npi: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    dea_number: Mapped[str | None] = mapped_column(String(15), nullable=True, index=True)
    dea_schedule_auth: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # e.g. "II,III,IV,V" — schedules this prescriber is authorized to prescribe

    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    suffix: Mapped[str | None] = mapped_column(String(20), nullable=True)
    specialty: Mapped[str | None] = mapped_column(String(100), nullable=True)
    specialty_code: Mapped[str | None] = mapped_column(String(10), nullable=True)

    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    zip_code: Mapped[str | None] = mapped_column(String(10), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fax: Mapped[str | None] = mapped_column(String(20), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    npi_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    dea_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    dea_check_digit_valid: Mapped[bool] = mapped_column(Boolean, default=False)

    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, nullable=False)
