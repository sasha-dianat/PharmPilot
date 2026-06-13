from __future__ import annotations

from services.ai.polypharmacy.schema import Finding, MessageFormat, PolyContext


def build_message(findings: list[Finding], context: PolyContext, fmt: MessageFormat = "sbar") -> str:
    if fmt == "concise":
        return _concise(findings, context)
    return _sbar(findings, context)


def _patient_context(context: PolyContext) -> str:
    age = f"{context.age} years old" if context.age is not None else "age not documented"
    conditions = ", ".join(context.conditions) if context.conditions else "no documented conditions"
    return f"Patient context: {age}; conditions: {conditions}."


def _finding_lines(findings: list[Finding]) -> list[str]:
    if not findings:
        return ["No high- or moderate-priority deterministic polypharmacy findings were identified in the available data."]
    lines: list[str] = []
    for index, finding in enumerate(findings, start=1):
        drugs = ", ".join(finding.drugs_involved) if finding.drugs_involved else "no current drug listed"
        caution = f" Taper caution: {finding.tapering_caution}" if finding.tapering_caution else ""
        lines.append(
            f"{index}. {finding.priority.upper()} {finding.category.replace('_', ' ')} ({drugs}): "
            f"{finding.explanation} Pharmacist question: {finding.suggested_pharmacist_discussion}{caution}"
        )
    return lines


def _sbar(findings: list[Finding], context: PolyContext) -> str:
    lines = [
        "SBAR medication review draft",
        "",
        "Situation:",
        "Requesting your review of deterministic polypharmacy findings from the medication profile.",
        "",
        "Background:",
        _patient_context(context),
        "",
        "Assessment:",
        *_finding_lines(findings),
        "",
        "Recommendation / question:",
        "Please review whether any medication-list updates, monitoring, safer alternatives, or a deprescribing/taper plan are appropriate.",
    ]
    return "\n".join(lines)


def _concise(findings: list[Finding], context: PolyContext) -> str:
    lines = [
        "Medication review draft",
        _patient_context(context),
        "Requesting your review of the following deterministic findings:",
        *_finding_lines(findings),
        "Please advise whether updates, monitoring, safer alternatives, or a deprescribing/taper plan are appropriate.",
    ]
    return "\n".join(lines)
