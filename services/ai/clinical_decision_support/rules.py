from __future__ import annotations

from collections.abc import Iterable

from .schema import AlertDict, CDSContext, CDSMedication, PHARMACIST_VERIFICATION_NOTICE, Severity

EVIDENCE_SIMVASTATIN_MACROLIDE = "FDA label: simvastatin"
EVIDENCE_ACEI_SPIRONOLACTONE = "Lexicomp interaction monograph: ACE inhibitors + potassium-sparing diuretics"
EVIDENCE_NSAID_ANTICOAG = "Lexicomp interaction monograph: NSAIDs + anticoagulants"
EVIDENCE_METFORMIN_EGFR = "FDA Drug Safety Communication: metformin-containing medicines and reduced kidney function"
EVIDENCE_BEERS_2023 = "AGS Beers Criteria 2023"
EVIDENCE_SSRI_TRAMADOL = "Lexicomp interaction monograph: tramadol + selective serotonin reuptake inhibitors"

POTASSIUM_MISSING = "serum potassium (most recent) — not on file"
EGFR_MISSING = "eGFR (most recent) — not on file"
AGE_MISSING = "patient age/date of birth — not available"


def _alert(
    *,
    rule_id: str,
    severity: Severity,
    title: str,
    clinical_problem: str,
    mechanism: str,
    patient_specific_factors: list[str] | None = None,
    missing_information: list[str] | None = None,
    suggested_pharmacist_actions: list[str],
    evidence_sources: list[str],
    confidence: float,
) -> AlertDict:
    return {
        "rule_id": rule_id,
        "severity": severity.value,
        "title": title,
        "clinical_problem": clinical_problem,
        "mechanism": mechanism,
        "patient_specific_factors": patient_specific_factors or [],
        "missing_information": missing_information or [],
        "suggested_pharmacist_actions": suggested_pharmacist_actions,
        "evidence_sources": evidence_sources,
        "confidence": confidence,
        "pharmacist_verification_notice": PHARMACIST_VERIFICATION_NOTICE,
    }


def _meds_with_class(context: CDSContext, class_name: str) -> list[CDSMedication]:
    return [med for med in context.medications if class_name in med.classes]


def _has_name(context: CDSContext, normalized_name: str) -> bool:
    return any(med.normalized_name == normalized_name for med in context.medications)


def _names(meds: Iterable[CDSMedication]) -> str:
    names = sorted({med.normalized_name or med.drug_name for med in meds})
    return ", ".join(names)


def _latest_lab(context: CDSContext, candidates: tuple[str, ...]):
    for key in candidates:
        lab = context.labs.get(key)
        if lab:
            return lab
    for key, lab in context.labs.items():
        lowered = key.lower()
        if any(candidate in lowered for candidate in candidates):
            return lab
    return None


def clarithromycin_simvastatin(context: CDSContext) -> list[AlertDict]:
    if not (_has_name(context, "clarithromycin") and _has_name(context, "simvastatin")):
        return []
    return [_alert(
        rule_id="clarithromycin_simvastatin",
        severity=Severity.CRITICAL,
        title="Clarithromycin with simvastatin",
        clinical_problem="High-risk interaction with increased simvastatin toxicity risk.",
        mechanism="Clarithromycin is a strong CYP3A4 inhibitor, increasing simvastatin exposure and risk for myopathy or rhabdomyolysis.",
        patient_specific_factors=["Active medication list includes clarithromycin and simvastatin."],
        suggested_pharmacist_actions=[
            "Hold simvastatin during the macrolide course or discuss azithromycin as an alternative with the prescriber.",
            "Counsel the patient to report muscle pain, weakness, or dark urine promptly.",
        ],
        evidence_sources=[EVIDENCE_SIMVASTATIN_MACROLIDE, "Lexicomp interaction monograph: clarithromycin + simvastatin"],
        confidence=0.97,
    )]


def acei_spironolactone_potassium(context: CDSContext) -> list[AlertDict]:
    acei = _meds_with_class(context, "ace_inhibitor")
    potassium_sparing = _meds_with_class(context, "potassium_sparing_diuretic")
    if not acei or not potassium_sparing:
        return []

    lab = _latest_lab(context, ("serum potassium", "potassium", "k"))
    severity = Severity.MODERATE
    missing: list[str] = []
    factors = [f"Active medication list includes ACE inhibitor ({_names(acei)}) and potassium-sparing diuretic ({_names(potassium_sparing)})."]
    if lab is None or lab.value is None:
        missing.append(POTASSIUM_MISSING)
    else:
        factors.append(f"Most recent serum potassium: {lab.value:g}{' ' + lab.unit if lab.unit else ''}.")
        if lab.value >= 5.5:
            severity = Severity.CRITICAL
        elif lab.value >= 5.0:
            severity = Severity.HIGH

    return [_alert(
        rule_id="acei_spironolactone_potassium",
        severity=severity,
        title="ACE inhibitor with spironolactone/eplerenone",
        clinical_problem="Potential hyperkalemia with additive potassium retention.",
        mechanism="ACE inhibitors reduce aldosterone-mediated potassium excretion; spironolactone/eplerenone further retain potassium.",
        patient_specific_factors=factors,
        missing_information=missing,
        suggested_pharmacist_actions=[
            "Obtain or recheck serum potassium and renal function.",
            "Review dose and ongoing need with the prescriber when potassium is elevated or monitoring is absent.",
        ],
        evidence_sources=[EVIDENCE_ACEI_SPIRONOLACTONE],
        confidence=0.93 if not missing else 0.88,
    )]


