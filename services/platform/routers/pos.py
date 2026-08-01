"""
Phase 29 — POS & Cash/Copay Collection
========================================
Endpoints for collecting patient-pay at the dispensing window.

Tender types supported:
  cash    — cash with change calculation
  card    — credit/debit/HSA/FSA with terminal reference
  mobile  — QR/mobile pay reference
  waiver  — copay waiver (Medicaid, hardship, professional courtesy)
  split   — partial cash + remainder on card

End-of-day reconciliation:
  GET /pos/end-of-day?date=YYYY-MM-DD  — tallies by tender type

All payment events are appended to `payment_events` table for audit.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user
from services.core.pharmacy_workflow.state_machine import (
    InvalidTransitionError, RxStateMachine, TRANSITIONS)
from shared.models.prescription import RxStatus

router = APIRouter(tags=["pos"])

# Derived from the state machine's own table rather than restated here, so the
# two cannot drift apart. Today this is {filled, will_call}.
DISPENSABLE_FROM: frozenset[str] = frozenset(
    status.value for status, allowed in TRANSITIONS.items()
    if RxStatus.DISPENSED in allowed
)

# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class CollectPaymentRequest(BaseModel):
    rx_id:           str
    tender_type:     str                          # cash | card | mobile | waiver | split
    amount_tendered: float = Field(ge=0)
    cash_amount:     Optional[float] = 0.0
    card_ref:        Optional[str]   = None
    mobile_ref:      Optional[str]   = None
    waiver_reason:   Optional[str]   = None
    waiver_note:     Optional[str]   = None
    collected_by:    str
    receipt_mode:    Optional[str]   = "print"    # print | email | none

class CollectPaymentResponse(BaseModel):
    payment_id:      str
    rx_id:           str
    rx_number:       str
    patient_pay:     float
    amount_tendered: float
    change_due:      float
    tender_type:     str
    receipt_number:  str
    timestamp:       str
    # Payment and dispensing are separate events. Taking the money never moves
    # the medicine on its own, so the caller is told plainly what happened.
    dispensed:               bool = False
    rx_status:               str  = ""
    dispense_blocked_reason: Optional[str] = None

class EndOfDaySummary(BaseModel):
    date:            str
    period_start:    str
    period_end:      str
    total_collected: float
    cash_total:      float
    card_total:      float
    mobile_total:    float
    waiver_count:    int
    transaction_count: int
    by_tender:       dict

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _receipt_number(today: date | None = None) -> str:
    d = today or date.today()
    suffix = uuid.uuid4().hex[:6].upper()
    return f"RCP-{d.strftime('%Y%m%d')}-{suffix}"

async def _get_rx_row(db: AsyncSession, rx_id: str) -> dict:
    """Fetch rx number and patient_pay from prescriptions + adjudication tables."""
    result = await db.execute(text("""
        SELECT p.rx_number,
               p.status,
               COALESCE(a.patient_pay, 0) AS patient_pay
        FROM   prescriptions p
        LEFT JOIN (
            SELECT rx_id,
                   patient_pay
            FROM   adjudication_results
            WHERE  status = 'paid'
            ORDER  BY adjudicated_at DESC
            LIMIT  1
        ) a ON a.rx_id = p.id
        WHERE  p.id = :rx_id
    """), {"rx_id": rx_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, f"Prescription {rx_id} not found")
    return dict(row)

async def _ensure_payment_events_table(db: AsyncSession) -> None:
    """Create payment_events table if it does not exist yet (idempotent)."""
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS payment_events (
            id              TEXT PRIMARY KEY,
            rx_id           TEXT NOT NULL,
            rx_number       TEXT,
            patient_pay     NUMERIC(10,2) DEFAULT 0,
            amount_tendered NUMERIC(10,2) DEFAULT 0,
            change_due      NUMERIC(10,2) DEFAULT 0,
            tender_type     TEXT NOT NULL,
            card_ref        TEXT,
            mobile_ref      TEXT,
            waiver_reason   TEXT,
            waiver_note     TEXT,
            receipt_number  TEXT,
            collected_by    TEXT,
            receipt_mode    TEXT DEFAULT 'print',
            created_at      TIMESTAMPTZ DEFAULT now()
        )
    """))
    await db.commit()

