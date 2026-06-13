from __future__ import annotations

from services.ai.clinical_decision_support.normalizer import BRAND_TO_GENERIC, classes_of, normalize
from services.ai.counselling.schema import CounsellingFacts


def _facts(
    what_for: str,
    how_to_take: str,
    what_to_avoid: list[str],
    common_side_effects: list[str],
    serious_red_flags: list[str],
    missed_dose: str,
    adherence_tips: list[str],
    evidence_source: str,
) -> CounsellingFacts:
    return CounsellingFacts(
        what_for=what_for,
        how_to_take=how_to_take,
        what_to_avoid=what_to_avoid,
        common_side_effects=common_side_effects,
        serious_red_flags=serious_red_flags,
        missed_dose=missed_dose,
        adherence_tips=adherence_tips,
        evidence_source=evidence_source,
    )


AMOXICILLIN = _facts(
    "Antibiotic used for susceptible bacterial infections.",
    "Take exactly as prescribed and complete the prescribed course unless the prescriber changes the plan.",
    ["Sharing antibiotics", "Using leftover antibiotics", "Missing multiple doses"],
    ["Nausea", "Diarrhea", "Rash"],
    ["Trouble breathing or swelling of the face, lips, tongue, or throat", "Severe watery or bloody diarrhea", "Widespread rash or blistering"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Use the same schedule each day", "Finish the full prescribed course", "Contact the pharmacy if doses are missed repeatedly"],
    "FDA label: amoxicillin; MedlinePlus: amoxicillin",
)

ACE_INHIBITOR = _facts(
    "ACE inhibitor used to treat high blood pressure and to protect the heart or kidneys in some patients.",
    "Take once daily or as prescribed, at the same time each day.",
    ["Salt substitutes containing potassium unless approved", "Pregnancy exposure", "Dehydration without clinician guidance"],
    ["Dizziness", "Dry cough", "Tiredness"],
    ["Swelling of the face, lips, tongue, or throat", "Fainting", "Signs of high potassium such as severe weakness or irregular heartbeat"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Rise slowly from sitting", "Keep blood pressure and lab appointments", "Tell the pharmacist about potassium supplements"],
    "FDA label: lisinopril; ACC/AHA hypertension guideline",
)

STATIN = _facts(
    "Statin used to lower cholesterol and reduce cardiovascular risk.",
    "Take as prescribed; some statins are taken in the evening and others can be taken any time of day.",
    ["Grapefruit products when the pharmacist says they interact", "Heavy alcohol use without clinician guidance", "Pregnancy exposure"],
    ["Muscle aches", "Headache", "Stomach upset"],
    ["Severe muscle pain or weakness", "Dark brown urine", "Yellowing skin or eyes"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Use a consistent daily routine", "Keep cholesterol and liver lab follow-up when ordered", "Report new severe muscle symptoms promptly"],
    "FDA label: atorvastatin; FDA label: simvastatin; ACC/AHA cholesterol guideline",
)

CCB = _facts(
    "Calcium channel blocker used to treat high blood pressure or chest pain.",
    "Take as prescribed at the same time each day.",
    ["Grapefruit products unless the pharmacist says they are safe", "Sudden position changes if dizzy"],
    ["Ankle swelling", "Flushing", "Dizziness"],
    ["Fainting", "Very fast or very slow heartbeat", "Shortness of breath with swelling"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Check blood pressure as directed", "Report bothersome swelling", "Stand up slowly if lightheaded"],
    "FDA label: amlodipine; ACC/AHA hypertension guideline",
)

PPI = _facts(
    "Proton pump inhibitor used to reduce stomach acid and help treat reflux, ulcers, or acid-related symptoms.",
    "Take as prescribed; many doses work best before a meal.",
    ["Taking longer than directed without follow-up", "Ignoring black stools or vomiting blood"],
    ["Headache", "Nausea", "Stomach pain"],
    ["Severe or persistent diarrhea", "Black stools or vomiting blood", "Chest pain that does not feel like usual heartburn"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Use the timing recommended on the prescription label", "Discuss ongoing symptoms with the pharmacist", "Keep follow-up if long-term therapy is planned"],
    "FDA label: omeprazole; MedlinePlus: omeprazole",
)

BETA_BLOCKER = _facts(
    "Beta blocker used for blood pressure, heart rate control, chest pain, or heart protection in some patients.",
    "Take as prescribed at the same time each day; some forms should be taken with or right after food.",
    ["Running out before discussing the plan with the prescriber", "New over-the-counter cold medicines without pharmacist review"],
    ["Tiredness", "Dizziness", "Slow heartbeat"],
    ["Fainting", "Trouble breathing or wheezing", "Very slow heartbeat with weakness or confusion"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Track pulse or blood pressure if instructed", "Rise slowly from sitting", "Refill before running out"],
    "FDA label: metoprolol; ACC/AHA hypertension guideline",
)

WARFARIN = _facts(
    "Anticoagulant used to lower the chance of harmful blood clots.",
    "Take exactly as prescribed and keep INR blood test appointments.",
    ["Major changes in vitamin K foods without clinician guidance", "Aspirin or NSAIDs unless approved", "Alcohol binges"],
    ["Easy bruising", "Minor bleeding", "Nausea"],
    ["Bleeding that will not stop", "Black or bloody stools", "Vomiting blood or material that looks like coffee grounds", "Severe headache, dizziness, weakness, or a fall with head injury"],
    "If a dose is missed, follow the anticoagulation clinic or prescriber instructions; do not take extra doses unless instructed by the care team.",
    ["Use a consistent daily dosing time", "Keep INR appointments", "Tell the pharmacist before any new prescription, OTC medicine, or supplement"],
    "FDA label: warfarin; CHEST antithrombotic therapy guideline",
)

METFORMIN = _facts(
    "Biguanide used to help control blood sugar in type 2 diabetes.",
    "Take with meals as prescribed to reduce stomach upset.",
    ["Heavy alcohol use", "Dehydration without clinician guidance", "Ignoring kidney-function lab follow-up"],
    ["Diarrhea", "Nausea", "Gas or stomach upset"],
    ["Extreme weakness or unusual sleepiness", "Trouble breathing", "Severe stomach pain with vomiting"],
    "If a dose is missed, take it with food when remembered unless it is close to the next dose; do not take extra doses.",
    ["Take with a meal", "Keep kidney lab follow-up", "Ask about extended-release options if stomach effects persist"],
    "FDA label: metformin; ADA Standards of Care in Diabetes",
)

NSAID = _facts(
    "NSAID used for pain, fever, or inflammation.",
    "Take as directed on the label or prescription, preferably with food or milk if stomach upset occurs.",
    ["Taking multiple NSAIDs together", "Using with blood thinners unless approved", "Using late in pregnancy"],
    ["Stomach upset", "Heartburn", "Dizziness"],
    ["Chest pain or shortness of breath", "Black or bloody stools", "Vomiting blood", "Swelling of the face or throat"],
    "If a dose is missed, take it when remembered if still needed and not close to the next dose; do not take extra doses.",
    ["Use the lowest effective amount for the shortest intended time", "Ask before combining with blood thinners or steroids", "Check labels because many cold products contain pain relievers"],
    "FDA label: ibuprofen; FDA label: naproxen",
)

SSRI = _facts(
    "SSRI antidepressant used for depression, anxiety, or related conditions.",
    "Take as prescribed at the same time each day; benefits may take several weeks.",
    ["Alcohol or sedatives without pharmacist review", "St. John's wort", "MAO inhibitors unless specifically managed by the prescriber"],
    ["Nausea", "Headache", "Sleep changes", "Sexual side effects"],
    ["Thoughts of self-harm", "Severe agitation, fever, sweating, confusion, or muscle stiffness", "Seizure", "Unusual bleeding"],
    "If a dose is missed, take it when remembered unless it is close to the next dose; do not take extra doses.",
    ["Take consistently each day", "Keep follow-up appointments", "Tell the pharmacist about mood worsening or severe restlessness"],
    "FDA label: sertraline; FDA label: fluoxetine; FDA antidepressant boxed warning",
)

BENZODIAZEPINE = _facts(
    "Benzodiazepine used short term for anxiety, panic, sleep, or seizure-related indications depending on the prescription.",
    "Take only as prescribed because it can cause sedation and dependence.",
    ["Alcohol", "Opioids unless specifically managed by the prescriber", "Driving or hazardous tasks until effects are known"],
    ["Drowsiness", "Dizziness", "Poor coordination", "Memory problems"],
    ["Severe sleepiness or trouble waking", "Slow or difficult breathing", "Confusion, falls, or injury"],
    "If a dose is missed, follow the prescription directions; do not take extra doses.",
    ["Use one prescriber and pharmacy when possible", "Store securely", "Discuss daytime sedation or falls with the pharmacist"],
    "FDA label: alprazolam; FDA label: lorazepam; FDA benzodiazepine boxed warning",
)

TRAMADOL = _facts(
    "Opioid pain medicine used for pain when prescribed.",
    "Take exactly as prescribed because it can cause sedation, dependence, and breathing problems.",
    ["Alcohol", "Other sedatives unless specifically approved", "Driving or hazardous tasks until effects are known"],
    ["Nausea", "Constipation", "Dizziness", "Sleepiness"],
    ["Slow or difficult breathing", "Severe sleepiness or trouble waking", "Seizure", "Severe agitation, fever, sweating, confusion, or muscle stiffness"],
    "If a dose is missed, take it when remembered if pain medicine is still needed and not close to the next dose; do not take extra doses.",
    ["Store securely", "Ask about constipation prevention", "Tell the pharmacist about antidepressants, seizure history, or other sedatives"],
    "FDA label: tramadol; FDA opioid analgesic boxed warning",
)


COUNSELLING: dict[str, CounsellingFacts] = {
    "amoxicillin": AMOXICILLIN,
    "lisinopril": ACE_INHIBITOR,
    "atorvastatin": STATIN,
    "simvastatin": STATIN,
    "amlodipine": CCB,
    "omeprazole": PPI,
    "metoprolol": BETA_BLOCKER,
    "warfarin": WARFARIN,
    "metformin": METFORMIN,
    "ibuprofen": NSAID,
    "naproxen": NSAID,
    "sertraline": SSRI,
    "fluoxetine": SSRI,
    "alprazolam": BENZODIAZEPINE,
    "lorazepam": BENZODIAZEPINE,
    "tramadol": TRAMADOL,
}

CLASS_FALLBACKS: dict[str, CounsellingFacts] = {
    "ace_inhibitor": ACE_INHIBITOR,
    "statin": STATIN,
    "dihydropyridine_ccb": CCB,
    "calcium_channel_blocker": CCB,
    "ppi": PPI,
    "proton_pump_inhibitor": PPI,
    "beta_blocker": BETA_BLOCKER,
    "anticoagulant": WARFARIN,
    "biguanide": METFORMIN,
    "nsaid": NSAID,
    "ssri": SSRI,
    "benzodiazepine": BENZODIAZEPINE,
    "serotonergic_opioid": TRAMADOL,
}

ADDITIONAL_CLASS_OVERRIDES: dict[str, set[str]] = {
    "amoxicillin": {"penicillin_antibiotic"},
    "nifedipine": {"dihydropyridine_ccb"},
    "felodipine": {"dihydropyridine_ccb"},
    "omeprazole": {"ppi", "proton_pump_inhibitor"},
    "pantoprazole": {"ppi", "proton_pump_inhibitor"},
    "esomeprazole": {"ppi", "proton_pump_inhibitor"},
    "lansoprazole": {"ppi", "proton_pump_inhibitor"},
    "atenolol": {"beta_blocker"},
    "carvedilol": {"beta_blocker"},
    "propranolol": {"beta_blocker"},
}

for _name in ("rosuvastatin", "pravastatin", "lovastatin", "fluvastatin", "pitavastatin"):
    ADDITIONAL_CLASS_OVERRIDES.setdefault(_name, set()).add("statin")


def resolve_facts(drug_name: str | None) -> tuple[str, CounsellingFacts | None]:
    normalized = normalize(drug_name)
    if not normalized:
        return "", None
    if normalized in COUNSELLING:
        return normalized, COUNSELLING[normalized]
    for class_name in sorted(_classes_for(normalized)):
        if class_name in CLASS_FALLBACKS:
            return normalized, CLASS_FALLBACKS[class_name]
    return normalized, None


def _classes_for(normalized: str) -> set[str]:
    classes = set(classes_of(normalized)) | ADDITIONAL_CLASS_OVERRIDES.get(normalized, set())
    if normalized.endswith("statin"):
        classes.add("statin")
    if normalized.endswith("prazole"):
        classes.update({"ppi", "proton_pump_inhibitor"})
    if normalized.endswith("pril"):
        classes.add("ace_inhibitor")
    if normalized.endswith("olol"):
        classes.add("beta_blocker")
    return classes


KNOWN_DRUG_NAMES: set[str] = (
    set(COUNSELLING)
    | set(BRAND_TO_GENERIC)
    | set(BRAND_TO_GENERIC.values())
    | set(ADDITIONAL_CLASS_OVERRIDES)
    | {
        "rosuvastatin",
        "pravastatin",
        "lovastatin",
        "fluvastatin",
        "pitavastatin",
        "pantoprazole",
        "esomeprazole",
        "lansoprazole",
        "nifedipine",
        "atenolol",
        "carvedilol",
        "propranolol",
        "apixaban",
        "rivaroxaban",
        "dabigatran",
        "losartan",
        "valsartan",
        "olmesartan",
        "aspirin",
        "clopidogrel",
        "diazepam",
        "clonazepam",
        "escitalopram",
        "paroxetine",
        "citalopram",
    }
)