def nsaid_anticoagulant(context: CDSContext) -> list[AlertDict]:
    nsaids = _meds_with_class(context, "nsaid")
    anticoagulants = _meds_with_class(context, "anticoagulant")
    if not nsaids or not anticoagulants:
        return []
    return [_alert(
        rule_id="nsaid_anticoagulant",
        severity=Severity.HIGH,
        title="NSAID with anticoagulant",
        clinical_problem="Increased bleeding risk, especially gastrointestinal bleeding.",
        mechanism="NSAID-related gastrointestinal mucosal injury and platelet effects add to anticoagulant bleeding risk.",
        patient_specific_factors=[f"Active medication list includes NSAID ({_names(nsaids)}) and anticoagulant ({_names(anticoagulants)})."],
        suggested_pharmacist_actions=[
            "Assess GI bleed risk and duration of NSAID use.",
            "Consider gastroprotection or an alternative analgesic with the prescriber.",
            "Counsel on bleeding signs such as black stools, unusual bruising, or prolonged bleeding.",
        ],
        evidence_sources=[EVIDENCE_NSAID_ANTICOAG],
        confidence=0.92,
    )]


def metformin_low_egfr(context: CDSContext) -> list[AlertDict]:
    if not _meds_with_class(context, "biguanide"):
        return []
    lab = _latest_lab(context, ("egfr", "estimated glomerular filtration rate"))
    severity = Severity.MODERATE
    missing: list[str] = []
    factors = ["Active medication list includes metformin."]
    if lab is None or lab.value is None:
        missing.append(EGFR_MISSING)
    else:
        factors.append(f"Most recent eGFR: {lab.value:g}{' ' + lab.unit if lab.unit else ''}.")
        if lab.value < 30:
            severity = Severity.CRITICAL
        elif 30 <= lab.value <= 44:
            severity = Severity.HIGH
        else:
            return []

    return [_alert(
        rule_id="metformin_low_egfr",
        severity=severity,
        title="Metformin with reduced or missing eGFR",
        clinical_problem="Potential metformin accumulation and lactic acidosis risk when renal clearance is reduced.",
        mechanism="Reduced kidney function decreases metformin clearance, increasing risk for metformin-associated lactic acidosis.",
        patient_specific_factors=factors,
        missing_information=missing,
        suggested_pharmacist_actions=[
            "Verify the most recent eGFR before initiation or continuation decisions.",
            "For eGFR below 30, contact the prescriber because metformin is contraindicated.",
            "For eGFR 30-44, review initiation/continuation and dose reduction with the prescriber.",
        ],
        evidence_sources=[EVIDENCE_METFORMIN_EGFR],
        confidence=0.95 if not missing else 0.86,
    )]


def benzodiazepine_elderly(context: CDSContext) -> list[AlertDict]:
    benzos = _meds_with_class(context, "benzodiazepine")
    if not benzos:
        return []
    if context.age is None:
        return [_alert(
            rule_id="benzodiazepine_elderly",
            severity=Severity.INFO,
            title="Age needed for benzodiazepine geriatric screening",
            clinical_problem="Benzodiazepine risk in older adults cannot be assessed without age/date of birth.",
            mechanism="The geriatric benzodiazepine safety rule applies at age 65 or older.",
            patient_specific_factors=[f"Active medication list includes benzodiazepine ({_names(benzos)})."],
            missing_information=[AGE_MISSING],
            suggested_pharmacist_actions=["Verify patient age/date of birth before applying geriatric benzodiazepine criteria."],
            evidence_sources=[EVIDENCE_BEERS_2023],
            confidence=0.75,
        )]
    if context.age < 65:
        return []
    return [_alert(
        rule_id="benzodiazepine_elderly",
        severity=Severity.HIGH,
        title="Benzodiazepine in older adult",
        clinical_problem="Potentially inappropriate benzodiazepine exposure in an older adult.",
        mechanism="Benzodiazepines increase risk of falls, fractures, cognitive impairment, delirium, and motor vehicle crashes in older adults.",
        patient_specific_factors=[f"Patient age: {context.age}.", f"Active medication list includes benzodiazepine ({_names(benzos)})."],
        suggested_pharmacist_actions=[
            "Review necessity and duration with the prescriber.",
            "If discontinuing is appropriate, consider a taper plan; never abruptly stop chronic benzodiazepine therapy.",
            "Counsel on sedation and fall-risk precautions.",
        ],
        evidence_sources=[EVIDENCE_BEERS_2023, "STOPP/START v2"],
        confidence=0.91,
    )]


def ssri_tramadol(context: CDSContext) -> list[AlertDict]:
    ssris = _meds_with_class(context, "ssri")
    tramadol = _meds_with_class(context, "serotonergic_opioid")
    if not ssris or not tramadol:
        return []
    return [_alert(
        rule_id="ssri_tramadol",
        severity=Severity.HIGH,
        title="SSRI with tramadol",
        clinical_problem="Increased serotonin syndrome and seizure risk.",
        mechanism="SSRIs and tramadol have additive serotonergic effects; tramadol also lowers the seizure threshold.",
        patient_specific_factors=[f"Active medication list includes SSRI ({_names(ssris)}) and tramadol."],
        suggested_pharmacist_actions=[
            "Assess for serotonin-syndrome symptoms such as agitation, tremor, sweating, diarrhea, or fever.",
            "Discuss an alternative analgesic with the prescriber when clinically appropriate.",
            "Counsel the patient to seek care for severe serotonin toxicity symptoms.",
        ],
        evidence_sources=[EVIDENCE_SSRI_TRAMADOL],
        confidence=0.90,
    )]


ALL_RULES = [
    clarithromycin_simvastatin,
    acei_spironolactone_potassium,
    nsaid_anticoagulant,
    metformin_low_egfr,
    benzodiazepine_elderly,
    ssri_tramadol,
]
