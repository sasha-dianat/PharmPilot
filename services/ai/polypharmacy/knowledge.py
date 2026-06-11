from __future__ import annotations

from services.ai.polypharmacy.schema import CascadeRule, ExpectedRule, PIMRule


ACB_SOURCE = (
    "Anticholinergic Cognitive Burden (ACB) Scale; Boustani et al., Aging Health 2008; "
    "Regenstrief Institute ACB Calculator"
)
BEERS_2023_SOURCE = (
    "American Geriatrics Society 2023 updated AGS Beers Criteria for potentially inappropriate "
    "medication use in older adults"
)
STOPP_START_V2_SOURCE = (
    "O'Mahony et al., STOPP/START criteria version 2, Age and Ageing 2015"
)
PRESCRIBING_CASCADE_SOURCE = (
    "Rochon and Gurwitz, prescribing cascade framework, BMJ 1997"
)
TAPER_SOURCE = (
    "Deprescribing.org algorithms and FDA/label safety warnings for benzodiazepines, opioids, "
    "antidepressants, beta-blockers, gabapentinoids, corticosteroids, PPIs, and clonidine"
)


ANTICHOLINERGIC_BURDEN: dict[str, int] = {
    "diphenhydramine": 3,
    "doxylamine": 3,
    "chlorpheniramine": 3,
    "hydroxyzine": 3,
    "promethazine": 3,
    "meclizine": 3,
    "oxybutynin": 3,
    "tolterodine": 3,
    "solifenacin": 3,
    "darifenacin": 3,
    "amitriptyline": 3,
    "nortriptyline": 3,
    "imipramine": 3,
    "doxepin": 3,
    "paroxetine": 3,
    "benztropine": 3,
    "trihexyphenidyl": 3,
    "cyclobenzaprine": 2,
    "carbamazepine": 2,
    "loperamide": 1,
    "ranitidine": 1,
    "furosemide": 1,
    "prednisone": 1,
}

SEDATIVE_FALL_RISK: set[str] = {
    "benzodiazepine",
    "alprazolam",
    "clonazepam",
    "diazepam",
    "lorazepam",
    "temazepam",
    "oxazepam",
    "z_drug",
    "zolpidem",
    "zopiclone",
    "eszopiclone",
    "opioid",
    "serotonergic_opioid",
    "oxycodone",
    "hydrocodone",
    "morphine",
    "hydromorphone",
    "fentanyl",
    "codeine",
    "tramadol",
    "sedating_antihistamine",
    "diphenhydramine",
    "doxylamine",
    "chlorpheniramine",
    "hydroxyzine",
    "promethazine",
    "antipsychotic",
    "quetiapine",
    "olanzapine",
    "risperidone",
    "haloperidol",
    "muscle_relaxant",
    "cyclobenzaprine",
    "methocarbamol",
    "carisoprodol",
    "baclofen",
}

TAPER_REQUIRED: set[str] = {
    "benzodiazepine",
    "alprazolam",
    "clonazepam",
    "diazepam",
    "lorazepam",
    "temazepam",
    "opioid",
    "serotonergic_opioid",
    "oxycodone",
    "hydrocodone",
    "morphine",
    "hydromorphone",
    "fentanyl",
    "tramadol",
    "ssri",
    "snri",
    "sertraline",
    "fluoxetine",
    "paroxetine",
    "citalopram",
    "escitalopram",
    "venlafaxine",
    "duloxetine",
    "beta_blocker",
    "metoprolol",
    "atenolol",
    "propranolol",
    "carvedilol",
    "gabapentinoid",
    "gabapentin",
    "pregabalin",
    "corticosteroid",
    "prednisone",
    "methylprednisolone",
    "ppi",
    "omeprazole",
    "pantoprazole",
    "esomeprazole",
    "lansoprazole",
    "clonidine",
}

