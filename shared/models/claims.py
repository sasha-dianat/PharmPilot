from datetime import date, datetime
from uuid import UUID

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import AuditedBase, TimestampedBase


class ClaimTransaction(AuditedBase):
    """One record per adjudication attempt — never overwritten, append-only."""
    __tablename__ = "claim_transactions"

    fill_id: Mapped[UUID] = mapped_column(
        ForeignKey("prescription_fills.id"), nullable=False, index=True
    )
    pharmacy_id: Mapped[UUID] = mapped_column(
        ForeignKey("pharmacies.id"), nullable=False, index=True
    )
    patient_insurance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("patient_insurances.id"), nullable=True
    )

    # Submission fields
    sequence_number: Mapped[int] = mapped_column(Integer, default=1)
    # 1=new, 2=rebill, 3+ =subsequent attempts
    transaction_type: Mapped[str] = mapped_column(String(2), default="B1")
    # B1=billing, B3=reversal

    # NCPDP D.0 key fields
    bin_number: Mapped[str] = mapped_column(String(6), nullable=False)
    pcn: Mapped[str | None] = mapped_column(String(10), nullable=True)
    group_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    member_id: Mapped[str] = mapped_column(String(50), nullable=False)
    person_code: Mapped[str] = mapped_column(String(3), default="01")

    ndc: Mapped[str] = mapped_column(String(11), nullable=False)
    quantity: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False)
    days_supply: Mapped[int] = mapped_column(Integer, nullable=False)
    daw_code: Mapped[str] = mapped_column(String(1), default="0")
    date_of_service: Mapped[date] = mapped_column(Date, nullable=False)
    submission_clarification_code: Mapped[str | None] = mapped_column(String(2), nullable=True)
    prior_auth_number: Mapped[str | None] = mapped_column(String(20), nullable=True)
    prior_auth_type_code: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Pricing submitted
    ingredient_cost_submitted: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    dispensing_fee_submitted: Mapped[float] = mapped_column(Numeric(8, 2), default=0.0)
    usual_and_customary: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    # Response fields
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending, approved, rejected, reversed, reversal_approved

    response_status: Mapped[str | None] = mapped_column(String(1), nullable=True)
    # A=approved, R=rejected, P=duplicate
    reject_codes: Mapped[list | None] = mapped_column(JSONB, nullable=True)  # ["75","65"]
    reject_messages: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # Approved pricing
    ingredient_cost_paid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    dispensing_fee_paid: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    total_amount_paid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    patient_pay_amount: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    sales_tax_paid: Mapped[float | None] = mapped_column(Numeric(8, 2), nullable=True)
    basis_of_reimbursement: Mapped[str | None] = mapped_column(String(2), nullable=True)

    # Timing
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Raw NCPDP transaction (for audit/replay)
    raw_request: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)

    # COB fields
    is_cob: Mapped[bool] = mapped_column(Boolean, default=False)
    cob_priority: Mapped[int] = mapped_column(Integer, default=1)
    other_payer_amount_paid: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)

    fill: Mapped["PrescriptionFill"] = relationship()


class ERA835Record(AuditedBase):
    """Electronic Remittance Advice — posted payments from PBMs."""
    __tablename__ = "era835_records"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    check_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    check_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    payment_amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    claim_count: Mapped[int] = mapped_column(Integer, default=0)
    raw_835: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed: Mapped[bool] = mapped_column(Boolean, default=False)
    line_items: Mapped[list | None] = mapped_column(JSONB, nullable=True)


class DIRFeeAdjustment(TimestampedBase):
    """Post-adjudication DIR fee clawbacks from PBMs (via 835 or separate file)."""
    __tablename__ = "dir_fee_adjustments"

    pharmacy_id: Mapped[UUID] = mapped_column(ForeignKey("pharmacies.id"), nullable=False, index=True)
    claim_transaction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("claim_transactions.id"), nullable=True, index=True
    )

    adjustment_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # performance_based, network_fee, admin_fee, quality_bonus
    adjustment_amount: Mapped[float] = mapped_column(Numeric(12, 2), nullable=False)
    # Negative = clawback, Positive = bonus
    performance_period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    performance_period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    payer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    adjustment_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    posted_date: Mapped[date] = mapped_column(Date, nullable=False)
    era_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("era835_records.id"), nullable=True
    )
