from datetime import date
from uuid import UUID

from sqlalchemy import Boolean, Date, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import AuditedBase


class InsurancePlan(AuditedBase):
    __tablename__ = "insurance_plans"

    bin_number: Mapped[str] = mapped_column(String(6), nullable=False, index=True)
    pcn: Mapped[str | None] = mapped_column(String(10), nullable=True)
    plan_name: Mapped[str] = mapped_column(String(255), nullable=False)
    payer_name: Mapped[str] = mapped_column(String(255), nullable=False)
    plan_type: Mapped[str] = mapped_column(String(30), default="commercial")
    # commercial, medicare_part_d, medicaid, tricare, workers_comp, cash

    processor_control_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    switch_id: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # change_healthcare, emdeon, argus, relay_health

    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)


class PatientInsurance(AuditedBase):
    __tablename__ = "patient_insurances"

    patient_id: Mapped[UUID] = mapped_column(
        ForeignKey("patients.id"), nullable=False, index=True
    )
    insurance_plan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("insurance_plans.id"), nullable=True
    )

    # NCPDP fields
    bin_number: Mapped[str] = mapped_column(String(6), nullable=False)
    pcn: Mapped[str | None] = mapped_column(String(10), nullable=True)
    group_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    member_id: Mapped[str] = mapped_column(String(50), nullable=False)
    person_code: Mapped[str] = mapped_column(String(3), default="01")
    # 01=cardholder, 02=spouse, 03+=dependent

    cardholder_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cardholder_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    effective_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    priority: Mapped[int] = mapped_column(Integer, default=1)  # 1=primary, 2=secondary, 3=tertiary
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # COB — stores primary EOB for secondary submission
    last_primary_eob: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    patient: Mapped["Patient"] = relationship()
    plan: Mapped["InsurancePlan | None"] = relationship()