PRESCRIBING_CASCADES: list[CascadeRule] = [
    CascadeRule(
        rule_id="dhp_ccb_loop_diuretic_edema",
        trigger_matches={"amlodipine", "nifedipine", "felodipine", "dihydropyridine_ccb"},
        effect="peripheral edema",
        treating_matches={"furosemide", "bumetanide", "torsemide", "loop_diuretic"},
        explanation=(
            "Dihydropyridine calcium-channel blockers can cause peripheral edema; a loop diuretic may be "
            "added to treat that adverse effect rather than a primary volume-overload condition."
        ),
        evidence_sources=[
            PRESCRIBING_CASCADE_SOURCE,
            "Savage et al., calcium-channel blocker and loop-diuretic prescribing cascade in older adults, JAMA Internal Medicine 2020",
        ],
        confidence=0.84,
    ),
    CascadeRule(
        rule_id="nsaid_antihypertensive_bp",
        trigger_matches={"nsaid", "ibuprofen", "naproxen", "diclofenac", "meloxicam", "celecoxib"},
        effect="raised blood pressure",
        treating_matches={
            "ace_inhibitor",
            "angiotensin_receptor_blocker",
            "beta_blocker",
            "calcium_channel_blocker",
            "amlodipine",
            "lisinopril",
            "losartan",
            "metoprolol",
        },
        explanation=(
            "NSAIDs can raise blood pressure or blunt antihypertensive effect; an antihypertensive may be "
            "added or escalated in response."
        ),
        evidence_sources=[PRESCRIBING_CASCADE_SOURCE, STOPP_START_V2_SOURCE],
    ),
    CascadeRule(
        rule_id="cholinesterase_inhibitor_bladder_anticholinergic",
        trigger_matches={"donepezil", "rivastigmine", "galantamine", "cholinesterase_inhibitor"},
        effect="urinary incontinence",
        treating_matches={"oxybutynin", "tolterodine", "solifenacin", "darifenacin", "bladder_anticholinergic"},
        explanation=(
            "Cholinesterase inhibitors can worsen urinary symptoms; bladder anticholinergics can then add "
            "opposing pharmacology and anticholinergic burden."
        ),
        evidence_sources=[PRESCRIBING_CASCADE_SOURCE, BEERS_2023_SOURCE],
    ),
    CascadeRule(
        rule_id="metoclopramide_levodopa_parkinsonism",
        trigger_matches={"metoclopramide"},
        effect="drug-induced parkinsonism",
        treating_matches={"levodopa", "carbidopa levodopa", "carbidopa-levodopa"},
        explanation=(
            "Dopamine-blocking antiemetics can cause parkinsonism; dopaminergic therapy may be added to "
            "treat a medication adverse effect."
        ),
        evidence_sources=[PRESCRIBING_CASCADE_SOURCE, BEERS_2023_SOURCE],
    ),
    CascadeRule(
        rule_id="thiazide_allopurinol_hyperuricemia",
        trigger_matches={"hydrochlorothiazide", "chlorthalidone", "thiazide_diuretic"},
        effect="hyperuricemia or gout",
        treating_matches={"allopurinol", "febuxostat"},
        explanation=(
            "Thiazide diuretics can raise uric acid; urate-lowering therapy may be added in response to "
            "the medication effect."
        ),
        evidence_sources=[PRESCRIBING_CASCADE_SOURCE, STOPP_START_V2_SOURCE],
    ),
]

PIM_RULES: list[PIMRule] = [
    PIMRule(
        rule_id="beers_benzodiazepine",
        matches={"benzodiazepine", "alprazolam", "clonazepam", "diazepam", "lorazepam", "temazepam", "oxazepam"},
        explanation="Benzodiazepines in adults 65 years and older are associated with cognitive impairment, delirium, falls, fractures, and motor vehicle crashes.",
        evidence_sources=[BEERS_2023_SOURCE, STOPP_START_V2_SOURCE],
        priority="high",
    ),
    PIMRule(
        rule_id="beers_first_generation_antihistamine",
        matches={"diphenhydramine", "doxylamine", "chlorpheniramine", "hydroxyzine", "promethazine", "meclizine"},
        explanation="First-generation antihistamines have strong anticholinergic effects and higher confusion/fall risk in older adults.",
        evidence_sources=[BEERS_2023_SOURCE, ACB_SOURCE],
        priority="high",
    ),
    PIMRule(
        rule_id="beers_long_term_nsaid",
        matches={"nsaid", "ibuprofen", "naproxen", "diclofenac", "meloxicam", "celecoxib"},
        explanation="Chronic NSAID exposure in older adults can increase gastrointestinal bleeding, kidney injury, and blood-pressure risk.",
        evidence_sources=[BEERS_2023_SOURCE, STOPP_START_V2_SOURCE],
    ),
    PIMRule(
        rule_id="beers_muscle_relaxant",
        matches={"muscle_relaxant", "cyclobenzaprine", "methocarbamol", "carisoprodol"},
        explanation="Skeletal muscle relaxants are poorly tolerated in older adults because of anticholinergic effects, sedation, and fracture risk.",
        evidence_sources=[BEERS_2023_SOURCE],
    ),
    PIMRule(
        rule_id="beers_sliding_scale_insulin",
        matches={"sliding_scale_insulin", "regular insulin sliding scale"},
        explanation="Sliding-scale insulin without basal insulin increases hypoglycemia risk without clear improvement in hyperglycemia management.",
        evidence_sources=[BEERS_2023_SOURCE],
    ),
]

