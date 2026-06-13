from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import datetime, timezone

from services.ai.clinical_decision_support.normalizer import (
    classes_of as normalizer_classes_of,
    normalize,
)
from services.ai.med_reconciliation.schema import (
    PHARMACIST_VERIFICATION_NOTICE,
    MedEntry,
    ReconciliationContext,
    ReconciliationDiscrepancy,
    ReconciliationResult,
)


DISCREPANCY_TYPES = [
    "OMITTED",
    "ADDED",
    "DOSE_CHANGE",
    "FREQUENCY_CHANGE",
    "ROUTE_CHANGE",
    "DUPLICATE",
    "THERAPEUTIC_DUPLICATE",
    "STATUS_CONFLICT",
]

_SORT_ORDER = {
    "OMITTED": 0,
    "DOSE_CHANGE": 1,
    "ADDED": 2,
    "FREQUENCY_CHANGE": 3,
    "ROUTE_CHANGE": 4,
    "STATUS_CONFLICT": 5,
    "DUPLICATE": 6,
    "THERAPEUTIC_DUPLICATE": 7,
}
_SEVERITY_ORDER = {"high": 0, "moderate": 1, "low": 2}

_EXTRA_DRUG_CLASSES: dict[str, set[str]] = {
    "losartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "valsartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "olmesartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "irbesartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "candesartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "telmisartan": {"angiotensin_receptor_blocker", "renin_angiotensin_system_agent"},
    "insulin": {"antidiabetic", "insulin"},
    "insulin glargine": {"antidiabetic", "insulin"},
    "insulin lispro": {"antidiabetic", "insulin"},
    "insulin aspart": {"antidiabetic", "insulin"},
    "glipizide": {"antidiabetic", "sulfonylurea"},
    "glyburide": {"antidiabetic", "sulfonylurea"},
    "levothyroxine": {"thyroid_agent", "narrow_therapeutic_index"},
    "digoxin": {"cardiac_glycoside", "narrow_therapeutic_index"},
    "lithium": {"lithium", "narrow_therapeutic_index"},
    "phenytoin": {"antiepileptic", "narrow_therapeutic_index"},
    "carbamazepine": {"antiepileptic", "narrow_therapeutic_index"},
    "valproate": {"antiepileptic"},
    "valproic acid": {"antiepileptic"},
    "levetiracetam": {"antiepileptic"},
    "tacrolimus": {"immunosuppressant", "narrow_therapeutic_index"},
    "cyclosporine": {"immunosuppressant", "narrow_therapeutic_index"},
    "mycophenolate": {"immunosuppressant"},
    "amiodarone": {"antiarrhythmic"},
    "sotalol": {"antiarrhythmic"},
    "flecainide": {"antiarrhythmic"},
    "haloperidol": {"antipsychotic"},
    "risperidone": {"antipsychotic"},
    "quetiapine": {"antipsychotic"},
    "olanzapine": {"antipsychotic"},
}

_CLASS_ALIASES: dict[str, set[str]] = {
    "ace_inhibitor": {"renin_angiotensin_system_agent", "antihypertensive"},
    "angiotensin_receptor_blocker": {"renin_angiotensin_system_agent", "antihypertensive"},
    "anticoagulant": {"anticoagulants"},
    "antiepileptic": {"antiepileptics"},
    "immunosuppressant": {"immunosuppressants"},
    "antidiabetic": {"antidiabetics"},
    "biguanide": {"antidiabetics"},
    "insulin": {"antidiabetics"},
    "cardiac_glycoside": {"cardiac_glycosides"},
    "antiarrhythmic": {"antiarrhythmics"},
    "antipsychotic": {"antipsychotics"},
    "thyroid_agent": {"thyroid_agents"},
    "narrow_therapeutic_index": {"narrow_therapeutic_index"},
}

