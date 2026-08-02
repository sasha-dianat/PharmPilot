"""
Prescription State Machine — the heart of the pharmacy workflow.

Enforces valid transitions, records every state change as an immutable
event with SHA-256 hash chaining for tamper evidence, and manages
concurrent workstation queue ownership via Redis lease tokens.
"""
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.prescription import Prescription, RxStateEvent, RxStatus

logger = logging.getLogger(__name__)

# ── Valid state transitions ───────────────────────────────────────────────────
TRANSITIONS: dict[RxStatus, list[RxStatus]] = {
    RxStatus.INTAKE: [
        RxStatus.PENDING_DUR,
        RxStatus.DUR_HOLD,
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.PENDING_DUR: [
        RxStatus.PENDING_VERIFICATION,
        RxStatus.DUR_HOLD,
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.DUR_HOLD: [
        RxStatus.PENDING_VERIFICATION,
        RxStatus.CANCELLED,
    ],
    RxStatus.PENDING_VERIFICATION: [
        RxStatus.VERIFICATION_IN_PROGRESS,
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.VERIFICATION_IN_PROGRESS: [
        RxStatus.PENDING_ADJUDICATION,
        RxStatus.DUR_HOLD,
        RxStatus.PENDING_VERIFICATION,   # Released back to queue
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.PENDING_ADJUDICATION: [
        RxStatus.ADJUDICATION_REJECTED,
        RxStatus.PENDING_PA,
        RxStatus.READY_TO_FILL,
        RxStatus.ON_HOLD,
    ],
    RxStatus.ADJUDICATION_REJECTED: [
        RxStatus.PENDING_ADJUDICATION,   # Rebill
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.PENDING_PA: [
        RxStatus.PENDING_ADJUDICATION,   # PA received — rebill
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.READY_TO_FILL: [
        RxStatus.FILLING,
        RxStatus.ON_HOLD,
        RxStatus.CANCELLED,
    ],
    RxStatus.FILLING: [
        RxStatus.FILLED,
        RxStatus.READY_TO_FILL,          # Filling abandoned
        RxStatus.ON_HOLD,
    ],
    RxStatus.FILLED: [
        RxStatus.WILL_CALL,
        RxStatus.DISPENSED,
        RxStatus.RETURNED_TO_STOCK,
    ],
    RxStatus.WILL_CALL: [
        RxStatus.DISPENSED,
        RxStatus.RETURNED_TO_STOCK,
    ],
    RxStatus.DISPENSED: [],              # Terminal state
    RxStatus.RETURNED_TO_STOCK: [],      # Terminal state
    RxStatus.CANCELLED: [],              # Terminal state
    RxStatus.TRANSFERRED_OUT: [],        # Terminal state
    RxStatus.ON_HOLD: [
        RxStatus.INTAKE,
        RxStatus.PENDING_VERIFICATION,
        RxStatus.CANCELLED,
    ],
}

# States where controlled substance EPCS check is required
EPCS_REQUIRED_STATES = {RxStatus.VERIFICATION_IN_PROGRESS, RxStatus.FILLING}

# Queue ownership lease duration
QUEUE_LEASE_SECONDS = 300  # 5 minutes — auto-released if not completed


def _compute_event_hash(
    prescription_id: UUID,
    from_status: Optional[str],
    to_status: str,
    triggered_by_id: Optional[UUID],
    timestamp: datetime,
    previous_hash: Optional[str] = None,
) -> str:
    """
    SHA-256 hash of the transition — chains with previous event hash
    for tamper evidence on the audit trail.
    """
    payload = json.dumps({
        "prescription_id": str(prescription_id),
        "from_status": from_status,
        "to_status": to_status,
        "triggered_by_id": str(triggered_by_id) if triggered_by_id else None,
        "timestamp": timestamp.isoformat(),
        "previous_hash": previous_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class RxStateMachine:
    """
    Manages all prescription state transitions.
    All transitions go through this class — no direct status updates permitted.
    """

    def __init__(self, db: AsyncSession, redis_client=None):
        self.db = db
        self.redis = redis_client  # For queue lease management

    async def transition(
        self,
        prescription_id: UUID,
        to_status: RxStatus,
        triggered_by_id: Optional[UUID] = None,
        triggered_by_type: str = "staff",
        reason: Optional[str] = None,
        metadata: Optional[dict] = None,
        check_epcs: bool = True,
    ) -> Prescription:
        """
        Transition a prescription to a new status.
        Validates the transition, records the event, and persists.
        Returns the updated prescription.
        """
        result = await self.db.execute(
            select(Prescription).where(Prescription.id == prescription_id)
        )
        rx = result.scalar_one_or_none()
        if not rx:
            raise ValueError(f"Prescription {prescription_id} not found")

        from_status = RxStatus(rx.status)
        allowed_transitions = TRANSITIONS.get(from_status, [])

        if to_status not in allowed_transitions:
            raise InvalidTransitionError(
                f"Cannot transition {from_status.value} → {to_status.value}. "
                f"Allowed: {[s.value for s in allowed_transitions]}"
            )

        # EPCS check for controlled substances entering key states
        if check_epcs and rx.is_controlled and to_status in EPCS_REQUIRED_STATES:
            if triggered_by_id and not await self._verify_epcs_enrollment(triggered_by_id):
                raise EPCSRequiredError(
                    f"EPCS enrollment required to process controlled substance "
                    f"(Schedule {rx.dea_schedule}) in state {to_status.value}"
                )

        # Get previous hash for chain
        prev_event_result = await self.db.execute(
            select(RxStateEvent)
            .where(RxStateEvent.prescription_id == prescription_id)
            .order_by(RxStateEvent.created_at.desc())
            .limit(1)
        )
        prev_event = prev_event_result.scalar_one_or_none()
        prev_hash = prev_event.event_hash if prev_event else None

        now = datetime.now(timezone.utc)
        event_hash = _compute_event_hash(
            prescription_id=prescription_id,
            from_status=from_status.value,
            to_status=to_status.value,
            triggered_by_id=triggered_by_id,
            timestamp=now,
            previous_hash=prev_hash,
        )

        # Create immutable state event
        event = RxStateEvent(
            prescription_id=prescription_id,
            from_status=from_status.value,
            to_status=to_status.value,
            triggered_by_id=triggered_by_id,
            triggered_by_type=triggered_by_type,
            reason=reason,
            event_metadata=metadata or {},
            event_hash=event_hash,
            created_by=triggered_by_id,
        )
        self.db.add(event)

        # Update prescription
        rx.status = to_status.value
        rx.updated_by = triggered_by_id

        # Handle specific transition side effects
        await self._handle_transition_effects(rx, from_status, to_status, triggered_by_id, now)

        logger.info(
            "Rx %s: %s → %s by %s [hash=%s...]",
            rx.rx_number, from_status.value, to_status.value,
            triggered_by_id, event_hash[:16],
        )

        return rx

    async def claim_for_verification(
        self,
        prescription_id: UUID,
        staff_id: UUID,
    ) -> Prescription:
        """
        Claim an Rx from the verification queue for a specific workstation/pharmacist.
        Uses Redis lease to prevent concurrent double-verification.
        """
        lease_key = f"rx:lease:{prescription_id}"

        if self.redis:
            # Try to acquire lease (SET NX EX)
            acquired = await self.redis.set(
                lease_key,
                str(staff_id),
                nx=True,
                ex=QUEUE_LEASE_SECONDS,
            )
            if not acquired:
                current_owner = await self.redis.get(lease_key)
                raise QueueOwnershipConflict(
                    f"Rx {prescription_id} is already claimed by staff {current_owner}"
                )

        rx = await self.transition(
            prescription_id=prescription_id,
            to_status=RxStatus.VERIFICATION_IN_PROGRESS,
            triggered_by_id=staff_id,
            reason="Claimed for verification",
        )
        rx.claimed_by_staff_id = staff_id
        rx.claimed_at = datetime.now(timezone.utc)
        return rx

    async def release_from_verification(
        self,
        prescription_id: UUID,
        staff_id: UUID,
        reason: str = "Released by pharmacist",
    ) -> Prescription:
        """
        Release an Rx back to the verification queue.
        Called when a pharmacist puts an Rx back without completing.
        """
        lease_key = f"rx:lease:{prescription_id}"
        if self.redis:
            await self.redis.delete(lease_key)

        rx = await self.transition(
            prescription_id=prescription_id,
            to_status=RxStatus.PENDING_VERIFICATION,
            triggered_by_id=staff_id,
            reason=reason,
        )
        rx.claimed_by_staff_id = None
        rx.claimed_at = None
        return rx

    async def auto_release_expired_leases(self) -> int:
        """
        Called by a scheduled task — releases orphaned queue leases.
        Moves VERIFICATION_IN_PROGRESS Rxs back to PENDING_VERIFICATION
        if their claimed_at is > QUEUE_LEASE_SECONDS ago.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=QUEUE_LEASE_SECONDS)
        result = await self.db.execute(
            select(Prescription).where(
                Prescription.status == RxStatus.VERIFICATION_IN_PROGRESS.value,
                Prescription.claimed_at < cutoff,
            )
        )
        expired = result.scalars().all()
        released = 0
        for rx in expired:
            await self.release_from_verification(
                prescription_id=rx.id,
                staff_id=None,
                reason="Auto-released: lease expired",
            )
            released += 1

        if released:
            logger.info("Auto-released %d expired verification leases", released)
        return released

    async def _handle_transition_effects(
        self,
        rx: Prescription,
        from_status: RxStatus,
        to_status: RxStatus,
        staff_id: Optional[UUID],
        now: datetime,
    ) -> None:
        """Side effects that accompany specific transitions."""

        if to_status == RxStatus.DISPENSED:
            rx.fill_date = now.date()
            rx.last_fill_date = now.date()
            # Decrement refills remaining
            if rx.refills_remaining > 0:
                rx.refills_remaining -= 1

            # Stock actually leaves the shelf here. This used to be a log line
            # reading "trigger inventory deduction (via Kafka event in
            # production)", which is why 46 fills existed against 8 movements
            # and on-hand only ever went up (ROADMAP:34).
            #
            # `apply_dispense` never raises: by the time this transition fires
            # the medicine is with the patient, so a shortfall or an inventory
            # failure is recorded and reported, never turned into a refusal to
            # dispense. The reconciliation report is what catches a hook that
            # did not run.
            from services.core.inventory.dispense import apply_dispense
            result = await apply_dispense(self.db, rx, staff_id=staff_id, now=now)
            if not result.ok:
                logger.error("Rx %s dispensed but stock was NOT decremented: %s",
                             rx.rx_number, result.error)
            elif result.skipped:
                logger.info("Rx %s inventory decrement skipped (%s)",
                            rx.rx_number, result.skipped)
            else:
                logger.info("Rx %s dispensed — %s units from lot(s) %s%s",
                            rx.rx_number, result.allocated,
                            ", ".join(result.lots) or "none",
                            f"; SHORT by {result.shortfall}" if result.shortfall else "")

        elif to_status == RxStatus.FILLING:
            rx.fill_date = now.date()

        elif to_status == RxStatus.RETURNED_TO_STOCK:
            # Units come back on the shelf. The original DISPENSE movements are
            # left untouched and offsetting receipts are posted, so the ledger
            # records both that the stock left and that it returned.
            from services.core.inventory.dispense import reverse_dispense
            from shared.models.prescription import PrescriptionFill
            fill = (await self.db.execute(
                select(PrescriptionFill)
                .where(PrescriptionFill.prescription_id == rx.id,
                       PrescriptionFill.is_deleted == False)  # noqa: E712
                .order_by(PrescriptionFill.fill_number.desc()))).scalars().first()
            if fill is not None:
                result = await reverse_dispense(self.db, fill.id, staff_id=staff_id,
                                                reason=f"returned to stock {rx.rx_number}")
                if not result.ok:
                    logger.error("Rx %s returned to stock but inventory was NOT "
                                 "restored: %s", rx.rx_number, result.error)
                else:
                    logger.info("Rx %s returned to stock — %s units restored",
                                rx.rx_number, result.allocated)

        elif to_status == RxStatus.WILL_CALL:
            # Record when Rx went to will-call — 14-day expiry in most states
            logger.info("Rx %s in will-call bin", rx.rx_number)

    async def _verify_epcs_enrollment(self, staff_id: UUID) -> bool:
        from shared.models.auth import Staff
        result = await self.db.execute(
            select(Staff).where(Staff.id == staff_id)
        )
        staff = result.scalar_one_or_none()
        if not staff:
            return False
        return staff.epcs_enrolled and staff.epcs_identity_proofed

    async def generate_rx_number(self, pharmacy_id: UUID) -> str:
        """
        Generate a unique Rx number for this pharmacy.
        Format: {pharmacy_ncpdp_last4}{YYYYMMDD}{sequence_6digits}
        """
        from shared.models.pharmacy import Pharmacy
        from sqlalchemy import func
        result = await self.db.execute(select(Pharmacy).where(Pharmacy.id == pharmacy_id))
        pharmacy = result.scalar_one_or_none()

        prefix = ""
        if pharmacy and pharmacy.ncpdp_id:
            prefix = pharmacy.ncpdp_id[-4:]
        else:
            prefix = str(pharmacy_id)[:4].upper()

        today = datetime.now().strftime("%Y%m%d")

        # Count Rxs created today for this pharmacy to generate sequence
        count_result = await self.db.execute(
            select(func.count(Prescription.id)).where(
                Prescription.pharmacy_id == pharmacy_id,
                Prescription.created_at >= datetime.now().replace(hour=0, minute=0, second=0),
            )
        )
        seq = (count_result.scalar() or 0) + 1

        return f"{prefix}{today}{seq:06d}"


class InvalidTransitionError(Exception):
    """Raised when an invalid state transition is attempted."""


class EPCSRequiredError(Exception):
    """Raised when EPCS enrollment is required but not present."""


class QueueOwnershipConflict(Exception):
    """Raised when trying to claim an Rx already owned by another workstation."""
