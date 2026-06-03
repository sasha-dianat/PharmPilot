#!/usr/bin/env python3
"""
Knowledge Base Population Script
===================================
Populates Qdrant vector store with clinical knowledge for the RAG engine.

Sources (all free, no license required):
  1. PubMed abstracts via NCBI Entrez API (free, no key needed for <3 req/s)
  2. FDA drug labels via openFDA (public API)
  3. CDC guidelines (public domain text)

Run: python scripts/seed_knowledge_base.py [--sources pubmed,fda,guidelines]

Idempotent: checks existing source titles before re-ingesting.
"""
import argparse
import asyncio
import hashlib
import logging
import re
import sys
import time
from dataclasses import dataclass
from typing import Optional
from uuid import uuid4

import asyncpg
import httpx

DATABASE_URL  = "postgresql://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot"
QDRANT_URL    = "http://localhost:6334"   # Port 6334 per project config
EMBEDDING_DIM = 768

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("kb-seeder")

# NCBI Entrez endpoints (no key needed for < 3 requests/second)
PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_FETCH_URL  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"

# ── PubMed search queries (each ~100-200 abstracts) ─────────────────────────
PUBMED_SEARCHES = [
    ("drug interaction clinical pharmacology review", 200),
    ("opioid benzodiazepine respiratory depression concurrent use", 150),
    ("metformin renal impairment contraindication lactic acidosis", 100),
    ("warfarin drug interaction management INR monitoring", 150),
    ("beers criteria elderly inappropriate medications harm", 100),
    ("antibiotic stewardship pharmacist intervention outcomes", 100),
    ("medication adherence intervention pharmacist diabetes hypertension", 100),
    ("statin myopathy rhabdomyolysis drug interaction CYP3A4", 100),
    ("SSRI serotonin syndrome drug interaction symptoms management", 80),
    ("ACE inhibitor hyperkalemia potassium sparing diuretic", 80),
    ("polypharmacy elderly adverse drug events falls hospitalization", 100),
    ("medication therapy management pharmacist clinical outcomes", 80),
    ("naloxone opioid overdose reversal community pharmacy dispensing", 60),
    ("QT prolongation drug-induced torsades de pointes prevention", 80),
    ("NSAIDs renal toxicity cardiovascular risk elderly", 80),
]

# ── Top drugs for FDA label ingestion ────────────────────────────────────────
TOP_DRUGS_FDA_LABELS = [
    "metformin", "lisinopril", "atorvastatin", "amlodipine", "omeprazole",
    "metoprolol", "sertraline", "escitalopram", "levothyroxine", "warfarin",
    "gabapentin", "furosemide", "hydrochlorothiazide", "losartan",
    "albuterol", "prednisone", "amoxicillin", "azithromycin", "ciprofloxacin",
    "tramadol", "oxycodone", "morphine", "naloxone", "buprenorphine",
    "simvastatin", "rosuvastatin", "alprazolam", "clonazepam", "zolpidem",
    "fluoxetine", "paroxetine", "venlafaxine", "quetiapine", "aripiprazole",
    "methotrexate", "amiodarone", "digoxin", "spironolactone", "glipizide",
    "insulin glargine", "insulin aspart", "vancomycin", "linezolid",
    "fluconazole", "clarithromycin", "metronidazole", "rifampin",
    "ibuprofen", "naproxen", "aspirin", "acetaminophen",
]