_OMITTED_HIGH_RISK_CLASSES = {
    "anticoagulants",
    "antiepileptics",
    "immunosuppressants",
    "antidiabetics",
    "insulin",
    "antipsychotics",
    "lithium",
    "cardiac_glycosides",
    "thyroid_agents",
}
_DOSE_HIGH_RISK_CLASSES = {
    "anticoagulants",
    "antiepileptics",
    "immunosuppressants",
    "antidiabetics",
    "cardiac_glycosides",
    "antiarrhythmics",
    "antipsychotics",
    "narrow_therapeutic_index",
    "insulin",
    "lithium",
}


def classes_of(name: str | None) -> set[str]:
    normalized = normalize(name)
    classes = set(normalizer_classes_of(normalized))
    classes |= _EXTRA_DRUG_CLASSES.get(normalized, set())
    for class_name in list(classes):
        classes |= _CLASS_ALIASES.get(class_name, set())
    return classes


def reconcile(context: ReconciliationContext) -> ReconciliationResult:
    source_a = [_prepare_entry(med) for med in context.source_a_meds]
    source_b = [_prepare_entry(med) for med in context.source_b_meds]

    a_by_key = _group_by_key(source_a)
    b_by_key = _group_by_key(source_b)
    matched_keys = set(a_by_key) & set(b_by_key)
    discrepancies: list[ReconciliationDiscrepancy] = []
    seen_ids: Counter[str] = Counter()

    for key in sorted(set(a_by_key) - set(b_by_key)):
        med = a_by_key[key][0]
        drug = _display_name(med)
        discrepancies.append(
            _build_discrepancy(
                "OMITTED",
                med.normalized_name,
                context,
                seen_ids,
                "high" if _has_any_class(med, _OMITTED_HIGH_RISK_CLASSES) else "moderate",
                drug,
                [drug],
                med,
                None,
                (
                    f"{drug} appears in {context.source_a_label} but is absent from {context.source_b_label}. "
                    "This may represent an omission, intentional discontinuation, or documentation gap."
                ),
                (
                    f"Verify with patient and prescriber whether {drug} should be continued, "
                    "discontinued, or was intentionally omitted."
                ),
            )
        )

    for key in sorted(set(b_by_key) - set(a_by_key)):
        med = b_by_key[key][0]
        drug = _display_name(med)
        discrepancies.append(
            _build_discrepancy(
                "ADDED",
                med.normalized_name,
                context,
                seen_ids,
                "moderate",
                drug,
                [drug],
                None,
                med,
                (
                    f"{drug} appears in {context.source_b_label} but is absent from {context.source_a_label}. "
                    "This may represent a new prescription, documentation gap, or patient non-reporting."
                ),
                (
                    f"Confirm with prescriber and patient whether {drug} is newly initiated or was "
                    f"pre-existing but not documented in {context.source_a_label}."
                ),
            )
        )

    primary_discrepant_keys: set[str] = set()
    for key in sorted(matched_keys):
        med_a = a_by_key[key][0]
        med_b = b_by_key[key][0]
        drug = _display_name(med_a)

        dose_a, dose_b = _comparable_doses(med_a, med_b)
        if dose_a is not None and dose_b is not None and _canonical_value(dose_a) != _canonical_value(dose_b):
            primary_discrepant_keys.add(key)
            discrepancies.append(
                _build_discrepancy(
                    "DOSE_CHANGE",
                    med_a.normalized_name,
                    context,
                    seen_ids,
                    "high" if _has_any_class(med_a, _DOSE_HIGH_RISK_CLASSES) or _has_any_class(med_b, _DOSE_HIGH_RISK_CLASSES) else "moderate",
                    drug,
                    [drug],
                    med_a,
                    med_b,
                    (
                        f"{drug} dose differs: {context.source_a_label} has '{dose_a}', "
                        f"{context.source_b_label} has '{dose_b}'. Dose changes in this drug class require verification."
                    ),
                    "Confirm intended dose with prescriber. Check for intentional titration, dispensing error, or documentation discrepancy.",
                )
            )

        if med_a.frequency and med_b.frequency and _canonical_value(med_a.frequency) != _canonical_value(med_b.frequency):
            primary_discrepant_keys.add(key)
            discrepancies.append(
                _build_discrepancy(
                    "FREQUENCY_CHANGE",
                    med_a.normalized_name,
                    context,
                    seen_ids,
                    "moderate",
                    drug,
                    [drug],
                    med_a,
                    med_b,
                    (
                        f"{drug} frequency differs: {context.source_a_label} has '{med_a.frequency}', "
                        f"{context.source_b_label} has '{med_b.frequency}'."
                    ),
                    "Verify intended frequency with prescriber.",
                )
            )

        if med_a.route and med_b.route and _canonical_value(med_a.route) != _canonical_value(med_b.route):
            primary_discrepant_keys.add(key)
            discrepancies.append(
                _build_discrepancy(
                    "ROUTE_CHANGE",
                    med_a.normalized_name,
                    context,
                    seen_ids,
                    "moderate",
                    drug,
                    [drug],
                    med_a,
                    med_b,
                    (
                        f"{drug} route of administration differs: {context.source_a_label} has '{med_a.route}', "
                        f"{context.source_b_label} has '{med_b.route}'."
                    ),
                    "Confirm intended route with prescriber (e.g., oral vs. IV transition may be intentional).",
                )
            )

        status_conflict = _status_conflict(med_a.status, med_b.status)
        if status_conflict:
            active_label, inactive_label = (
                (context.source_a_label, context.source_b_label)
                if _status_bucket(med_a.status) == "active"
                else (context.source_b_label, context.source_a_label)
            )
            discrepancies.append(
                _build_discrepancy(
                    "STATUS_CONFLICT",
                    med_a.normalized_name,
                    context,
                    seen_ids,
                    "moderate",
                    drug,
                    [drug],
                    med_a,
                    med_b,
                    f"{drug} is listed as active in {active_label} but inactive/discontinued in {inactive_label}.",
                    "Verify current status with prescriber and update medication list accordingly.",
                )
            )

    for source_label, entries, side in (
        (context.source_a_label, source_a, "a"),
        (context.source_b_label, source_b, "b"),
    ):
        for key, grouped in sorted(_group_by_key(entries).items()):
            if len(grouped) <= 1:
                continue
            med = grouped[0]
            drug = _display_name(med)
            discrepancies.append(
                _build_discrepancy(
                    "DUPLICATE",
                    med.normalized_name,
                    context,
                    seen_ids,
                    "moderate",
                    drug,
                    [drug],
                    med if side == "a" else None,
                    med if side == "b" else None,
                    f"{drug} appears {len(grouped)} times in {source_label}. This may indicate a transcription error or multiple prescribers.",
                    "Verify with prescriber whether duplicate entries represent different formulations or a transcription error.",
                )
            )

    for med_a in source_a:
        if _med_key(med_a) in matched_keys:
            continue
        for med_b in source_b:
            if _med_key(med_b) in matched_keys or _med_key(med_a) == _med_key(med_b):
                continue
            shared = _therapeutic_shared_classes(med_a, med_b)
            if not shared:
                continue
            class_name = sorted(shared)[0]
            drug_a = _display_name(med_a)
            drug_b = _display_name(med_b)
            discrepancies.append(
                _build_discrepancy(
                    "THERAPEUTIC_DUPLICATE",
                    f"{med_a.normalized_name}-{med_b.normalized_name}",
                    context,
                    seen_ids,
                    "low",
                    f"{drug_a} / {drug_b}",
                    [drug_a, drug_b],
                    med_a,
                    med_b,
                    (
                        f"{drug_a} ({context.source_a_label}) and {drug_b} ({context.source_b_label}) "
                        f"belong to the same class ({class_name}). One may have been substituted for the other."
                    ),
                    "Confirm with prescriber whether drug substitution was intended or both drugs should remain.",
                )
            )

    discrepancies.sort(
        key=lambda item: (
            _SORT_ORDER[item.discrepancy_type],
            _SEVERITY_ORDER[item.severity],
            item.drug_name.lower(),
            item.discrepancy_id,
        )
    )
    reconciled_count = sum(1 for key in matched_keys if key not in primary_discrepant_keys)

    return ReconciliationResult(
        patient_id=context.patient_id,
        source_a_label=context.source_a_label,
        source_b_label=context.source_b_label,
        discrepancies=discrepancies,
        drugs_in_source_a=len(source_a),
        drugs_in_source_b=len(source_b),
        reconciled_count=reconciled_count,
        assessment_date=datetime.now(timezone.utc).isoformat(),
        pharmacist_verification_notice=PHARMACIST_VERIFICATION_NOTICE,
    )


