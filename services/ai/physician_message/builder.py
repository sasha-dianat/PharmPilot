from __future__ import annotations

from services.ai.physician_message.schema import MessageContent, MessageInput


def build_baseline(input: MessageInput, fmt: str) -> MessageContent:
    subject = _subject(input)
    sections = _sections(input, fmt)
    return MessageContent(
        format=fmt,
        language="en",
        subject=subject,
        body=body_from_sections(fmt, sections),
        urgency=input.urgency,
        sections=sections,
    )


def body_from_sections(fmt: str, sections: dict[str, str]) -> str:
    if fmt == "sbar":
        return "\n\n".join(
            [
                f"Situation:\n{sections['situation']}",
                f"Background:\n{sections['background']}",
                f"Assessment:\n{sections['assessment']}",
                f"Recommendation:\n{sections['recommendation']}",
            ]
        )
    if fmt == "soap":
        return "\n\n".join(
            [
                f"Subjective:\n{sections['subjective']}",
                f"Objective:\n{sections['objective']}",
                f"Assessment:\n{sections['assessment']}",
                f"Plan:\n{sections['plan']}",
            ]
        )
    if fmt == "letter":
        parts = [
            sections["greeting"],
            sections["body"],
            sections["recommendation"],
            sections["closing"],
        ]
        return "\n\n".join(part for part in parts if part.strip())
    return "\n\n".join([sections["message"], sections["recommendation"]])


def _sections(input: MessageInput, fmt: str) -> dict[str, str]:
    patient_context = _value(input.patient_context, "Patient context not supplied")
    rationale = _value(input.clinical_rationale, "Clinical rationale not supplied")
    supporting_data = _supporting_data(input.supporting_data)
    issue = input.medication_issue.strip()
    recommendation = input.recommendation_or_question.strip()
    urgency_line = f"Urgency: {input.urgency}"

    if fmt == "sbar":
        return {
            "situation": (
                f"{urgency_line}. I am writing to flag a medication therapy concern: {issue}."
            ),
            "background": (
                f"Patient context: {patient_context}\nSupporting data: {supporting_data}"
            ),
            "assessment": (
                f"My assessment/rationale: {rationale}. I am raising this for your review and clinical judgment."
            ),
            "recommendation": (
                f"Would you consider the following recommendation/question: {recommendation} Please advise on how you would like to proceed."
            ),
        }
    if fmt == "soap":
        return {
            "subjective": (
                f"{urgency_line}. Patient context: {patient_context}"
            ),
            "objective": (
                f"Medication issue: {issue}\nSupporting data: {supporting_data}"
            ),
            "assessment": (
                f"Clinical rationale: {rationale}. This is shared as a pharmacist-identified concern for prescriber review."
            ),
            "plan": (
                f"Recommendation/question: {recommendation} Please advise whether you would like any change or additional follow-up."
            ),
        }
    if fmt == "letter":
        prescriber = input.prescriber_name.strip() if input.prescriber_name and input.prescriber_name.strip() else "Prescriber"
        pharmacist = input.pharmacist_name.strip() if input.pharmacist_name and input.pharmacist_name.strip() else "Pharmacy team"
        return {
            "greeting": f"Dear Dr. {prescriber},",
            "body": (
                f"{urgency_line}. I am writing to flag a medication therapy concern for your review.\n"
                f"Patient context: {patient_context}\n"
                f"Medication issue: {issue}\n"
                f"Clinical rationale: {rationale}\n"
                f"Supporting data: {supporting_data}"
            ),
            "recommendation": (
                f"Would you consider the following recommendation/question: {recommendation} Please advise on your preferred plan."
            ),
            "closing": f"Respectfully,\n{pharmacist}",
        }
    return {
        "message": (
            f"{urgency_line}. I am writing to flag {issue}. Patient context: {patient_context}. "
            f"Clinical rationale: {rationale}. Supporting data: {supporting_data}."
        ),
        "recommendation": (
            f"Would you consider this recommendation/question: {recommendation} Please advise."
        ),
    }


def _subject(input: MessageInput) -> str:
    issue = input.medication_issue.strip()
    if len(issue) > 80:
        issue = f"{issue[:77].rstrip()}..."
    return f"Medication therapy concern ({input.urgency}): {issue}"


def _value(value: str | None, fallback: str) -> str:
    return value.strip() if value and value.strip() else fallback


def _supporting_data(items: list[str]) -> str:
    clean = [item.strip() for item in items if item and item.strip()]
    return "; ".join(clean) if clean else "None supplied"