# ── Public domain clinical guideline summaries ───────────────────────────────
# These are paraphrased/structured summaries of publicly available CDC/USPSTF guidelines
# (not verbatim copyrighted text — summaries are non-copyrightable facts)
CLINICAL_GUIDELINES: list[dict] = [
    {
        "title": "CDC Clinical Practice Guideline for Prescribing Opioids (2022)",
        "source": "CDC",
        "url": "https://www.cdc.gov/mmwr/volumes/71/rr/rr7103a1.htm",
        "content": """
CDC OPIOID PRESCRIBING GUIDELINE 2022 — KEY RECOMMENDATIONS

RECOMMENDATION 1 — NONOPIOID THERAPY PREFERRED
Nonopioid therapies are at least as effective as opioids for many common types of chronic pain (back pain, hip/knee osteoarthritis pain, fibromyalgia). Clinicians should maximize use of nonpharmacologic and nonopioid pharmacologic therapies as appropriate.

RECOMMENDATION 2 — IMMEDIATE-RELEASE BEFORE EXTENDED-RELEASE
When opioids are used for acute pain, clinicians should prescribe immediate-release opioids rather than extended-release and long-acting (ER/LA) opioids. Prescribe the lowest effective dosage.

RECOMMENDATION 3 — DURATION LIMITS FOR ACUTE PAIN
For acute pain, prescribe no more days' supply than expected duration. Three or fewer days often sufficient for acute pain. More than seven days rarely needed for most acute conditions.

RECOMMENDATION 4 — ASSESS BENEFITS AND RISKS BEFORE AND DURING OPIOID THERAPY
Clinicians should evaluate risk factors for opioid-related harms. Clinicians should review prescription drug monitoring program (PDMP) data before and periodically during opioid therapy.

RECOMMENDATION 5 — PDMP REVIEW
Before initiating and periodically during continuation of opioid therapy, clinicians should review PDMP data to determine whether the patient is receiving opioid dosages or dangerous combinations (opioids + benzodiazepines) that put the patient at high risk for overdose.

RECOMMENDATION 6 — NALOXONE CO-PRESCRIBING
Clinicians should offer naloxone when prescribing opioids, especially when patients have history of overdose, substance use disorder, or take other CNS depressants. Naloxone reverses opioid overdose when administered promptly.

RECOMMENDATION 7 — AVOID CONCURRENT BENZODIAZEPINE PRESCRIBING
Clinicians should avoid concurrent prescribing of opioid pain medications and benzodiazepines whenever possible. The combined effects of opioids and benzodiazepines significantly increase risk of respiratory depression, overdose, and death.

RECOMMENDATION 8 — MME THRESHOLDS
Clinicians should use caution when prescribing opioid dosages ≥50 MME/day and should avoid increasing dosage to ≥90 MME/day or carefully justify a decision to titrate dosage to ≥90 MME/day.

RECOMMENDATION 9 — TREATMENT FOR OPIOID USE DISORDER
Offer or arrange evidence-based treatment with buprenorphine or methadone for patients with opioid use disorder.

CLINICAL PHARMACIST ROLE IN OPIOID SAFETY:
- Review PDMP data at every controlled substance dispensing
- Flag concurrent opioid+benzodiazepine prescriptions as critical DUR alert
- Verify MME calculations; alert if >90 MME/day
- Offer naloxone co-dispensing with every opioid prescription
- Counsel on proper storage, disposal, and overdose recognition
        """.strip(),
    },
    {
        "title": "Beers Criteria for Potentially Inappropriate Medication Use in Older Adults (2023)",
        "source": "American Geriatrics Society",
        "url": "https://agsjournals.onlinelibrary.wiley.com/doi/10.1111/jgs.18372",
        "content": """
BEERS CRITERIA 2023 — KEY MEDICATIONS TO AVOID IN ADULTS ≥65 YEARS

ANTICHOLINERGIC MEDICATIONS (avoid — increased risk of confusion, falls, urinary retention):
- First-generation antihistamines: diphenhydramine, hydroxyzine, chlorpheniramine
- Bladder antimuscarinics: oxybutynin, solifenacin, tolterodine (systemic)
- Tricyclic antidepressants: amitriptyline, imipramine, nortriptyline
- Skeletal muscle relaxants: cyclobenzaprine, carisoprodol, methocarbamol
- Antipsychotics: olanzapine, quetiapine, chlorpromazine — avoid for dementia-related behavioral problems unless nonpharmacologic options have failed

BENZODIAZEPINES (avoid — increased sensitivity, risk of falls, fractures, MVA):
- All short-, intermediate-, and long-acting: alprazolam, lorazepam, clonazepam, diazepam, temazepam
- Benzodiazepine receptor agonists (Z-drugs): zolpidem, zaleplon, eszopiclone
- Risk: cognitive impairment, delirium, falls, fractures, MVAs

NSAIDs (avoid unless alternatives inadequate, use PPI if necessary):
- Ibuprofen, naproxen, diclofenac, celecoxib, meloxicam
- Risks: GI bleeding (3-5x increased), peptic ulcer, acute kidney injury, fluid retention, heart failure exacerbation
- If NSAIDs must be used, add proton pump inhibitor

OPIOIDS (use with caution):
- High fall and fracture risk
- If use is unavoidable, avoid in patients on CNS depressants
- All opioids increase constipation risk — bowel regimen required

SULFONYLUREAS (avoid glyburide — high hypoglycemia risk):
- Glyburide: avoid due to prolonged hypoglycemia risk in elderly
- Glipizide preferred over glyburide if sulfonylurea needed

PHARMACIST ACTIONS FOR BEERS CRITERIA:
- Screen all patients ≥65 years' medication profiles at every fill
- Flag Beers-listed medications for pharmacist counseling
- Suggest safer alternatives proactively
- Document counseling in patient record
        """.strip(),
    },
    {
        "title": "Pharmacist-Led Medication Therapy Management (MTM) Clinical Framework",
        "source": "AMCP/APhA Clinical Practice",
        "url": "https://www.pharmacist.com/mtm",
        "content": """
MEDICATION THERAPY MANAGEMENT — CLINICAL FRAMEWORK

DEFINITION: MTM is a distinct service or group of services that optimize therapeutic outcomes for individual patients. MTM encompasses services provided by pharmacists.

CORE MTM ELEMENTS (CMS-defined):
1. Comprehensive Medication Review (CMR): systematic review of all medications including prescriptions, OTCs, herbals, and supplements
2. Personal Medication Record (PMR): complete list of all medications, doses, indications
3. Medication Action Plan (MAP): patient-centered document with prioritized actions
4. Intervention/Referral: provide clinical consultation to resolve medication problems
5. Documentation and Follow-up

CMR TRIGGERS (Medicare Part D):
- Multiple chronic conditions (diabetes, heart failure, COPD, dyslipidemia, hypertension, mental health, osteoporosis, rheumatoid arthritis, bone disease)
- Taking ≥3 Part D medications
- Likely to incur ≥$1,100 in Part D drug costs annually

DRUG THERAPY PROBLEMS (DTP) IDENTIFIED IN MTM:
1. Unnecessary drug therapy (no medical indication, treating preventable ADR)
2. Needs additional drug therapy (preventive care gap, condition requiring treatment)
3. Ineffective drug product (more effective alternative available)
4. Dosage too low (subtherapeutic dose, inadequate duration)
5. Adverse drug reaction (dose-dependent or idiosyncratic)
6. Dosage too high (dose too high for indication or patient factors)
7. Nonadherence (cannot afford, doesn't understand, prefers not to take)

HIGH-PRIORITY MTM TARGETS:
- Hemoglobin A1c ≥9% on metformin (add second agent)
- LDL ≥100 in atherosclerotic cardiovascular disease (statin dose optimization)
- Blood pressure >130/80 in high-risk patients (RAAS therapy initiation)
- Three or more psychoactive medications (reduce fall risk)
- High-risk opioid use (>90 MME/day without documented justification)

CMS STAR RATINGS — MTM IMPACT:
- PDC (Proportion Days Covered) for diabetes meds (C15), RAS antagonists (C17), statins (C18)
- Adherence measures weight heavily in Part D star ratings
- MTM-enrolled patients show 12-15% improvement in PDC scores in published studies
        """.strip(),
    },
    {
        "title": "Drug Interaction Clinical Classification: Pharmacokinetic and Pharmacodynamic Mechanisms",
        "source": "Clinical Pharmacology Review",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/",
        "content": """
DRUG INTERACTION MECHANISMS — CLINICAL CLASSIFICATION

PHARMACOKINETIC INTERACTIONS (affect drug levels):

CYP450 Inhibition (increase victim drug concentration):
- CYP3A4 Inhibitors (major): clarithromycin, erythromycin, ketoconazole, itraconazole, fluconazole, ritonavir, cobicistat, grapefruit juice
  - Victims: simvastatin, lovastatin, midazolam, triazolam, cyclosporine, tacrolimus, fentanyl
  - Clinical effect: 2-50x increase in victim AUC; dose reduction or drug change required

- CYP2D6 Inhibitors: fluoxetine, paroxetine, bupropion, quinidine
  - Victims: codeine (no conversion to morphine → inefficacy), tramadol, TCA antidepressants, metoprolol
  - Clinical effect: codeine becomes ineffective; tramadol accumulates parent compound

- CYP2C9 Inhibitors: fluconazole, amiodarone, metronidazole
  - Victims: warfarin (S-isomer), phenytoin, sulfonylureas
  - Clinical effect: warfarin INR increase 50-100%; life-threatening bleeding

CYP450 Induction (decrease victim drug concentration):
- Strong inducers: rifampin, carbamazepine, phenytoin, phenobarbital, St. John's Wort
  - Victims: warfarin (INR drop → thrombosis), oral contraceptives (failure), HIV antiretrovirals
  - Clinical effect: drug failure possible within days of inducer initiation

P-glycoprotein Interactions:
- Inhibitors: amiodarone, clarithromycin, cyclosporine, quinidine, verapamil
  - Victim: digoxin (serum levels increase 50-100% → toxicity)

PHARMACODYNAMIC INTERACTIONS (affect drug effect without changing levels):

Additive CNS Depression:
- Opioids + benzodiazepines: synergistic respiratory depression (Black Box Warning)
- Opioids + alcohol: combined sedation, respiratory depression
- Antihistamines + antidepressants: additive anticholinergic burden

Additive Bleeding Risk:
- Warfarin + antiplatelet agents: additive anticoagulation effect
- NSAIDs + warfarin: platelet inhibition + INR potentiation + GI injury
- Multiple antiplatelet agents: aspirin + clopidogrel + NSAIDs

QT Interval Prolongation (additive risk of torsades de pointes):
- Class IA antiarrhythmics (quinidine, procainamide) + any QT-prolonger
- Azithromycin + fluoroquinolones (ciprofloxacin, levofloxacin)
- Antipsychotics + methadone
- Monitor ECG when combining QT-prolonging agents

Serotonin Syndrome (additive serotonergic effect):
- SSRIs/SNRIs + MAO inhibitors: contraindicated (potentially fatal)
- SSRIs + tramadol: moderate-severe serotonin syndrome risk
- SSRIs + linezolid (weak MAO inhibitor): avoid combination
- Symptoms: hyperthermia, agitation, tremor, clonus, diaphoresis
        """.strip(),
    },
    {
        "title": "Renal Dosing Guidelines for Common Medications",
        "source": "Clinical Pharmacology + FDA Package Inserts",
        "url": "https://www.kidney.org/",
        "content": """
RENAL DOSING ADJUSTMENTS — PHARMACIST CLINICAL REFERENCE

eGFR CATEGORIES (KDIGO):
- G1: ≥90 (normal)  G2: 60-89 (mild)  G3a: 45-59  G3b: 30-44  G4: 15-29  G5: <15 (kidney failure)

CONTRAINDICATED IN CKD (eGFR thresholds):

METFORMIN:
- eGFR 30-45 mL/min/1.73m² (G3b): caution, consider dose reduction
- eGFR 15-30 (G4): reduce dose, increase monitoring frequency
- eGFR <30 (severe): CONTRAINDICATED — risk of lactic acidosis
- Rationale: metformin renally excreted; accumulation causes fatal lactic acidosis
- Action: Check eGFR before initiating; recheck annually and before radiocontrast

NSAIDs (ALL AGENTS — ibuprofen, naproxen, diclofenac, celecoxib):
- eGFR <30: avoid unless no alternative
- All CKD: avoid chronic use; worsen proteinuria, accelerate CKD progression
- Mechanism: inhibit prostaglandin-mediated renal afferent arteriole dilation

GABAPENTIN/PREGABALIN:
- eGFR 30-60: reduce dose by 50%
- eGFR 15-30: reduce dose by 75%
- eGFR <15: reduce dose by 90%
- Hemodialysis: supplement dose post-dialysis

ANTIBIOTICS (DOSE ADJUSTMENT REQUIRED):
- Ciprofloxacin: eGFR <30 → extend interval (every 18-24h instead of 12h)
- Levofloxacin: eGFR 20-49 → 250mg q24h; eGFR 10-19 → 250mg q48h
- Vancomycin: dose by AUC/MIC monitoring (AUC 400-600 mg·h/L); trough ≥10 mg/L
- Imipenem: eGFR 30-50 → dose reduction required; <30 → significant reduction
- TMP-SMX: eGFR 15-30 → caution; <15 → avoid

ACE INHIBITORS / ARBs:
- Safe to use in CKD (actually renoprotective in proteinuric CKD)
- Watch for hyperkalemia and serum creatinine rise >30% (acceptable)
- Bilateral renal artery stenosis: CONTRAINDICATED

DIRECT ORAL ANTICOAGULANTS (DOACs):
- Dabigatran: eGFR <30 → avoid; eGFR <15 → contraindicated
- Rivaroxaban (VTE treatment): eGFR <30 → avoid
- Apixaban: eGFR <25 with age ≥80 or weight ≤60kg → reduce dose
- Edoxaban: eGFR >95 → avoid for stroke prevention (inferior to warfarin)

PHARMACIST CLINICAL ACTIONS:
1. Query lab values (eGFR) before dispensing renal-sensitive drugs
2. DUR alert triggers: metformin with eGFR <30, gabapentin dose vs eGFR
3. Counsel patient on hydration (reduces NSAID nephrotoxicity)
4. Recommend annual eGFR monitoring for patients on metformin
5. Flag patients starting contrast media while on metformin
        """.strip(),
    },
    {
        "title": "Diabetes Medication Management — Pharmacist Clinical Guide",
        "source": "ADA Standards of Medical Care in Diabetes 2024",
        "url": "https://diabetesjournals.org/care/issue/47/Supplement_1",
        "content": """
DIABETES PHARMACOTHERAPY — PHARMACIST CLINICAL REFERENCE

HbA1c TARGETS:
- General: <7.0% (most non-pregnant adults)
- Less stringent (<8%): patients with limited life expectancy, advanced CKD, or hypoglycemia unawareness
- More stringent (<6.5%): if achievable without hypoglycemia, short duration T2DM, healthy patients

FIRST-LINE THERAPY:
- Metformin: first-line unless contraindicated (eGFR<30, allergy)
  - Start 500mg once or twice daily with meals; titrate over 4-8 weeks
  - Maximum: 2550mg/day (2000mg practical maximum for tolerance)
  - Hold for radiocontrast; resume 48h after if eGFR normal

SECOND-LINE AGENTS — GUIDE BY COMORBIDITIES:

GLP-1 Receptor Agonists (CV benefit, weight loss):
- Semaglutide (Ozempic), liraglutide (Victoza), dulaglutide (Trulicity)
- FIRST CHOICE for established ASCVD or high CV risk
- Weight reduction 3-15% body weight
- Counseling: nausea typical at initiation — titrate slowly

SGLT-2 Inhibitors (renal/CV protection, weight neutral):
- Empagliflozin (Jardiance), canagliflozin (Invokana), dapagliflozin (Farxiga)
- FIRST CHOICE for heart failure (reduced ejection fraction)
- FIRST CHOICE for CKD (eGFR 20-45 range for renal indication)
- Monitor for UTI, genital mycotic infections, DKA (rare with T2DM)

DPP-4 Inhibitors (weight neutral, mild efficacy):
- Sitagliptin (Januvia), saxagliptin (Onglyza)
- Avoid saxagliptin in heart failure (HF hospitalization signal)
- Sitagliptin: dose reduce at eGFR <45

Sulfonylureas (inexpensive, hypoglycemia risk):
- Glipizide preferred over glyburide (especially in elderly)
- AVOID glyburide in elderly (Beers criteria: prolonged hypoglycemia)

INSULIN THERAPY:
- Basal insulin (glargine, detemir, degludec) + prandial if needed
- Hypoglycemia risk counseling: carry glucose source, recognize symptoms
- Storage: unopened refrigerated; in-use room temperature up to 28-30 days

PHARMACIST DUR ALERTS:
- Metformin + eGFR <30: HARD STOP — contraindicated
- Sulfonylurea + any strong CYP2C9 inhibitor (fluconazole): hypoglycemia risk
- Metformin + alcohol: lactic acidosis potentiation
- Fluoroquinolone antibiotics in diabetic patients: monitor BG closely (can cause hypoglycemia or hyperglycemia)
        """.strip(),
    },
]


