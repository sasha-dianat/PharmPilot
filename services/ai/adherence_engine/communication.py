"""
Multilingual Patient Communication Engine
==========================================
Generates personalized, culturally appropriate outreach messages.
Supports 14 languages. Reading level: 6th grade default (health literacy).
Channels: SMS, push notification, secure in-app message, phone script.
No PHI in SMS — drug names and diagnoses sent only via secure in-app channel.
"""
import logging
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)

# Supported languages with display names
SUPPORTED_LANGUAGES = {
    "en": "English",      "es": "Spanish",     "zh-CN": "Chinese (Simplified)",
    "ar": "Arabic",       "hi": "Hindi",        "fr": "French",
    "vi": "Vietnamese",   "ko": "Korean",       "ru": "Russian",
    "pt": "Portuguese",   "tl": "Tagalog",      "ht": "Haitian Creole",
    "ja": "Japanese",     "de": "German",
}

# SMS templates (NO PHI — only pharmacy name and refill prompt)
# Drug names NEVER appear in SMS — patient must open the app for clinical info
SMS_REFILL_REMINDER = {
    "en": "Hi from {pharmacy}! It looks like it may be time for a refill. Reply STOP to opt out.",
    "es": "Hola de {pharmacy}! Parece que puede ser hora de un resurtido. Responda STOP para salir.",
    "fr": "Bonjour de {pharmacy}! Il est peut-être temps pour un renouvellement. Répondez STOP pour désactiver.",
    "ar": "مرحباً من {pharmacy}! قد يكون حان وقت إعادة ملء وصفتك. أرسل STOP للإلغاء.",
    "zh-CN": "来自{pharmacy}的您好！您可能需要补充药物了。回复STOP以退订。",
    "vi": "Xin chào từ {pharmacy}! Có thể đã đến lúc bạn cần lấy thuốc. Trả lời STOP để hủy đăng ký.",
    "ko": "{pharmacy}에서 안녕하세요! 약을 보충할 시간이 된 것 같습니다. 수신 거부는 STOP 회신.",
    "hi": "{pharmacy} से नमस्ते! शायद दवा दोबारा लेने का समय हो गया है। बंद करने के लिए STOP लिखें।",
}

SMS_PICKUP_READY = {
    "en": "Great news from {pharmacy}! Your prescription is ready for pickup. We're open until {closing_time}.",
    "es": "¡Buenas noticias de {pharmacy}! Su receta está lista para recoger. Abierto hasta las {closing_time}.",
    "fr": "Bonne nouvelle de {pharmacy}! Votre ordonnance est prête. Ouvert jusqu'à {closing_time}.",
    "ar": "أخبار رائعة من {pharmacy}! وصفتك جاهزة للاستلام. مفتوح حتى {closing_time}.",
    "zh-CN": "来自{pharmacy}的好消息！您的处方已准备好取药。营业至{closing_time}。",
}

# In-app secure messages (CAN include clinical context — HIPAA-compliant channel)
INAPP_ADHERENCE_MESSAGE = {
    "en": (
        "Hi {first_name}, we noticed you may be running low on {drug_name}. "
        "Staying on schedule with this medication helps {benefit}. "
        "Tap to request a refill — we'll have it ready for you."
    ),
    "es": (
        "Hola {first_name}, notamos que puede estar quedándose sin {drug_name}. "
        "Tomar este medicamento regularmente ayuda a {benefit}. "
        "Toque para solicitar un resurtido."
    ),
}

DRUG_BENEFIT_DESCRIPTIONS = {
    "metformin":     "control your blood sugar",
    "lisinopril":    "protect your heart and kidneys",
    "atorvastatin":  "lower your cholesterol and reduce heart attack risk",
    "amlodipine":    "keep your blood pressure in a healthy range",
    "levothyroxine": "keep your thyroid hormone levels stable",
    "metoprolol":    "protect your heart and control your blood pressure",
}


@dataclass
class OutreachMessage:
    patient_id: UUID
    channel: str               # sms | push | inapp | phone_script
    language: str
    subject: Optional[str]
    body: str
    phi_present: bool          # True = only send via secure channel
    action_url: Optional[str]  # Deep link into the app
    urgency: str               # normal | high | critical


