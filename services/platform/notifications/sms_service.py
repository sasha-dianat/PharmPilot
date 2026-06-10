"""
SMS Notification Service — Twilio Integration
================================================
Sends PHI-safe SMS messages for refill reminders and pickup alerts.
No drug names, diagnoses, or clinical information in SMS — HIPAA requirement.
PHI belongs only in the secure in-app messaging channel.
"""
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx

from services.platform.config import settings

logger = logging.getLogger(__name__)

# PHI-safe SMS templates (no drug names)
SMS_TEMPLATES = {
    "refill_reminder": "Hi from {pharmacy}! It looks like it may be time for a refill. "
                       "Call us or use the PharmPilot app to request. "
                       "Reply STOP to opt out.",

    "pickup_ready":    "{pharmacy}: Your prescription is ready for pickup! "
                       "We're open until {closing}. "
                       "Questions? Call {phone}. Reply STOP to opt out.",

    "pickup_reminder": "{pharmacy}: Reminder — you have a prescription waiting for pickup. "
                       "It will be returned to stock after {days} days. "
                       "Call {phone} or use the app. Reply STOP to opt out.",

    "insurance_issue": "{pharmacy}: We need your help with an insurance question "
                       "for a recent prescription. Please call us at {phone}. "
                       "Reply STOP to opt out.",

    "refill_sync":     "{pharmacy}: All your medications are synced and ready! "
                       "Pickup date: {date}. See you then! Reply STOP to opt out.",
}


@dataclass
class SMSResult:
    to_phone: str
    success: bool
    message_sid: Optional[str] = None
    error: Optional[str] = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class SMSService:
    """Twilio SMS integration with PHI safety enforcement."""

    TWILIO_API = "https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    def __init__(
        self,
        account_sid: str = "",
        auth_token: str = "",
        from_number: str = "",
        integrations_sandbox: Optional[bool] = None,
    ):
        self.account_sid = account_sid
        self.auth_token  = auth_token
        self.from_number = from_number
        self.integrations_sandbox = (
            settings.INTEGRATIONS_SANDBOX if integrations_sandbox is None else integrations_sandbox
        )

    async def send(
        self,
        to_phone: str,
        template_name: str,
        template_vars: dict,
    ) -> SMSResult:
        """Send a PHI-safe SMS using a pre-approved template."""
        template = SMS_TEMPLATES.get(template_name)
        if not template:
            return SMSResult(to_phone=to_phone, success=False,
                             error=f"Unknown template: {template_name}")

        # Format template — only safe variables allowed
        message_body = template.format(**{
            k: str(v)[:50]  # Cap variable length for safety
            for k, v in template_vars.items()
            if k in ("pharmacy", "closing", "phone", "days", "date", "time")
        })

        if self.integrations_sandbox or not self.account_sid:
            message_sid = f"sandbox-sms-{self._stable_message_suffix(to_phone, message_body)}"
            logger.info(
                "SMS [SANDBOX] to=%s sid=%s body=%s",
                mask_phone(to_phone),
                message_sid,
                mask_phone_text(message_body)[:160],
            )
            return SMSResult(to_phone=to_phone, success=True, message_sid=message_sid)

        try:
            url = self.TWILIO_API.format(account_sid=self.account_sid)
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(
                    url,
                    data={
                        "To":   to_phone,
                        "From": self.from_number,
                        "Body": message_body,
                    },
                    auth=(self.account_sid, self.auth_token),
                )
                if response.status_code in (200, 201):
                    data = response.json()
                    logger.info("SMS sent: sid=%s to=%s…", data.get("sid"), to_phone[-4:])
                    return SMSResult(
                        to_phone=to_phone,
                        success=True,
                        message_sid=data.get("sid"),
                    )
                else:
                    return SMSResult(
                        to_phone=to_phone, success=False,
                        error=f"Twilio {response.status_code}: {response.text[:100]}",
                    )
        except Exception as exc:
            return SMSResult(to_phone=to_phone, success=False, error=str(exc))

    @staticmethod
    def _stable_message_suffix(to_phone: str, message_body: str) -> str:
        import hashlib
        return hashlib.sha256(f"{to_phone}|{message_body}".encode("utf-8")).hexdigest()[:12]


def mask_phone(phone: Optional[str]) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 5:
        return "***"
    return f"{digits[:3]}****{digits[-2:]}"


def mask_phone_text(text: str) -> str:
    def repl(match: re.Match) -> str:
        token = match.group(0)
        digits = re.sub(r"\D", "", token)
        if len(digits) < 7:
            return token
        return mask_phone(digits)

    return re.sub(r"\+?[\d][\d\s().-]{5,}\d", repl, text)