def _staff_uuid(current: dict):
    """Acting staff from the verified session. `body.collected_by` is
    caller-supplied and must not become the actor on an audited transition."""
    raw = (current or {}).get("staff_id") or (current or {}).get("sub")
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/collect-payment", response_model=CollectPaymentResponse)
async def collect_payment(
    body:        CollectPaymentRequest,
    db:          AsyncSession = Depends(get_db),
    _current:    dict         = Depends(get_current_user),
):
    await _ensure_payment_events_table(db)

    rx     = await _get_rx_row(db, body.rx_id)
    rx_num = rx["rx_number"]

    patient_pay     = float(rx["patient_pay"])
    amount_tendered = body.amount_tendered
    change_due      = round(max(0.0, amount_tendered - patient_pay), 2)

    if body.tender_type not in ("waiver",) and amount_tendered < patient_pay:
        # Allow ±0.01 rounding tolerance
        if amount_tendered < (patient_pay - 0.01):
            raise HTTPException(400, "Amount tendered is less than patient_pay")

    receipt_num = _receipt_number()
    payment_id  = str(uuid.uuid4())
    now         = datetime.now(timezone.utc)

    await db.execute(text("""
        INSERT INTO payment_events
            (id, rx_id, rx_number, patient_pay, amount_tendered, change_due,
             tender_type, card_ref, mobile_ref, waiver_reason, waiver_note,
             receipt_number, collected_by, receipt_mode, created_at)
        VALUES
            (:id, :rx_id, :rx_number, :patient_pay, :amount_tendered, :change_due,
             :tender_type, :card_ref, :mobile_ref, :waiver_reason, :waiver_note,
             :receipt_number, :collected_by, :receipt_mode, :now)
    """), dict(
        id=payment_id, rx_id=body.rx_id, rx_number=rx_num,
        patient_pay=patient_pay, amount_tendered=amount_tendered,
        change_due=change_due, tender_type=body.tender_type,
        card_ref=body.card_ref, mobile_ref=body.mobile_ref,
        waiver_reason=body.waiver_reason, waiver_note=body.waiver_note,
        receipt_number=receipt_num, collected_by=body.collected_by,
        receipt_mode=body.receipt_mode, now=now,
    ))

    # The payment is a financial fact and is committed on its own. Paying for a
    # prescription that is still being filled is ordinary practice, so the money
    # is always recorded — what is withheld is the medicine, not the receipt.
    await db.commit()

    # Dispensing is a SEPARATE event and goes through RxStateMachine, which
    # validates the transition, hash-chains an RxStateEvent and applies the EPCS
    # rules. This endpoint previously wrote status='dispensed' by raw SQL from
    # any status except cancelled/voided — including DUR_HOLD, whose whole
    # purpose is to stop a dispense. Collecting payment dispensed the medicine.
    current_status = str(rx["status"])
    dispensed = False
    blocked_reason: Optional[str] = None

    if current_status in DISPENSABLE_FROM:
        try:
            updated = await RxStateMachine(db).transition(
                prescription_id=uuid.UUID(body.rx_id),
                to_status=RxStatus.DISPENSED,
                triggered_by_id=_staff_uuid(_current),
                triggered_by_type="staff",
                reason=f"POS payment {receipt_num} ({body.tender_type})",
                metadata={"payment_id": payment_id,
                          "receipt_number": receipt_num,
                          "tender_type": body.tender_type},
            )
            await db.commit()
            dispensed = True
            current_status = str(updated.status)
        except InvalidTransitionError as exc:
            await db.rollback()
            blocked_reason = str(exc)
        except Exception as exc:            # EPCS, missing Rx, anything else
            await db.rollback()
            blocked_reason = f"{type(exc).__name__}: {exc}"
    else:
        blocked_reason = (
            f"Prescription is '{current_status}'. Dispensing is only permitted "
            f"from {sorted(DISPENSABLE_FROM)}. Payment has been recorded; the "
            f"prescription must complete its workflow before it is handed over."
        )

    return CollectPaymentResponse(
        payment_id=payment_id, rx_id=body.rx_id, rx_number=rx_num,
        patient_pay=patient_pay, amount_tendered=amount_tendered,
        change_due=change_due, tender_type=body.tender_type,
        receipt_number=receipt_num, timestamp=now.isoformat(),
        dispensed=dispensed, rx_status=current_status,
        dispense_blocked_reason=blocked_reason,
    )


@router.get("/end-of-day", response_model=EndOfDaySummary)
async def end_of_day(
    target_date: Optional[str] = Query(None, description="YYYY-MM-DD; defaults to today"),
    db:          AsyncSession   = Depends(get_db),
    _current:    dict           = Depends(get_current_user),
):
    await _ensure_payment_events_table(db)

    d     = date.fromisoformat(target_date) if target_date else date.today()
    start = f"{d.isoformat()}T00:00:00+00:00"
    end   = f"{d.isoformat()}T23:59:59+00:00"

    rows = await db.execute(text("""
        SELECT tender_type,
               COUNT(*)                         AS cnt,
               COALESCE(SUM(patient_pay), 0)    AS collected
        FROM   payment_events
        WHERE  created_at >= :start
          AND  created_at <= :end
        GROUP  BY tender_type
    """), {"start": start, "end": end})

    by_tender: dict = {}
    total = cash = card = mobile = 0.0
    waiver_count = 0

    for row in rows.mappings():
        t   = row["tender_type"]
        amt = float(row["collected"])
        cnt = int(row["cnt"])
        by_tender[t] = {"count": cnt, "total": amt}
        total += amt
        if t == "cash":    cash   += amt
        if t in ("card", "split"): card += amt
        if t == "mobile":  mobile += amt
        if t == "waiver":  waiver_count += cnt

    return EndOfDaySummary(
        date=d.isoformat(),
        period_start=start, period_end=end,
        total_collected=round(total, 2),
        cash_total=round(cash, 2),
        card_total=round(card, 2),
        mobile_total=round(mobile, 2),
        waiver_count=waiver_count,
        transaction_count=sum(v["count"] for v in by_tender.values()),
        by_tender=by_tender,
    )


@router.get("/receipt/{payment_id}")
async def get_receipt(
    payment_id: str,
    db:         AsyncSession = Depends(get_db),
    _current:   dict         = Depends(get_current_user),
):
    await _ensure_payment_events_table(db)

    result = await db.execute(text("""
        SELECT * FROM payment_events WHERE id = :pid
    """), {"pid": payment_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, "Receipt not found")
    return dict(row)
