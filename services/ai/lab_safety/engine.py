from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

from services.ai.lab_safety.knowledge import DrugLabRule, LAB_RULES, LabThreshold
from services.ai.lab_safety.schema import (
    LabSafetyContext,
    LabSafetyFinding,
    LabSafetyResult,
    MissingLab,
    PHARMACIST_VERIFICATION_NOTICE,
)


SEVERITY_ORDER = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
RECENT_WINDOW_DAYS = 90


def _get(item: Any, field: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(field, default)
    return getattr(item, field, default)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean_key(value: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[_/-]+", " ", (value or "").lower())).strip()


def _key_variants(value: str | None) -> set[str]:
    cleaned = _clean_key(value)
    if not cleaned:
        return set()
    variants = {cleaned, cleaned.replace(" ", "_")}
    if cleaned.endswith("s"):
        variants.add(cleaned[:-1])
        variants.add(cleaned[:-1].replace(" ", "_"))
    else:
        variants.add(f"{cleaned}s")
    aliases = {
        "ace inhibitor": {"ace inhibitors", "ace_inhibitor"},
        "angiotensin receptor blocker": {"angiotensin receptor blockers", "arb"},
        "statin": {"statins"},
        "nsaid": {"nsaids"},
        "ssri": {"ssris", "serotonin reuptake inhibitor", "serotonin reuptake inhibitors"},
        "snri": {"snris", "serotonin reuptake inhibitor", "serotonin reuptake inhibitors"},
        "anticoagulant": {"anticoagulants"},
        "loop diuretic": {"loop diuretics"},
    }
    for key, mapped in aliases.items():
        if cleaned == key or cleaned in mapped:
            variants.update(mapped)
            variants.add(key)
    return variants


def _med_matches_rule(medication: Any, rule: DrugLabRule) -> bool:
    drug_name = str(_get(medication, "drug_name", "") or "")
    normalized_name = str(_get(medication, "normalized_name", "") or "")
    classes = _get(medication, "classes", []) or []
    normalized_candidates = _key_variants(normalized_name) | _key_variants(drug_name)
    for med_class in classes:
        normalized_candidates.update(_key_variants(str(med_class)))

    rule_keys = {_clean_key(key) for key in rule.drug_keys}
    rule_variants: set[str] = set()
    for key in rule.drug_keys:
        rule_variants.update(_key_variants(key))

    if normalized_candidates & (rule_keys | rule_variants):
        return True

    haystack = " ".join(sorted(normalized_candidates))
    return any(key and (key in haystack or haystack in key) for key in rule_keys)


def _lab_name_matches(test_name: str, aliases: list[str]) -> bool:
    cleaned = _clean_key(test_name)
    return any(_clean_key(alias) in cleaned for alias in aliases)


def _numeric_value(raw_value: Any) -> tuple[float | None, str | None]:
    raw = str(raw_value or "").strip()
    qualifier_match = re.match(r"^\s*([<>~])", raw)
    match = re.search(r"-?\d+(?:\.\d+)?", raw)
    if not match:
        return None, qualifier_match.group(1) if qualifier_match else None
    return float(match.group(0)), qualifier_match.group(1) if qualifier_match else None


def _threshold_triggered(value: float, qualifier: str | None, threshold: LabThreshold) -> bool:
    if threshold.lower_bound is not None and threshold.upper_bound is not None:
        return threshold.lower_bound <= value <= threshold.upper_bound
    if threshold.lower_bound is not None:
        if qualifier == "<":
            return value <= threshold.lower_bound
        return value < threshold.lower_bound
    if threshold.upper_bound is not None:
        if qualifier == ">":
            return value >= threshold.upper_bound
        return value > threshold.upper_bound
    return False


def _threshold_label(threshold: LabThreshold) -> str:
    return threshold.condition


def _most_recent_lab(
    lab_results: list[Any],
    aliases: list[str],
    now: datetime,
) -> tuple[Any | None, str | None]:
    matching: list[tuple[datetime, Any]] = []
    for lab in lab_results:
        test_name = str(_get(lab, "test_name", "") or "")
        if not _lab_name_matches(test_name, aliases):
            continue
        result_date = _as_datetime(_get(lab, "result_date"))
        if result_date is None:
            continue
        matching.append((result_date, lab))

    if not matching:
        return None, "no result on record"

    matching.sort(key=lambda item: item[0], reverse=True)
    most_recent_date, most_recent_lab = matching[0]
    if most_recent_date < now - timedelta(days=RECENT_WINDOW_DAYS):
        return None, "no result in last 90 days"
    return most_recent_lab, None


def _aliases_for_required_lab(rule: DrugLabRule, lab_name: str) -> list[str]:
    aliases: list[str] = []
    for threshold in rule.lab_thresholds:
        if threshold.lab_name == lab_name:
            aliases.extend(threshold.aliases)
    return sorted(set(aliases))


def _finding_sort_key(finding: LabSafetyFinding) -> tuple[int, str, str, str]:
    return (
        SEVERITY_ORDER.get(finding.severity, 99),
        finding.drug.lower(),
        finding.rule_id,
        finding.lab_name,
    )


def _newer_or_more_severe(
    candidate: LabSafetyFinding,
    existing: LabSafetyFinding,
) -> bool:
    candidate_severity = SEVERITY_ORDER.get(candidate.severity, 99)
    existing_severity = SEVERITY_ORDER.get(existing.severity, 99)
    if candidate_severity != existing_severity:
        return candidate_severity < existing_severity
    return candidate.lab_date > existing.lab_date


def assess(context: LabSafetyContext) -> LabSafetyResult:
    now = datetime.now(timezone.utc)
    findings_by_key: dict[tuple[str, str, str], LabSafetyFinding] = {}
    missing_labs: dict[tuple[str, str, str], MissingLab] = {}
    drugs_evaluated: list[str] = []
    labs_evaluated: set[str] = set()

    for medication in context.medications:
        drug_name = str(_get(medication, "drug_name", "") or _get(medication, "normalized_name", "") or "").strip()
        if not drug_name:
            continue

        matched_rules = [rule for rule in LAB_RULES if _med_matches_rule(medication, rule)]
        if matched_rules and drug_name not in drugs_evaluated:
            drugs_evaluated.append(drug_name)

        for rule in matched_rules:
            found_labs_for_rule: set[str] = set()
            for threshold in rule.lab_thresholds:
                lab, _ = _most_recent_lab(context.lab_results, threshold.aliases, now)
                if lab is None:
                    continue

                found_labs_for_rule.add(threshold.lab_name)
                labs_evaluated.add(threshold.lab_name)
                numeric, qualifier = _numeric_value(_get(lab, "value"))
                if numeric is None or not _threshold_triggered(numeric, qualifier, threshold):
                    continue

                result_date = _as_datetime(_get(lab, "result_date"))
                lab_date = result_date.date().isoformat() if result_date else ""
                explanation = threshold.explanation
                if qualifier:
                    explanation = f"{explanation} Lab value included qualifier '{qualifier}'; numeric portion was used for deterministic threshold evaluation."

                finding = LabSafetyFinding(
                    rule_id=rule.rule_id,
                    severity=threshold.severity,
                    drug=drug_name,
                    lab_name=threshold.lab_name,
                    lab_value=str(_get(lab, "value", "")),
                    lab_unit=_get(lab, "unit") or threshold.unit_hint or None,
                    lab_date=lab_date,
                    threshold_triggered=_threshold_label(threshold),
                    explanation=explanation,
                    suggested_pharmacist_action=threshold.suggested_action,
                    evidence_source=threshold.evidence_source,
                    confidence=threshold.confidence,
                )
                key = (rule.rule_id, drug_name.lower(), threshold.lab_name)
                existing = findings_by_key.get(key)
                if existing is None or _newer_or_more_severe(finding, existing):
                    findings_by_key[key] = finding

            for required_lab in rule.required_labs:
                aliases = _aliases_for_required_lab(rule, required_lab)
                lab, reason = _most_recent_lab(context.lab_results, aliases, now)
                if lab is not None:
                    found_labs_for_rule.add(required_lab)
                    labs_evaluated.add(required_lab)
                    continue
                if required_lab not in found_labs_for_rule:
                    key = (rule.rule_id, drug_name.lower(), required_lab)
                    missing_labs[key] = MissingLab(
                        drug=drug_name,
                        lab_name=required_lab,
                        reason=reason or "no result on record",
                    )

    findings = sorted(findings_by_key.values(), key=_finding_sort_key)
    return LabSafetyResult(
        patient_id=str(context.patient_id),
        findings=findings,
        missing_labs=sorted(missing_labs.values(), key=lambda item: (item.drug.lower(), item.lab_name)),
        drugs_evaluated=drugs_evaluated,
        labs_evaluated=sorted(labs_evaluated),
        assessment_date=now.isoformat(),
        pharmacist_verification_notice=PHARMACIST_VERIFICATION_NOTICE,
    )
