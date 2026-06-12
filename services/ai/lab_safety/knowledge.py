from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LabThreshold:
    lab_name: str
    aliases: list[str]
    unit_hint: str
    severity: str
    condition: str
    lower_bound: float | None
    upper_bound: float | None
    explanation: str
    suggested_action: str
    evidence_source: str
    confidence: float


@dataclass(frozen=True)
class DrugLabRule:
    rule_id: str
    drug_keys: list[str]
    lab_thresholds: list[LabThreshold]
    required_labs: list[str]


EGFR_ALIASES = ["egfr", "gfr", "estimated gfr", "ckd-epi", "mdrd", "creatinine clearance", "crcl"]
POTASSIUM_ALIASES = ["potassium", "serum potassium", "k+", "k,serum"]
SODIUM_ALIASES = ["sodium", "serum sodium", "na,serum", "na+"]
TSH_ALIASES = ["tsh", "thyroid stimulating hormone", "thyroid function"]
ALT_ALIASES = ["alt", "alanine aminotransferase", "sgpt", "liver enzymes", "lft"]


LAB_RULES: list[DrugLabRule] = [
    DrugLabRule(
        rule_id="warfarin_inr",
        drug_keys=["warfarin"],
        lab_thresholds=[
            LabThreshold("inr", ["inr", "international normalized ratio", "pt/inr", "prothrombin"], "", "high", "INR > 3.0", None, 3.0, "Supratherapeutic INR - bleeding risk elevated", "Notify prescriber immediately; assess for bleeding signs; consider dose hold per protocol", "ACC/AHA Anticoagulation Guideline 2023; warfarin labeling", 0.95),
            LabThreshold("inr", ["inr", "international normalized ratio", "pt/inr", "prothrombin"], "", "critical", "INR > 5.0", None, 5.0, "Critically supratherapeutic INR - serious bleeding risk", "Urgent prescriber notification; do NOT dispense next dose without physician review; evaluate for reversal", "ACC/AHA Anticoagulation Guideline 2023; warfarin labeling", 0.97),
            LabThreshold("inr", ["inr", "international normalized ratio", "pt/inr", "prothrombin"], "", "moderate", "INR < 2.0", 2.0, None, "Subtherapeutic INR - thrombotic risk", "Notify prescriber; review adherence, diet (vitamin K), interacting drugs", "ACC/AHA Anticoagulation Guideline 2023; warfarin labeling", 0.94),
        ],
        required_labs=["inr"],
    ),
    DrugLabRule(
        rule_id="acei_arb_hyperkalemia",
        drug_keys=["ace inhibitors", "ace inhibitor", "ace_inhibitor", "angiotensin receptor blockers", "angiotensin receptor blocker", "arb", "lisinopril", "enalapril", "ramipril", "perindopril", "captopril", "benazepril", "fosinopril", "quinapril", "trandolapril", "losartan", "valsartan", "irbesartan", "olmesartan", "candesartan", "telmisartan", "azilsartan", "eprosartan", "sacubitril-valsartan"],
        lab_thresholds=[
            LabThreshold("potassium", POTASSIUM_ALIASES, "mEq/L", "high", "K+ > 5.5 mEq/L", None, 5.5, "Hyperkalemia - ACE inhibitor/ARB can impair renal potassium excretion", "Notify prescriber; review potassium-sparing drugs and diet; consider dose reduction or potassium restriction", "Lexicomp: lisinopril/losartan - electrolyte monitoring; KDIGO 2023", 0.93),
            LabThreshold("potassium", POTASSIUM_ALIASES, "mEq/L", "critical", "K+ > 6.0 mEq/L", None, 6.0, "Severe hyperkalemia - cardiac arrhythmia risk", "Urgent prescriber notification; do not dispense ACE inhibitor/ARB until potassium reviewed by physician", "Lexicomp: lisinopril/losartan - electrolyte monitoring; KDIGO 2023", 0.96),
        ],
        required_labs=["potassium"],
    ),
    DrugLabRule(
        rule_id="metformin_renal",
        drug_keys=["metformin"],
        lab_thresholds=[
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "high", "eGFR < 30 mL/min/1.73m2", 30.0, None, "eGFR below 30 - metformin contraindicated; lactic acidosis risk", "Notify prescriber; metformin should be discontinued per FDA labeling until eGFR reviewed", "FDA metformin label 2023; AACE/ADA diabetes guidelines", 0.95),
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "moderate", "eGFR 30-45 mL/min/1.73m2", 30.0, 45.0, "eGFR 30-45 - metformin dose may need reduction; monitor closely", "Flag for prescriber review; confirm dose is halved and patient is monitoring renal function every 3 months", "FDA metformin label 2023; AACE/ADA diabetes guidelines", 0.92),
        ],
        required_labs=["egfr"],
    ),
    DrugLabRule(
        rule_id="digoxin_renal",
        drug_keys=["digoxin"],
        lab_thresholds=[
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "high", "eGFR < 30", 30.0, None, "Severely reduced renal clearance - digoxin accumulation risk; narrow therapeutic index", "Notify prescriber; consider dose reduction/extended interval and digoxin level monitoring", "Lexicomp: digoxin renal dosing; AHA Heart Failure Guideline 2022", 0.92),
            LabThreshold("digoxin level", ["digoxin", "digoxin level", "dig level"], "ng/mL", "critical", "digoxin level > 2.0 ng/mL", None, 2.0, "Supratherapeutic digoxin level - toxicity risk (nausea, arrhythmia, visual disturbance)", "Urgent prescriber notification; hold dose; assess for digoxin toxicity symptoms", "Lexicomp: digoxin renal dosing; AHA Heart Failure Guideline 2022", 0.96),
            LabThreshold("digoxin level", ["digoxin", "digoxin level", "dig level"], "ng/mL", "moderate", "digoxin level > 1.5 ng/mL", None, 1.5, "Elevated digoxin level - consider if patient is elderly or has renal impairment", "Verify recent renal function; flag for prescriber if level trending up", "Lexicomp: digoxin renal dosing; AHA Heart Failure Guideline 2022", 0.88),
        ],
        required_labs=["egfr", "digoxin level"],
    ),
    DrugLabRule(
        rule_id="lithium_monitoring",
        drug_keys=["lithium"],
        lab_thresholds=[
            LabThreshold("lithium level", ["lithium", "lithium level", "li,serum", "serum lithium"], "mEq/L", "critical", "lithium level > 1.5 mEq/L", None, 1.5, "Lithium level above therapeutic range - toxicity risk (tremor, confusion, arrhythmia)", "Urgent prescriber notification; hold dose; assess for lithium toxicity; check renal function", "APA Lithium Monitoring Guideline; lithium labeling", 0.96),
            LabThreshold("lithium level", ["lithium", "lithium level", "li,serum", "serum lithium"], "mEq/L", "high", "lithium level > 1.2 mEq/L", None, 1.2, "Lithium approaching toxic range - monitor closely", "Notify prescriber; confirm patient hydration status and interacting NSAIDs/diuretics", "APA Lithium Monitoring Guideline; lithium labeling", 0.93),
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "moderate", "eGFR < 45", 45.0, None, "Reduced renal clearance - lithium accumulation risk", "Notify prescriber; lithium dose likely needs reduction; check lithium level", "APA Lithium Monitoring Guideline; lithium labeling", 0.9),
            LabThreshold("tsh", TSH_ALIASES, "mIU/L", "moderate", "TSH > 4.5 mIU/L", None, 4.5, "Elevated TSH - lithium-induced hypothyroidism", "Notify prescriber; thyroid function monitoring required; consider endocrinology referral", "APA Lithium Monitoring Guideline; lithium labeling", 0.88),
        ],
        required_labs=["lithium level", "egfr", "tsh"],
    ),
    DrugLabRule(
        rule_id="amiodarone_thyroid_liver",
        drug_keys=["amiodarone"],
        lab_thresholds=[
            LabThreshold("tsh", TSH_ALIASES, "mIU/L", "high", "TSH < 0.1 mIU/L", 0.1, None, "Suppressed TSH - amiodarone-induced hyperthyroidism risk", "Notify prescriber; thyroid function review required; consider cardiology/endocrinology consult", "Amiodarone labeling; AHA/ACC 2023; Thyroid 2022", 0.92),
            LabThreshold("tsh", TSH_ALIASES, "mIU/L", "moderate", "TSH > 10 mIU/L", None, 10.0, "Elevated TSH - amiodarone-induced hypothyroidism", "Notify prescriber; may require levothyroxine initiation", "Amiodarone labeling; AHA/ACC 2023; Thyroid 2022", 0.9),
            LabThreshold("alt", ALT_ALIASES, "U/L", "high", "ALT > 105 U/L", None, 105.0, "Elevated transaminases - amiodarone hepatotoxicity risk", "Notify prescriber; consider liver function panel; evaluate for amiodarone hepatotoxicity", "Lexicomp: amiodarone hepatotoxicity monitoring", 0.9),
        ],
        required_labs=["tsh", "alt"],
    ),
    DrugLabRule(
        rule_id="statin_myopathy",
        drug_keys=["statins", "statin", "atorvastatin", "rosuvastatin", "simvastatin", "pravastatin", "fluvastatin", "lovastatin", "pitavastatin"],
        lab_thresholds=[
            LabThreshold("ck", ["ck", "creatine kinase", "cpk", "creatine phosphokinase"], "U/L", "high", "CK > 1000 U/L", None, 1000.0, "CK markedly elevated on statin - myopathy risk; screen for rhabdomyolysis", "Notify prescriber; assess for muscle symptoms (pain, weakness, dark urine); hold statin if symptomatic", "ACC/AHA Statin Safety Statement 2019; statin labeling", 0.93),
            LabThreshold("ck", ["ck", "creatine kinase", "cpk", "creatine phosphokinase"], "U/L", "critical", "CK > 10000 U/L", None, 10000.0, "Critically elevated CK - rhabdomyolysis risk", "Urgent prescriber notification; hold statin immediately; check renal function (myoglobinuria)", "ACC/AHA Statin Safety Statement 2019; statin labeling", 0.97),
            LabThreshold("alt", ALT_ALIASES, "U/L", "moderate", "ALT > 105 U/L", None, 105.0, "Elevated transaminases on statin - hepatotoxicity signal", "Notify prescriber; repeat LFTs; consider statin dose reduction", "FDA Statin Safety Communication 2012", 0.88),
        ],
        required_labs=["ck"],
    ),
    DrugLabRule(
        rule_id="nsaid_renal_and_gi",
        drug_keys=["nsaids", "nsaid", "cox-2 inhibitors", "cox-2 inhibitor", "ibuprofen", "naproxen", "diclofenac", "celecoxib", "meloxicam", "indomethacin", "ketorolac", "piroxicam", "etodolac", "mefenamic acid"],
        lab_thresholds=[
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "high", "eGFR < 30", 30.0, None, "Severely reduced eGFR - NSAIDs contraindicated; acute kidney injury risk", "Notify prescriber; NSAID should be discontinued; consider alternative analgesic", "KDIGO 2023; NSAID labeling", 0.93),
            LabThreshold("egfr", EGFR_ALIASES, "mL/min/1.73m2", "moderate", "eGFR 30-60", 30.0, 60.0, "Reduced eGFR - NSAID nephrotoxicity risk elevated", "Notify prescriber; use lowest effective dose shortest duration; monitor creatinine", "KDIGO 2023; NSAID labeling", 0.9),
            LabThreshold("potassium", POTASSIUM_ALIASES, "mEq/L", "moderate", "K+ > 5.5 mEq/L", None, 5.5, "Hyperkalemia - NSAIDs can reduce renal potassium excretion especially in elderly/CKD", "Notify prescriber; consider NSAID discontinuation", "Lexicomp: NSAID-induced hyperkalemia", 0.86),
        ],
        required_labs=["egfr"],
    ),
    DrugLabRule(
        rule_id="clozapine_anc",
        drug_keys=["clozapine"],
        lab_thresholds=[
            LabThreshold("anc", ["anc", "absolute neutrophil count", "neutrophils absolute", "neutrophil count"], "/uL", "critical", "ANC < 1000 /uL", 1000.0, None, "ANC critically low - severe neutropenia; clozapine must be discontinued immediately per REMS", "URGENT: notify prescriber immediately; clozapine must be held; clozapine REMS ANC monitoring is mandatory", "FDA Clozapine REMS program 2021; clozapine labeling", 0.98),
            LabThreshold("anc", ["anc", "absolute neutrophil count", "neutrophils absolute", "neutrophil count"], "/uL", "high", "ANC < 1500 /uL", 1500.0, None, "ANC below safe monitoring threshold - clozapine REMS requires prescriber notification and dose hold", "Notify prescriber immediately; clozapine REMS protocol requires ANC >1500 for continuation", "FDA Clozapine REMS program 2021; clozapine labeling", 0.96),
            LabThreshold("wbc", ["wbc", "white blood cell", "white blood count", "leukocytes"], "x10^9/L", "moderate", "WBC < 3.0 x10^9/L", 3.0, None, "Low WBC - clozapine-induced leukopenia risk; ANC not reported; request ANC urgently", "Notify prescriber; request ANC immediately; do not dispense clozapine without confirmed ANC", "FDA Clozapine REMS program 2021; clozapine labeling", 0.82),
        ],
        required_labs=["anc"],
    ),
    DrugLabRule(
        rule_id="ssri_snri_hyponatremia",
        drug_keys=["ssris", "ssri", "snris", "snri", "serotonin reuptake inhibitors", "serotonin reuptake inhibitor", "sertraline", "fluoxetine", "paroxetine", "escitalopram", "citalopram", "fluvoxamine", "venlafaxine", "desvenlafaxine", "duloxetine", "levomilnacipran"],
        lab_thresholds=[
            LabThreshold("sodium", SODIUM_ALIASES, "mEq/L", "high", "Na < 130 mEq/L", 130.0, None, "Severe hyponatremia - SSRIs/SNRIs can cause SIADH", "Notify prescriber; assess for SIADH symptoms (confusion, headache, seizures); SSRI/SNRI dose review", "Pharmacoepidemiology Drug Safety 2017; SSRI labeling (SIADH)", 0.92),
            LabThreshold("sodium", SODIUM_ALIASES, "mEq/L", "moderate", "Na < 135 mEq/L", 135.0, None, "Mild-moderate hyponatremia - SSRI/SNRI-induced SIADH possible", "Notify prescriber; monitor sodium; assess fluid intake/electrolytes", "Pharmacoepidemiology Drug Safety 2017; SSRI labeling (SIADH)", 0.88),
        ],
        required_labs=["sodium"],
    ),
    DrugLabRule(
        rule_id="loop_diuretic_electrolytes",
        drug_keys=["loop diuretics", "loop diuretic", "furosemide", "bumetanide", "torsemide", "ethacrynic acid"],
        lab_thresholds=[
            LabThreshold("potassium", POTASSIUM_ALIASES, "mEq/L", "high", "K+ < 3.0 mEq/L", 3.0, None, "Severe hypokalemia on loop diuretic - cardiac arrhythmia risk", "Notify prescriber; potassium supplementation review; ECG may be warranted", "ACCF/AHA Heart Failure Guideline; furosemide labeling", 0.93),
            LabThreshold("potassium", POTASSIUM_ALIASES, "mEq/L", "moderate", "K+ 3.0-3.5 mEq/L", 3.0, 3.5, "Mild-moderate hypokalemia on loop diuretic", "Notify prescriber; assess potassium supplementation and dietary intake", "ACCF/AHA Heart Failure Guideline; furosemide labeling", 0.88),
            LabThreshold("sodium", SODIUM_ALIASES, "mEq/L", "moderate", "Na < 130 mEq/L", 130.0, None, "Hyponatremia on loop diuretic", "Notify prescriber; assess volume status and fluid/sodium intake", "Lexicomp: furosemide electrolyte monitoring", 0.86),
        ],
        required_labs=["potassium"],
    ),
    DrugLabRule(
        rule_id="anticoagulant_cbc",
        drug_keys=["anticoagulants", "anticoagulant", "warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban", "enoxaparin", "heparin"],
        lab_thresholds=[
            LabThreshold("hemoglobin", ["hemoglobin", "hgb", "hb", "haemoglobin"], "g/dL", "high", "Hgb < 9 g/dL", 9.0, None, "Low hemoglobin on anticoagulant - possible occult or active bleeding", "Notify prescriber; assess for bleeding source; consider anticoagulant hold and GI evaluation", "ASH Anticoagulation Guideline 2023", 0.9),
            LabThreshold("platelets", ["platelets", "platelet count", "plt"], "x10^9/L", "high", "platelets < 50 x10^9/L", 50.0, None, "Thrombocytopenia on anticoagulant - bleeding risk significantly elevated", "Notify prescriber immediately; anticoagulant may need to be held; assess for HIT if on heparin", "ASH Anticoagulation Guideline 2023; HIT guidance", 0.92),
        ],
        required_labs=["hemoglobin", "platelets"],
    ),
]