class PatientCommunicationEngine:
    """Generates personalized, translated outreach messages per patient."""

    def __init__(self, pharmacy_name: str = "Your Pharmacy"):
        self.pharmacy_name = pharmacy_name

    def generate_refill_reminder(
        self,
        patient_id: UUID,
        patient_first_name: str,
        drug_name: str,
        days_until_due: int,
        language: str = "en",
        closing_time: str = "6:00 PM",
    ) -> list[OutreachMessage]:
        """Generate refill reminder across multiple channels."""
        messages = []
        lang = language if language in SUPPORTED_LANGUAGES else "en"
        drug_lower = drug_name.lower().split()[0]
        benefit = DRUG_BENEFIT_DESCRIPTIONS.get(drug_lower, "manage your health condition")

        urgency = "critical" if days_until_due <= 0 else ("high" if days_until_due <= 3 else "normal")

        # SMS — no PHI, just prompt to check app
        sms_template = SMS_REFILL_REMINDER.get(lang, SMS_REFILL_REMINDER["en"])
        messages.append(OutreachMessage(
            patient_id=patient_id,
            channel="sms",
            language=lang,
            subject=None,
            body=sms_template.format(pharmacy=self.pharmacy_name),
            phi_present=False,
            action_url="pharmpilot://prescriptions",
            urgency=urgency,
        ))

        # Push notification — short, no drug name in notification body
        push_body = (
            f"Time to refill" if days_until_due <= 3
            else f"Refill due in {days_until_due} days"
        )
        messages.append(OutreachMessage(
            patient_id=patient_id,
            channel="push",
            language=lang,
            subject="Refill Reminder",
            body=push_body,
            phi_present=False,
            action_url="pharmpilot://prescriptions",
            urgency=urgency,
        ))

        # In-app secure message — CAN include drug name and clinical context
        inapp_template = INAPP_ADHERENCE_MESSAGE.get(lang, INAPP_ADHERENCE_MESSAGE["en"])
        messages.append(OutreachMessage(
            patient_id=patient_id,
            channel="inapp",
            language=lang,
            subject="Refill Reminder",
            body=inapp_template.format(
                first_name=patient_first_name,
                drug_name=drug_name,
                benefit=benefit,
            ),
            phi_present=True,
            action_url="pharmpilot://prescriptions",
            urgency=urgency,
        ))

        return messages

    def generate_pickup_ready(
        self,
        patient_id: UUID,
        language: str = "en",
        closing_time: str = "6:00 PM",
    ) -> OutreachMessage:
        lang = language if language in SUPPORTED_LANGUAGES else "en"
        template = SMS_PICKUP_READY.get(lang, SMS_PICKUP_READY["en"])
        return OutreachMessage(
            patient_id=patient_id,
            channel="sms",
            language=lang,
            subject=None,
            body=template.format(pharmacy=self.pharmacy_name, closing_time=closing_time),
            phi_present=False,
            action_url="pharmpilot://prescriptions",
            urgency="high",
        )

    def generate_pharmacist_call_script(
        self,
        patient_first_name: str,
        drug_name: str,
        days_overdue: int,
        language: str = "en",
    ) -> str:
        """Phone script for pharmacist to follow during high-risk outreach call."""
        return (
            f"Hello, may I speak with {patient_first_name}? "
            f"[pause] "
            f"Hi {patient_first_name}, this is [Pharmacist Name] calling from {self.pharmacy_name}. "
            f"I'm reaching out because we noticed your {drug_name} refill is "
            f"{'overdue by ' + str(days_overdue) + ' days' if days_overdue > 0 else 'coming up soon'}. "
            f"Staying consistent with this medication is really important for your health. "
            f"Is there anything making it difficult to get your refill — "
            f"like the cost, transportation, or side effects? "
            f"[listen, address barriers] "
            f"We'd love to help. Can I go ahead and request that refill for you right now? "
            f"It will be ready in [X hours]. "
            f"Is there anything else I can help you with today?"
        )
