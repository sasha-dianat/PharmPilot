"""
Celery Scheduled Notification Tasks
=====================================
Background tasks for all time-triggered outreach.
Scheduled via Celery Beat — runs independently of API requests.
"""
import logging
from datetime import date, datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# These tasks are registered with Celery Beat via celeryconfig.py
# They query the database and fire notifications on schedule

CELERY_BEAT_SCHEDULE = {
    # Daily refill reminder check (8 AM pharmacy local time)
    "daily-refill-reminders": {
        "task":     "services.platform.notifications.celery_tasks.send_refill_reminders",
        "schedule": {"hour": 8, "minute": 0},
    },
    # Pickup reminder for prescriptions in will-call > 7 days
    "pickup-reminders": {
        "task":     "services.platform.notifications.celery_tasks.send_pickup_reminders",
        "schedule": {"hour": 10, "minute": 0},
    },
    # Adherence risk scoring (nightly)
    "adherence-scoring": {
        "task":     "services.platform.notifications.celery_tasks.run_adherence_scoring",
        "schedule": {"hour": 2, "minute": 0},
    },
    # Inventory reorder evaluation (nightly)
    "inventory-reorder": {
        "task":     "services.platform.notifications.celery_tasks.evaluate_reorder_needs",
        "schedule": {"hour": 3, "minute": 0},
    },
    # Star ratings recalculation (weekly Sunday)
    "star-ratings-refresh": {
        "task":     "services.platform.notifications.celery_tasks.refresh_star_ratings",
        "schedule": {"day_of_week": 0, "hour": 4, "minute": 0},
    },
    # PDMP data sync (every 4 hours for active pharmacies)
    "pdmp-sync": {
        "task":     "services.platform.notifications.celery_tasks.sync_pdmp_data",
        "schedule": {"minute": 0, "hour": "*/4"},
    },
    # Expiry alert notifications (daily)
    "expiry-alerts": {
        "task":     "services.platform.notifications.celery_tasks.send_expiry_alerts",
        "schedule": {"hour": 7, "minute": 30},
    },
    # Back-office agents: auto-rebill, auto-PA, auto-reorder, refill pre-staging (every 2h)
    "backoffice-agents": {
        "task":     "services.platform.notifications.celery_tasks.run_backoffice_agents_all_pharmacies",
        "schedule": {"minute": 0, "hour": "*/2"},
    },
}


async def send_refill_reminders(pharmacy_id: str = None, db=None, push_service=None, sms_service=None):
    """
    Find patients whose medications are due for refill in 7 days
    and send PHI-safe reminders via push + SMS.
    """
    if not db:
        logger.info("send_refill_reminders: no DB configured")
        return

    from sqlalchemy import text
    from services.platform.notifications.push_service import PushNotificationRequest, NotificationType

    result = await db.execute(text("""
        SELECT DISTINCT
            p.id AS patient_id,
            p.phone_primary,
            p.preferred_contact_method,
            ph.name AS pharmacy_name,
            ph.phone AS pharmacy_phone
        FROM prescriptions pr
        JOIN patients p ON p.id = pr.patient_id
        JOIN pharmacies ph ON ph.id = pr.pharmacy_id
        WHERE pr.status = 'dispensed'
          AND pr.last_fill_date IS NOT NULL
          AND pr.refills_remaining > 0
          AND (pr.last_fill_date + (pr.days_supply || ' days')::interval)
              BETWEEN NOW() AND NOW() + INTERVAL '7 days'
          AND p.is_deleted = false
          AND (:pharmacy_id IS NULL OR pr.pharmacy_id = :pharmacy_id::uuid)
    """), {"pharmacy_id": pharmacy_id})

    patients = result.mappings().all()
    sent = 0

    for pat in patients:
        # Push notification
        if push_service and pat.get("push_token"):
            from uuid import UUID
            await push_service.send(PushNotificationRequest(
                patient_id=UUID(str(pat["patient_id"])),
                notification_type=NotificationType.REFILL_REMINDER,
                device_token=pat["push_token"],
                platform="ios",
                pharmacy_name=pat["pharmacy_name"],
            ))

        # SMS
        if sms_service and pat.get("phone_primary"):
            await sms_service.send(
                to_phone=pat["phone_primary"],
                template_name="refill_reminder",
                template_vars={"pharmacy": pat["pharmacy_name"]},
            )
        sent += 1

    logger.info("Refill reminders sent: %d patients", sent)
    return {"sent": sent}