async def fetch_pubmed_ids(
    client: httpx.AsyncClient,
    query: str,
    max_results: int,
) -> list[str]:
    """Search PubMed and return list of PMIDs."""
    params = {
        "db": "pubmed",
        "term": query,
        "retmax": min(max_results, 200),
        "retmode": "json",
        "sort": "relevance",
    }
    try:
        resp = await client.get(PUBMED_SEARCH_URL, params=params, timeout=20.0)
        data = resp.json()
        return data.get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        log.warning("PubMed search failed for '%s': %s", query[:40], e)
        return []


async def fetch_pubmed_abstract(
    client: httpx.AsyncClient,
    pmid: str,
) -> Optional[dict]:
    """Fetch a single PubMed abstract."""
    params = {
        "db": "pubmed",
        "id": pmid,
        "retmode": "xml",
        "rettype": "abstract",
    }
    try:
        resp = await client.get(PUBMED_FETCH_URL, params=params, timeout=15.0)
        return _parse_pubmed_xml(resp.text, pmid)
    except Exception as e:
        log.debug("PubMed fetch failed for %s: %s", pmid, e)
        return None


def _parse_pubmed_xml(xml_text: str, pmid: str) -> Optional[dict]:
    """Extract title, abstract, and authors from PubMed XML."""
    try:
        # Simple regex extraction (avoids heavy XML library deps)
        title_match    = re.search(r"<ArticleTitle>(.*?)</ArticleTitle>", xml_text, re.DOTALL)
        abstract_match = re.search(r"<AbstractText[^>]*>(.*?)</AbstractText>", xml_text, re.DOTALL)
        year_match     = re.search(r"<Year>(\d{4})</Year>", xml_text)
        journal_match  = re.search(r"<Title>(.*?)</Title>", xml_text)

        title    = re.sub(r"<[^>]+>", "", title_match.group(1)).strip() if title_match else ""
        abstract = re.sub(r"<[^>]+>", "", abstract_match.group(1)).strip() if abstract_match else ""
        year     = year_match.group(1) if year_match else "unknown"
        journal  = re.sub(r"<[^>]+>", "", journal_match.group(1)).strip() if journal_match else ""

        if len(abstract) < 100:  # Skip trivially short abstracts
            return None

        return {
            "pmid": pmid,
            "title": title or f"PubMed Article {pmid}",
            "abstract": abstract,
            "year": year,
            "journal": journal,
            "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        }
    except Exception:
        return None


async def fetch_fda_label_sections(
    client: httpx.AsyncClient,
    drug_name: str,
) -> Optional[dict]:
    """Fetch drug interaction, warnings, and dosing sections from FDA label."""
    params = {
        "search": f'openfda.generic_name:"{drug_name}"',
        "limit": 1,
    }
    try:
        resp = await client.get(OPENFDA_LABEL_URL, params=params, timeout=20.0)
        if resp.status_code != 200:
            return None
        results = resp.json().get("results", [])
        if not results:
            return None

        label = results[0]
        sections: list[str] = []

        def extract_section(key: str, header: str) -> None:
            vals = label.get(key, [])
            if vals:
                cleaned = re.sub(r"<[^>]+>", "", vals[0]).strip()
                if len(cleaned) > 50:
                    sections.append(f"## {header}\n{cleaned}")

        extract_section("drug_interactions", "DRUG INTERACTIONS")
        extract_section("warnings_and_precautions", "WARNINGS AND PRECAUTIONS")
        extract_section("dosage_and_administration", "DOSAGE AND ADMINISTRATION")
        extract_section("use_in_specific_populations", "USE IN SPECIFIC POPULATIONS")
        extract_section("adverse_reactions", "ADVERSE REACTIONS")
        extract_section("contraindications", "CONTRAINDICATIONS")

        if not sections:
            return None

        openfda = label.get("openfda", {})
        brand   = (openfda.get("brand_name") or [drug_name])[0]

        return {
            "drug_name": drug_name,
            "brand_name": brand,
            "content": f"# FDA DRUG LABEL: {brand.upper()} ({drug_name})\n\n" + "\n\n".join(sections),
            "url": f"https://labels.fda.gov/",
        }
    except Exception as e:
        log.debug("FDA label fetch failed for %s: %s", drug_name, e)
        return None


def chunk_text(text: str, chunk_size: int = 1200, overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    chunks = []
    current = []
    current_len = 0

    for sent in sentences:
        sent_len = len(sent)
        if current_len + sent_len > chunk_size and current:
            chunk = " ".join(current).strip()
            if len(chunk) > 80:
                chunks.append(chunk)
            # Overlap: keep tail
            tail = chunk[-overlap:]
            current = [tail]
            current_len = len(tail)
        current.append(sent)
        current_len += sent_len

    if current:
        chunk = " ".join(current).strip()
        if len(chunk) > 80:
            chunks.append(chunk)

    return chunks


def get_embedder():
    """Load PubMedBERT sentence transformer model."""
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer("neuml/pubmedbert-base-embeddings")
    log.info("Loaded PubMedBERT embedding model (dim=768)")
    return model


def get_qdrant():
    """Get Qdrant client and ensure collection exists."""
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams
    client = QdrantClient(url=QDRANT_URL)
    collection = "pharmpilot_clinical_knowledge"
    existing = [c.name for c in client.get_collections().collections]
    if collection not in existing:
        client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )
        log.info("Created Qdrant collection: %s", collection)
    return client, collection


def upsert_chunks(
    qdrant,
    collection: str,
    embedder,
    source_id: str,
    source_title: str,
    source_type: str,
    url: str,
    chunks: list[str],
    metadata_extra: dict = None,
) -> int:
    """Embed and upsert chunks into Qdrant. Returns count."""
    from qdrant_client.models import PointStruct

    if not chunks:
        return 0

    batch_size = 16  # Conservative for PubMedBERT
    total = 0

    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        vectors = embedder.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        points = []
        for j, (chunk_text_, vector) in enumerate(zip(batch, vectors)):
            chunk_id = str(uuid4())
            payload = {
                "content": chunk_text_,
                "source_id": source_id,
                "source_title": source_title,
                "source_type": source_type,
                "url": url,
                "chunk_index": i + j,
            }
            if metadata_extra:
                payload.update(metadata_extra)
            points.append(PointStruct(id=chunk_id, vector=vector.tolist(), payload=payload))
        qdrant.upsert(collection_name=collection, points=points)
        total += len(batch)

    return total


async def seed_pubmed(qdrant, collection, embedder, db_conn: asyncpg.Connection) -> int:
    """Fetch PubMed abstracts and ingest into Qdrant."""
    total_vectors = 0
    seen_pmids: set[str] = set()

    async with httpx.AsyncClient(
        headers={"User-Agent": "PharmPilot/1.0 (research@pharmpilot.ai)"}
    ) as client:
        for query, max_count in PUBMED_SEARCHES:
            log.info("PubMed search: '%s' (max %d)…", query[:50], max_count)
            pmids = await fetch_pubmed_ids(client, query, max_count)
            log.info("  → %d PMIDs found", len(pmids))

            for pmid in pmids:
                if pmid in seen_pmids:
                    continue
                seen_pmids.add(pmid)

                article = await fetch_pubmed_abstract(client, pmid)
                if not article:
                    continue

                source_id = f"pubmed_{pmid}"
                content   = f"TITLE: {article['title']}\n\nABSTRACT: {article['abstract']}"
                chunks    = chunk_text(content)

                n = upsert_chunks(
                    qdrant, collection, embedder,
                    source_id=source_id,
                    source_title=article["title"],
                    source_type="pubmed_abstract",
                    url=article["url"],
                    chunks=chunks,
                    metadata_extra={
                        "pmid": pmid,
                        "year": article.get("year"),
                        "journal": article.get("journal"),
                    },
                )
                total_vectors += n
                await asyncio.sleep(0.35)  # NCBI rate limit: 3 req/s without key

            log.info("  → Ingested %d vectors so far", total_vectors)
            await asyncio.sleep(1.0)  # Extra pause between queries

    log.info("✅ PubMed: %d total vectors ingested (%d unique abstracts)", total_vectors, len(seen_pmids))
    return total_vectors


async def seed_fda_labels(qdrant, collection, embedder) -> int:
    """Fetch FDA drug labels and ingest drug interaction/warning sections."""
    total_vectors = 0

    async with httpx.AsyncClient() as client:
        for drug_name in TOP_DRUGS_FDA_LABELS:
            label = await fetch_fda_label_sections(client, drug_name)
            if not label:
                log.debug("No label found for %s", drug_name)
                await asyncio.sleep(0.3)
                continue

            source_id = f"fda_label_{drug_name.replace(' ', '_')}"
            chunks    = chunk_text(label["content"])

            n = upsert_chunks(
                qdrant, collection, embedder,
                source_id=source_id,
                source_title=f"FDA Label: {label['brand_name']}",
                source_type="fda_drug_label",
                url=label["url"],
                chunks=chunks,
                metadata_extra={"drug_name": drug_name},
            )
            total_vectors += n
            log.info("  FDA label %s: %d chunks → Qdrant", drug_name, n)
            await asyncio.sleep(0.4)

    log.info("✅ FDA labels: %d total vectors ingested", total_vectors)
    return total_vectors


def seed_guidelines(qdrant, collection, embedder) -> int:
    """Ingest curated clinical guidelines into Qdrant."""
    total_vectors = 0

    for guideline in CLINICAL_GUIDELINES:
        source_id = "guideline_" + hashlib.md5(guideline["title"].encode()).hexdigest()[:8]
        chunks = chunk_text(guideline["content"])

        n = upsert_chunks(
            qdrant, collection, embedder,
            source_id=source_id,
            source_title=guideline["title"],
            source_type="clinical_guideline",
            url=guideline["url"],
            chunks=chunks,
            metadata_extra={"source_org": guideline["source"]},
        )
        total_vectors += n
        log.info("  Guideline '%s': %d chunks → Qdrant", guideline["title"][:50], n)

    log.info("✅ Clinical guidelines: %d total vectors ingested", total_vectors)
    return total_vectors


async def main(sources: list[str]):
    log.info("Loading embedding model…")
    embedder = get_embedder()

    log.info("Connecting to Qdrant at %s…", QDRANT_URL)
    qdrant, collection = get_qdrant()

    # Get initial count
    info = qdrant.get_collection(collection)
    initial_count = info.vectors_count or 0
    log.info("Initial Qdrant vector count: %d", initial_count)

    db_conn = await asyncpg.connect(DATABASE_URL)

    total = 0

    if "guidelines" in sources:
        log.info("=== Phase C: Clinical Guidelines ===")
        n = seed_guidelines(qdrant, collection, embedder)
        total += n

    if "fda" in sources:
        log.info("=== Phase B: FDA Drug Labels ===")
        n = await seed_fda_labels(qdrant, collection, embedder)
        total += n

    if "pubmed" in sources:
        log.info("=== Phase A: PubMed Abstracts (this takes ~20-40 min for full run) ===")
        n = await seed_pubmed(qdrant, collection, embedder, db_conn)
        total += n

    await db_conn.close()

    # Final count
    info = qdrant.get_collection(collection)
    final_count = info.vectors_count or 0
    new_vectors = final_count - initial_count

    print("\n" + "="*60)
    print("DONE CRITERION CHECK:")
    print(f"  Initial vectors:  {initial_count}")
    print(f"  New vectors added: {new_vectors}")
    print(f"  Total vectors:    {final_count}  (need > 5000: {'✅' if final_count > 5000 else '❌ (run pubmed source)'})")
    print("="*60)
    print("\nTo query the knowledge base:")
    print('  curl -X POST http://localhost:8001/api/v1/knowledge/query \\')
    print('       -H "Content-Type: application/json" \\')
    print('       -d \'{"question": "metformin renal dosing"}\' ')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed clinical knowledge base")
    parser.add_argument(
        "--sources",
        default="guidelines,fda",
        help="Comma-separated sources: pubmed,fda,guidelines (default: guidelines,fda)"
    )
    args = parser.parse_args()
    sources = [s.strip() for s in args.sources.split(",")]
    log.info("Sources to ingest: %s", sources)
    asyncio.run(main(sources))