EXPECTED_THERAPY: list[ExpectedRule] = [
    ExpectedRule(
        rule_id="start_afib_anticoagulant",
        condition_matches={"atrial_fibrillation", "afib", "atrial fib"},
        expected_matches={"anticoagulant", "warfarin", "apixaban", "rivaroxaban", "dabigatran", "edoxaban"},
        explanation="Atrial fibrillation commonly warrants considering anticoagulation unless contraindicated or already addressed.",
        evidence_sources=[STOPP_START_V2_SOURCE],
    ),
    ExpectedRule(
        rule_id="start_diabetes_statin",
        condition_matches={"diabetes", "type_2_diabetes", "type 2 diabetes", "diabetes_mellitus"},
        expected_matches={"statin", "atorvastatin", "rosuvastatin", "simvastatin", "pravastatin"},
        explanation="Diabetes with age at least 40 years, or documented ASCVD, commonly warrants considering statin therapy.",
        evidence_sources=[STOPP_START_V2_SOURCE, "ACC/AHA cholesterol guideline primary-prevention statin framework"],
        min_age=40,
        any_condition_matches={"ascvd", "coronary_artery_disease", "cad", "stroke", "peripheral_artery_disease"},
    ),
    ExpectedRule(
        rule_id="start_hfref_raas",
        condition_matches={"heart_failure_ref", "hfref", "heart failure with reduced ejection fraction", "systolic_heart_failure"},
        expected_matches={"ace_inhibitor", "angiotensin_receptor_blocker", "arni", "lisinopril", "enalapril", "losartan", "sacubitril valsartan"},
        explanation="Heart failure with reduced ejection fraction commonly warrants considering ACEi/ARB/ARNI therapy unless contraindicated.",
        evidence_sources=[STOPP_START_V2_SOURCE, "ACC/AHA/HFSA heart failure guideline foundational therapy framework"],
    ),
    ExpectedRule(
        rule_id="start_post_mi_beta_blocker",
        condition_matches={"post_mi", "history_of_mi", "myocardial_infarction"},
        expected_matches={"beta_blocker", "metoprolol", "carvedilol", "atenolol", "propranolol"},
        explanation="Post-MI history commonly warrants considering beta-blocker therapy when appropriate.",
        evidence_sources=[STOPP_START_V2_SOURCE],
    ),
    ExpectedRule(
        rule_id="start_post_mi_statin",
        condition_matches={"post_mi", "history_of_mi", "myocardial_infarction"},
        expected_matches={"statin", "atorvastatin", "rosuvastatin", "simvastatin", "pravastatin"},
        explanation="Post-MI history commonly warrants considering statin therapy when appropriate.",
        evidence_sources=[STOPP_START_V2_SOURCE],
    ),
]

CLASS_ALIASES: dict[str, set[str]] = {
    "amlodipine": {"dihydropyridine_ccb", "calcium_channel_blocker"},
    "nifedipine": {"dihydropyridine_ccb", "calcium_channel_blocker"},
    "felodipine": {"dihydropyridine_ccb", "calcium_channel_blocker"},
    "furosemide": {"loop_diuretic"},
    "bumetanide": {"loop_diuretic"},
    "torsemide": {"loop_diuretic"},
    "donepezil": {"cholinesterase_inhibitor"},
    "rivastigmine": {"cholinesterase_inhibitor"},
    "galantamine": {"cholinesterase_inhibitor"},
    "oxybutynin": {"bladder_anticholinergic"},
    "tolterodine": {"bladder_anticholinergic"},
    "hydrochlorothiazide": {"thiazide_diuretic"},
    "chlorthalidone": {"thiazide_diuretic"},
    "zolpidem": {"z_drug"},
    "zopiclone": {"z_drug"},
    "eszopiclone": {"z_drug"},
    "oxycodone": {"opioid"},
    "hydrocodone": {"opioid"},
    "morphine": {"opioid"},
    "hydromorphone": {"opioid"},
    "fentanyl": {"opioid"},
    "codeine": {"opioid"},
    "metoprolol": {"beta_blocker"},
    "atenolol": {"beta_blocker"},
    "propranolol": {"beta_blocker"},
    "carvedilol": {"beta_blocker"},
    "venlafaxine": {"snri"},
    "duloxetine": {"snri"},
    "gabapentin": {"gabapentinoid"},
    "pregabalin": {"gabapentinoid"},
    "prednisone": {"corticosteroid"},
    "methylprednisolone": {"corticosteroid"},
    "omeprazole": {"ppi"},
    "pantoprazole": {"ppi"},
    "esomeprazole": {"ppi"},
    "lansoprazole": {"ppi"},
    "aspirin": {"antiplatelet"},
    "clopidogrel": {"antiplatelet"},
    "prasugrel": {"antiplatelet"},
    "ticagrelor": {"antiplatelet"},
    "quetiapine": {"antipsychotic"},
    "olanzapine": {"antipsychotic"},
    "risperidone": {"antipsychotic"},
    "haloperidol": {"antipsychotic"},
    "cyclobenzaprine": {"muscle_relaxant"},
    "methocarbamol": {"muscle_relaxant"},
    "carisoprodol": {"muscle_relaxant"},
}
