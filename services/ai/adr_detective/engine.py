from __future__ import annotations

from dataclasses import replace
from datetime import date

from services.ai.adr_detective.knowledge import DRUG_CLASS_OVERRIDES, DRUG_REACTIONS, REACTION_KEYWORDS
from services.ai.adr_detective.schema import ADRContext, ADRMedication, LabValue, ReactionEntry, SuspectedCause


def assess(context: ADRContext) -> list[SuspectedCause]:
    reaction = _match_reaction(context.complaint)
    if not reaction:
        return [
            SuspectedCause(
                drug="Unclear",
                normalized_name="",
                reaction="unclear",
                causality="unclear",
                seriousness="mild",
                missing_information=["specific symptom not recognized - clarify complaint"],
                reasoning=["The reported complaint did not match a hardcoded ADR reaction concept."],
                questions_to_ask=["Clarify the exact symptom, severity, timing, and whether any urgent red flags are present."],
                suggested_pharmacist_action="Clarify the complaint and review the medication profile before drawing an ADR conclusion.",
                urgency="unknown",
                evidence_sources=[],
            )
        ]

    candidates: list[tuple[ADRMedication, ReactionEntry]] = []
    for medication in context.medications:
        for entry in _matching_entries(medication, reaction):
            candidates.append((medication, entry))

    if not candidates:
        return []

    same_reaction_names = [med.drug_name for med, _entry in candidates]
    causes = [
        _build_cause(context, medication, entry, same_reaction_names)
        for medication, entry in candidates
    ]
    if reaction == "bleeding" and _has_class(context.medications, "nsaid") and _has_class(context.medications, "anticoagulant"):
        causes = [
            replace(
                cause,
                seriousness="serious",
                urgency="high",
                signals=[*cause.signals, "concurrent NSAID and anticoagulant exposure increases bleeding concern"],
                reasoning=[
                    *cause.reasoning,
                    "The medication profile includes both an NSAID and an anticoagulant, a deterministic high-urgency bleeding signal.",
                ],
            )
            for cause in causes
        ]
    return causes


def _match_reaction(complaint: str) -> str | None:
    text = f" {complaint.lower()} "
    matches: list[tuple[int, str]] = []
    for reaction, keywords in REACTION_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in text:
                matches.append((len(keyword), reaction))
    if not matches:
        return None
    matches.sort(reverse=True)
    return matches[0][1]


def _classes(medication: ADRMedication) -> set[str]:
    return set(medication.classes) | DRUG_CLASS_OVERRIDES.get(medication.normalized_name, set())


def _matching_entries(medication: ADRMedication, reaction: str) -> list[ReactionEntry]:
    keys = [medication.normalized_name, *_classes(medication)]
    entries: list[ReactionEntry] = []
    seen: set[tuple[str, str]] = set()
    for key in keys:
        for entry in DRUG_REACTIONS.get(key, []):
            marker = (entry.reaction, entry.evidence_source)
            if entry.reaction == reaction and marker not in seen:
                entries.append(entry)
                seen.add(marker)
    return entries


def _build_cause(
    context: ADRContext,
    medication: ADRMedication,
    entry: ReactionEntry,
    same_reaction_names: list[str],
) -> SuspectedCause:
    signals: list[str] = []
    missing: list[str] = []
    positive = 0
    stopped_before_onset = False

    if context.onset_date and medication.stop_date and medication.stop_date < context.onset_date:
        stopped_before_onset = True
        signals.append("medication stop date predates reported symptom onset")

    if context.onset_date and medication.start_date:
        if medication.start_date <= context.onset_date and _onset_plausible(entry.typical_onset, medication.start_date, context.onset_date):
            positive += 1
            signals.append(f"temporal relationship fits typical {entry.typical_onset} onset")
        elif medication.start_date > context.onset_date:
            signals.append("medication start date is after reported symptom onset")
    else:
        if not context.onset_date:
            missing.append("symptom onset date - not provided")
        if not medication.start_date:
            missing.append(f"{medication.drug_name} start date - not on file")

    lab_signal = _lab_signal(entry.reaction, context.labs)
    if lab_signal:
        positive += 1
        signals.append(lab_signal)
    missing.extend(_missing_labs(entry.reaction, context.labs))

    if medication.recent_dose_increase:
        positive += 1
        signals.append("recent dose increase reported")

    if entry.reaction == "confusion" and context.age is not None and context.age >= 65:
        signals.append("age 65 or older increases vulnerability to anticholinergic cognitive effects")

    alternatives = [
        f"{name} is also associated with {entry.reaction}"
        for name in same_reaction_names
        if name != medication.drug_name
    ]
    multiple_same_reaction = bool(alternatives)

    if stopped_before_onset or (context.onset_date and medication.start_date and medication.start_date > context.onset_date):
        causality = "unlikely"
    elif positive <= 0:
        causality = "possible"
    elif multiple_same_reaction and positive < 2:
        causality = "possible"
    else:
        causality = "probable"

    urgency = _urgency(entry.reaction, entry.seriousness)
    reasoning = _reasoning(medication, entry, causality, signals)
    questions = _questions(entry.reaction, missing)
    action = _suggested_action(entry)

    return SuspectedCause(
        drug=medication.drug_name,
        normalized_name=medication.normalized_name,
        reaction=entry.reaction,
        causality=causality,
        seriousness=entry.seriousness,
        signals=signals,
        reasoning=reasoning,
        alternative_explanations=alternatives,
        questions_to_ask=questions,
        missing_information=sorted(set(missing)),
        suggested_pharmacist_action=action,
        urgency=urgency,
        evidence_sources=[entry.evidence_source],
    )