def _prepare_entry(med: MedEntry) -> MedEntry:
    normalized = normalize(med.normalized_name or med.drug_name)
    normalized = normalized or med.drug_name.strip().lower()
    return replace(med, normalized_name=normalized, classes=sorted(classes_of(normalized)))


def _group_by_key(meds: list[MedEntry]) -> dict[str, list[MedEntry]]:
    grouped: dict[str, list[MedEntry]] = defaultdict(list)
    for med in meds:
        grouped[_med_key(med)].append(med)
    return dict(grouped)


def _med_key(med: MedEntry) -> str:
    return med.normalized_name or normalize(med.drug_name) or med.drug_name.strip().lower()


def _display_name(med: MedEntry) -> str:
    return med.normalized_name or med.drug_name


def _canonical_value(value: str) -> str:
    return re.sub(r"\s+", "", value.strip().lower())


def _comparable_doses(med_a: MedEntry, med_b: MedEntry) -> tuple[str | None, str | None]:
    if med_a.strength and med_b.strength:
        return med_a.strength, med_b.strength
    if med_a.dose and med_b.dose:
        return med_a.dose, med_b.dose
    return None, None


def _has_any_class(med: MedEntry, targets: set[str]) -> bool:
    return bool(set(med.classes) & targets)


def _status_bucket(status: str | None) -> str | None:
    if not status:
        return None
    value = status.strip().lower()
    if value == "active":
        return "active"
    if value in {"inactive", "discontinued"}:
        return "inactive"
    return None


