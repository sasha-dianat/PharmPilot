"""
Push Notification Service — APNS (iOS) + FCM (Android)
=======================================================
Sends push notifications to patient mobile app.
HIPAA requirement: no PHI in notification payload.
Drug names, diagnoses, and clinical information ONLY in secure in-app messages.

Push payload structure:
  iOS (APNS):   {"aps": {"alert": {"title": ..., "body": ...}, "badge": n}}
  Android (FCM): {"notification": {"title": ..., "body": ...}, "data": {...}}

PHI safety rules enforced at this layer:
  - Drug names: NEVER in push body
  - Diagnoses: NEVER in push body
  - Dollar amounts: allowed (not PHI)
  - Pharmacy name: allowed
  - Generic "your prescription" language only
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID

import httpx

logger = logging.getLogger(__name__)


class NotificationChannel(str, Enum):
    PUSH    = "push"
    SMS     = "sms"
    IN_APP  = "in_app"
    EMAIL   = "email"


class NotificationType(str, Enum):
    REFILL_REMINDER      = "refill_reminder"
    PICKUP_READY         = "pickup_ready"
    PRESCRIPTION_DELAYED = "prescription_delayed"
    INSURANCE_ISSUE      = "insurance_issue"
    PRIOR_AUTH_APPROVED  = "prior_auth_approved"
    PRIOR_AUTH_DENIED    = "prior_auth_denied"
    APPOINTMENT_REMINDER = "appointment_reminder"
    SECURITY_ALERT       = "security_alert"   # Staff only
    DURESS_ACTIVATED     = "duress_activated"  # Staff only


# PHI-safe push notification templates (no drug names, no diagnoses)
PUSH_TEMPLATES: dict[NotificationType, dict] = {
    NotificationType.REFILL_REMINDER: {
        "title": "Refill Reminder",
        "body":  "It looks like it may be time for a refill. Tap to check.",
        "action": "open_prescriptions",
    },
    NotificationType.PICKUP_READY: {
        "title": "Prescription Ready",
        "body":  "Your prescription at {pharmacy_name} is ready for pickup. We're open until {closing_time}.",
        "action": "open_prescriptions",
    },
    NotificationType.PRESCRIPTION_DELAYED: {
        "title": "Prescription Delayed",
        "body":  "There's a delay with your prescription. Tap to see details.",
        "action": "open_prescriptions",
    },
    NotificationType.INSURANCE_ISSUE: {
        "title": "Insurance Issue",
        "body":  "There's an issue with your insurance. Please contact your pharmacy.",
        "action": "open_prescriptions",
    },
    NotificationType.PRIOR_AUTH_APPROVED: {
        "title": "Prior Authorization Approved",
        "body":  "Your prior authorization has been approved. Your pharmacy will contact you.",
        "action": "open_prescriptions",
    },
    NotificationType.PRIOR_AUTH_DENIED: {
        "title": "Prior Authorization Update",
        "body":  "There's an update on your prior authorization. Tap for details.",
        "action": "open_prescriptions",
    },
}


@dataclass
class PushNotificationRequest:
    patient_id: UUID
    notification_type: NotificationType
    device_token: str
    platform: str              # ios | android
    pharmacy_name: str = ""
    closing_time: str = ""
    extra_data: dict = field(default_factory=dict)


@dataclass
class PushNotificationResult:
    patient_id: UUID
    notification_type: NotificationType
    success: bool
    message_id: Optional[str] = None
    error: Optional[str] = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class PushNotificationService:
    """
    Multi-platform push notification service.
    Uses Firebase Cloud Messaging (FCM) for both iOS and Android
    via the HTTP v1 API (supports APNS via FCM for unified delivery).
    """

    FCM_API_URL = "https://fcm.googleapis.com/v1/projects/{project_id}/messages:send"

    def __init__(self, fcm_project_id: str = "", fcm_service_account_key: dict = None):
        self.fcm_project_id = fcm_project_id
        self.fcm_key = fcm_service_account_key
        self._access_token: Optional[str] = None

    async def send(self, request: PushNotificationRequest) -> PushNotificationResult:
        """Send a PHI-safe push notification."""
        template = PUSH_TEMPLATES.get(request.notification_type)
        if not template:
            return PushNotificationResult(
                patient_id=request.patient_id,
                notification_type=request.notification_type,
                success=False,
                error=f"No template for notification type: {request.notification_type}",
            )

        # Format template with safe values
        title = template["title"]
        body = template["body"].format(
            pharmacy_name=request.pharmacy_name,
            closing_time=request.closing_time or "closing time",
        )

        if not self.fcm_project_id:
            # Log-only in development
            logger.info(
                "PUSH [%s] → patient=%s: %s — %s",
                request.platform, str(request.patient_id)[:8], title, body
            )
            return PushNotificationResult(
                patient_id=request.patient_id,
                notification_type=request.notification_type,
                success=True,
                message_id=f"dev-{request.patient_id!s:.8}",
            )

        return await self._send_fcm(request, title, body)

    async def send_batch(
        self,
        requests: list[PushNotificationRequest],
    ) -> list[PushNotificationResult]:
        """Send multiple notifications concurrently."""
        import asyncio
        results = await asyncio.gather(
            *[self.send(r) for r in requests],
            return_exceptions=True,
        )
        return [
            r if isinstance(r, PushNotificationResult)
            else PushNotificationResult(
                patient_id=requests[i].patient_id,
                notification_type=requests[i].notification_type,
                success=False,
                error=str(r),
            )
            for i, r in enumerate(results)
        ]

    async def _send_fcm(
        self,
        request: PushNotificationRequest,
        title: str,
        body: str,
    ) -> PushNotificationResult:
        token = await self._get_access_token()
        payload = {
            "message": {
                "token": request.device_token,
                "notification": {"title": title, "body": body},
                "data": {
                    "action": PUSH_TEMPLATES[request.notification_type].get("action", ""),
                    "notification_type": request.notification_type.value,
                    **{k: str(v) for k, v in request.extra_data.items()},
                },
                "apns": {
                    "payload": {"aps": {"sound": "default", "badge": 1}},
                },
                "android": {
                    "notification": {"sound": "default"},
                    "priority": "high",
                },
            }
        }

        try:
            url = self.FCM_API_URL.format(project_id=self.fcm_project_id)
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 200:
                    data = response.json()
                    return PushNotificationResult(
                        patient_id=request.patient_id,
                        notification_type=request.notification_type,
                        success=True,
                        message_id=data.get("name"),
                    )
                else:
                    return PushNotificationResult(
                        patient_id=request.patient_id,
                        notification_type=request.notification_type,
                        success=False,
                        error=f"FCM error: {response.status_code} {response.text[:200]}",
                    )
        except Exception as exc:
            return PushNotificationResult(
                patient_id=request.patient_id,
                notification_type=request.notification_type,
                success=False,
                error=str(exc),
            )

    async def _get_access_token(self) -> str:
        """Get OAuth2 access token for FCM API."""
        if self._access_token:
            return self._access_token
        # In production: use google-auth library to get token from service account
        # For development: return empty string (will fail gracefully)
        return ""