def _onset_plausible(typical_onset: str, start: date, onset: date) -> bool:
    days = (onset - start).days
    if days < 0:
        return False
    if typical_onset == "days":
        return days <= 21
    if typical_onset == "weeks":
        return 7 <= days <= 120
    if typical_onset == "months":
        return 21 <= days <= 540
    return True


def _lab_signal(reaction: str, labs: dict[str, LabValue]) -> str | None:
    if reaction == "hyponatremia":
        sodium = _find_lab(labs, ("sodium", "na"))
        if sodium is not None and sodium < 135:
            return f"serum sodium {sodium:g} is below 135"
    if reaction == "bleeding":
        hemoglobin = _find_lab(labs, ("hemoglobin", "hgb"))
        hematocrit = _find_lab(labs, ("hematocrit", "hct"))
        if hemoglobin is not None and hemoglobin < 12:
            return f"hemoglobin {hemoglobin:g} is low"
        if hematocrit is not None and hematocrit < 36:
            return f"hematocrit {hematocrit:g} is low"
    if reaction == "hyperkalemia":
        potassium = _find_lab(labs, ("potassium", "serum potassium", "k"))
        if potassium is not None and potassium > 5:
            return f"serum potassium {potassium:g} is elevated"
    if reaction == "hypoglycemia":
        glucose = _find_lab(labs, ("glucose", "blood glucose"))
        if glucose is not None and glucose < 70:
            return f"glucose {glucose:g} is below 70"
    return None


def _missing_labs(reaction: str, labs: dict[str, LabValue]) -> list[str]:
    if reaction == "hyponatremia" and _find_lab(labs, ("sodium", "na")) is None:
        return ["serum sodium - not on file"]
    if reaction == "bleeding" and _find_lab(labs, ("hemoglobin", "hgb", "hematocrit", "hct")) is None:
        return ["hemoglobin/hematocrit - not on file"]
    if reaction == "hyperkalemia" and _find_lab(labs, ("potassium", "serum potassium", "k")) is None:
        return ["serum potassium - not on file"]
    if reaction == "hypoglycemia" and _find_lab(labs, ("glucose", "blood glucose")) is None:
        return ["glucose - not on file"]
    return []


def _find_lab(labs: dict[str, LabValue], names: tuple[str, ...]) -> float | None:
    for key, lab in labs.items():
        lowered = key.lower()
        if any(name == lowered or name in lowered for name in names):
            return lab.value
    return None


def _urgency(reaction: str, seriousness: str) -> str:
    if seriousness == "serious" or reaction in {"bleeding", "angioedema", "hypoglycemia", "hyperkalemia"}:
        return "high"
    if seriousness == "moderate":
        return "routine"
    return "low"


def _reasoning(medication: ADRMedication, entry: ReactionEntry, causality: str, signals: list[str]) -> list[str]:
    lines = [
        f"{medication.drug_name} is in the deterministic ADR knowledge base for {entry.reaction}.",
        f"Deterministic causality is {causality}; the engine does not use LLM output for this verdict.",
    ]
    if signals:
        lines.extend(signals)
    else:
        lines.append("No temporal, laboratory, or recent-dose signal was available, so the finding remains conservative.")
    return lines


def _questions(reaction: str, missing: list[str]) -> list[str]:
    questions = [
        "When did the symptom begin relative to each medication and any recent dose changes?",
        "How severe is the symptom, and are there red flags requiring urgent evaluation?",
    ]
    if missing:
        questions.append("Can the missing information be obtained before making a clinical recommendation?")
    if reaction == "bleeding":
        questions.append("Ask about melena, hematemesis, dizziness, falls, bruising, and recent procedures.")
    if reaction == "hyponatremia":
        questions.append("Ask about confusion, falls, fluid intake changes, and recent sodium measurements.")
    return questions


def _suggested_action(entry: ReactionEntry) -> str:
    if entry.seriousness == "serious":
        return "Assess severity and red flags promptly, document findings, and discuss medication-related concern with the prescriber or urgent care pathway as appropriate."
    if entry.seriousness == "moderate":
        return "Assess symptom severity, timing, and functional impact, then consider prescriber discussion if the pattern remains medication-related."
    return "Assess tolerability and timing, counsel on what to monitor, and document the patient-specific review."


def _has_class(medications: list[ADRMedication], class_name: str) -> bool:
    return any(class_name in _classes(medication) for medication in medications)