def _status_conflict(status_a: str | None, status_b: str | None) -> bool:
    buckets = {_status_bucket(status_a), _status_bucket(status_b)}
    return buckets == {"active", "inactive"}


def _therapeutic_shared_classes(med_a: MedEntry, med_b: MedEntry) -> set[str]:
    ignored = {
        "antihypertensive",
        "narrow_therapeutic_index",
    }
    return (set(med_a.classes) & set(med_b.classes)) - ignored


def _build_discrepancy(
    discrepancy_type: str,
    normalized_name: str,
    context: ReconciliationContext,
    seen_ids: Counter[str],
    severity: str,
    drug_name: str,
    drugs_involved: list[str],
    source_a_entry: MedEntry | None,
    source_b_entry: MedEntry | None,
    explanation: str,
    suggested_pharmacist_action: str,
) -> ReconciliationDiscrepancy:
    base_id = _discrepancy_id(discrepancy_type, normalized_name, context)
    seen_ids[base_id] += 1
    discrepancy_id = base_id if seen_ids[base_id] == 1 else f"{base_id}-{seen_ids[base_id]}"
    return ReconciliationDiscrepancy(
        discrepancy_id=discrepancy_id,
        discrepancy_type=discrepancy_type,
        severity=severity,
        source_a_label=context.source_a_label,
        source_b_label=context.source_b_label,
        drug_name=drug_name,
        drugs_involved=drugs_involved,
        source_a_entry=source_a_entry,
        source_b_entry=source_b_entry,
        explanation=explanation,
        suggested_pharmacist_action=suggested_pharmacist_action,
        confidence=1.0,
    )


def _discrepancy_id(discrepancy_type: str, normalized_name: str, context: ReconciliationContext) -> str:
    stem = "-".join(
        [
            discrepancy_type,
            normalized_name,
            context.source_a_label[:3],
            "vs",
            context.source_b_label[:3],
        ]
    )
    return re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")