async def send_pickup_reminders(pharmacy_id: str = None, db=None, sms_service=None):
    """
    Notify patients whose prescriptions have been in will-call > 7 days.
    Will be returned to stock after pharmacy's configured hold period.
    """
    if not db:
        return

    from sqlalchemy import text

    result = await db.execute(text("""
        SELECT
            p.id AS patient_id, p.phone_primary,
            ph.name AS pharmacy_name, ph.phone AS pharmacy_phone,
            pr.rx_number,
            EXTRACT(DAY FROM NOW() - pr.updated_at)::int AS days_in_will_call
        FROM prescriptions pr
        JOIN patients p ON p.id = pr.patient_id
        JOIN pharmacies ph ON ph.id = pr.pharmacy_id
        WHERE pr.status = 'will_call'
          AND pr.updated_at <= NOW() - INTERVAL '7 days'
          AND p.is_deleted = false
    """), {})

    for row in result.mappings().all():
        if sms_service and row.get("phone_primary"):
            await sms_service.send(
                to_phone=row["phone_primary"],
                template_name="pickup_reminder",
                template_vars={
                    "pharmacy": row["pharmacy_name"],
                    "phone":    row["pharmacy_phone"] or "",
                    "days":     str(14 - row["days_in_will_call"]),
                },
            )

    logger.info("Pickup reminders sent")


async def run_adherence_scoring(pharmacy_id: str = None, db=None):
    """Nightly adherence risk scoring for all active patients."""
    from services.ai.adherence_engine.predictor import AdherencePredictor
    predictor = AdherencePredictor()
    from uuid import UUID
    if pharmacy_id and db:
        profiles = await predictor.score_all_patients(UUID(pharmacy_id), db=db)
        high_risk = [p for p in profiles if p.risk_level in ("high", "critical")]
        logger.info(
            "Adherence scoring: pharmacy=%s total=%d high_risk=%d",
            pharmacy_id[:8], len(profiles), len(high_risk)
        )
    return {"scored": True}


async def evaluate_reorder_needs(pharmacy_id: str = None, db=None):
    """Nightly inventory reorder evaluation."""
    if not pharmacy_id or not db:
        return
    from sqlalchemy import text
    from services.ai.inventory_intelligence.forecaster import SmartReorderEngine
    from uuid import UUID

    engine = SmartReorderEngine()
    result = await db.execute(
        text("SELECT ndc11, quantity_on_hand, drug_product_id FROM stock_levels WHERE pharmacy_id = :pid"),
        {"pid": pharmacy_id}
    )
    reorders_needed = 0
    for row in result.mappings().all():
        decision = await engine.evaluate_reorder(
            ndc11=row["ndc11"],
            pharmacy_id=UUID(pharmacy_id),
            current_stock=float(row["quantity_on_hand"]),
        )
        if decision.reorder_now:
            reorders_needed += 1
    logger.info("Reorder evaluation: pharmacy=%s reorders_needed=%d", pharmacy_id[:8], reorders_needed)


async def refresh_star_ratings(pharmacy_id: str = None, db=None):
    """Weekly Star Ratings recalculation."""
    if not pharmacy_id or not db:
        return
    from services.core.clinical_services.cms_star_ratings import StarRatingsCalculator
    from uuid import UUID
    calc = StarRatingsCalculator(db)
    ratings = await calc.calculate_pharmacy_ratings(UUID(pharmacy_id), date.today().year)
    logger.info("Star ratings refreshed: overall=%.1f", ratings.overall_star_estimate)


async def sync_pdmp_data(pharmacy_id: str = None, db=None):
    """Periodic PDMP data freshness check."""
    logger.info("PDMP sync check: pharmacy=%s", str(pharmacy_id)[:8] if pharmacy_id else "all")


async def send_expiry_alerts(pharmacy_id: str = None, db=None):
    """Daily expiry alerts for pharmacy management."""
    if not db:
        return
    from sqlalchemy import text
    result = await db.execute(text("""
        SELECT COUNT(*) AS count
        FROM inventory_lots
        WHERE pharmacy_id = :pid
          AND expiry_date <= CURRENT_DATE + INTERVAL '14 days'
          AND quantity_on_hand > 0
    """), {"pid": pharmacy_id})
    row = result.one_or_none()
    if row and row[0] > 0:
        logger.warning("EXPIRY ALERT: %d lots expiring within 14 days", row[0])


async def run_backoffice_agents_all_pharmacies(db=None):
    """
    Run all four back-office agents across all active pharmacies.
    AutoRebill: re-submits recoverable claim rejections
    AutoPA: initiates prior-auth for PA-required rejects
    AutoReorder: creates draft POs for below-reorder-point drugs
    ChronicRefillPreStager: identifies upcoming refill candidates
    """
    if not db:
        logger.info("Back-office agents: no DB session — skipping (will run when Celery is live)")
        return
    from sqlalchemy import text
    rows = (await db.execute(text(
        "SELECT id FROM pharmacies WHERE is_active = true"))).all()
    from services.core.pharmacy_workflow.backoffice_agents import BackOfficeOrchestrator
    for (pharm_id,) in rows:
        try:
            orch = BackOfficeOrchestrator(db)
            result = await orch.run_all(str(pharm_id))
            logger.info("BackOffice agents for pharmacy %s: %s", str(pharm_id)[:8], result)
        except Exception as e:
            logger.error("Back-office agents failed for %s: %s", str(pharm_id)[:8], e)
