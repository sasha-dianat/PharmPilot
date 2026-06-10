/**
 * PharmPilot Global Abbreviation Glossary
 * =========================================
 * Every abbreviation used anywhere in the platform.
 * Displayed as a tooltip when user hovers over the term.
 *
 * Categories:
 *   Drug / Dispensing  — NDC, SIG, DAW, DUR, AWP…
 *   Clinical / Medical — DDI, ADR, QD, BID, PRN…
 *   Insurance / Claims — COB, EOB, ERA, PA, PBM…
 *   Regulatory         — HIPAA, PHI, DEA, FDA, USP…
 *   Technical / System — API, JWT, ML, OCR, FHIR…
 *   Financial          — POS, HSA, FSA, DIR…
 */

export const GLOSSARY: Record<string, string> = {

  // ── Drug / Dispensing ────────────────────────────────────────────────────
  'NDC':        'National Drug Code — unique 11-digit identifier for every drug product (labeler·product·package)',
  'NDC-11':     '11-digit National Drug Code: 5-digit labeler code + 4-digit product code + 2-digit package code',
  'NDC-10':     '10-digit National Drug Code — older format; must be zero-padded to NDC-11 for claims',
  'NPI':        'National Provider Identifier — unique 10-digit number assigned to every US healthcare provider',
  'DEA':        'Drug Enforcement Administration — US federal agency that regulates controlled substances',
  'Rx':         'Prescription — a licensed prescriber\'s written or electronic order authorizing a specific medication for a patient',
  'OTC':        'Over-The-Counter — medication sold without a prescription (e.g., ibuprofen, antacids)',
  'SIG':        'Signa (Latin: "mark thou") — the directions to the patient printed on the dispensing label',
  'DAW':        'Dispense As Written — code indicating whether generic substitution is permitted (DAW-0 = substitution OK; DAW-1 = brand required by prescriber)',
  'DUR':        'Drug Utilization Review — clinical screening of prescriptions for drug interactions, allergies, duplicates, and dosing issues',
  'AWP':        'Average Wholesale Price — benchmark list price for a drug; AWP = WAC × 1.07 (generic). Used as a pricing reference — pharmacies rarely pay AWP',
  'WAC':        'Wholesale Acquisition Cost — manufacturer\'s published list price to wholesalers before discounts',
  'AAC':        'Actual Acquisition Cost — the real price the pharmacy paid for the drug after all discounts',
  'DIR':        'Direct and Indirect Remuneration — retroactive fees clawed back by PBMs from pharmacies after claim adjudication; often wiping out the original margin',
  'GPI':        'Generic Product Identifier — 14-character hierarchical drug classification code used for formulary management',
  'GCN':        'Generic Code Number — identifier for generic drug equivalency groupings',
  'RxCUI':      'RxNorm Concept Unique Identifier — NLM\'s unique identifier for drug concepts in the RxNorm vocabulary',
  'RxNorm':     'RxNorm — NLM\'s normalized naming system for clinical drugs; links drug names across different coding systems',
  'NLM':        'National Library of Medicine — US federal agency that manages RxNorm, PubMed, and other health databases',
  'DSCSA':      'Drug Supply Chain Security Act — US law (2013) requiring full electronic traceability of prescription drugs through the supply chain',
  'TTAC':       'Track and Trace Assistance Center — drug shortage search system operated by HDA (NOT for authenticity verification)',
  'USP':        'United States Pharmacopeia — non-governmental standards organization setting quality, purity, and potency standards for medicines',
  'GMP':        'Good Manufacturing Practice — FDA quality system regulations for pharmaceutical manufacturing',
  'NIOSH':      'National Institute for Occupational Safety and Health — publishes Table 1 list of hazardous drugs requiring special handling',

  // ── Clinical / Medical dosing ────────────────────────────────────────────
  'DDI':        'Drug-Drug Interaction — a pharmacological effect caused by two or more drugs taken together; can increase/decrease drug effect or cause adverse reactions',
  'ADR':        'Adverse Drug Reaction — harmful, unintended response to a medication at normal therapeutic doses',
  'QD':         'Quaque Die (Latin) — once daily',
  'BID':        'Bis In Die (Latin) — twice daily (every 12 hours)',
  'TID':        'Ter In Die (Latin) — three times daily (every 8 hours)',
  'QID':        'Quater In Die (Latin) — four times daily (every 6 hours)',
  'PRN':        'Pro Re Nata (Latin) — as needed; only when the patient requires it',
  'AC':         'Ante Cibum (Latin) — before meals',
  'PC':         'Post Cibum (Latin) — after meals',
  'HS':         'Hora Somni (Latin) — at bedtime (hora somni = "hour of sleep")',
  'PO':         'Per Os (Latin) — by mouth (oral administration route)',
  'IV':         'Intravenous — administered directly into a vein',
  'IM':         'Intramuscular — injected into a muscle (e.g., deltoid, gluteal)',
  'SQ':         'Subcutaneous — injected into the tissue just beneath the skin',
  'SC':         'Subcutaneous — same as SQ; injected under the skin',
  'SL':         'Sublingual — placed under the tongue for rapid absorption into bloodstream',
  'INH':        'Inhalation — administered by breathing through an inhaler or nebulizer',
  'TOP':        'Topical — applied to skin or mucous membranes (cream, ointment, patch)',
  'OPH':        'Ophthalmic — for use in the eye only',
  'STAT':       'Statim (Latin) — immediately; administer or process at once',
  'mg':         'Milligrams — unit of drug mass (1/1000 of a gram)',
  'mcg':        'Micrograms — unit of drug mass (1/1,000,000 of a gram); also written μg',
  'mL':         'Millilitres — unit of liquid volume (1/1000 of a litre)',

  // ── Insurance / Claims ───────────────────────────────────────────────────
  'PBM':        'Pharmacy Benefit Manager — private company (e.g., CVS Caremark, Express Scripts) that administers prescription drug benefits on behalf of insurers and employers',
  'BIN':        'Bank Identification Number — 6-digit code on insurance card that routes the claim to the correct PBM processor',
  'PCN':        'Processor Control Number — secondary 3–10 character code further identifying the PBM processor segment within a BIN',
  'COB':        'Coordination of Benefits — adjudication process when a patient has multiple insurance plans; determines which pays primary vs. secondary',
  'EOB':        'Explanation of Benefits — document from the insurer detailing what was billed, approved, and paid for a claim',
  'ERA':        'Electronic Remittance Advice — electronic version of EOB (835 transaction) sent from payer to pharmacy with payment details',
  'PA':         'Prior Authorization — formal approval from the insurer required before dispensing certain drugs; if not obtained, claim will reject',
  'CMS':        'Centers for Medicare & Medicaid Services — US federal agency that administers Medicare, Medicaid, CHIP, and marketplace insurance',
  'PDE':        'Prescription Drug Event — standardized claim record submitted to CMS for every Medicare Part D drug dispensed',
  'TrOOP':      'True Out-Of-Pocket — Medicare Part D patient spending threshold ($8,000 in 2024) triggering catastrophic coverage where patient pays nothing',
  'NCPDP':      'National Council for Prescription Drug Programs — industry standards body that publishes the D.0 transaction standard used for all pharmacy claims',
  'CoPay':      'Copayment — fixed dollar amount the patient pays for each prescription (e.g., $10 for generic, $45 for brand)',

  // ── Regulatory / Compliance ──────────────────────────────────────────────
  'HIPAA':      'Health Insurance Portability and Accountability Act (1996) — US federal law protecting the privacy and security of patient health information',
  'BAA':        'Business Associate Agreement — HIPAA-required contract ensuring third-party vendors who handle PHI maintain proper safeguards',
  'PHI':        'Protected Health Information — any individually identifiable health data covered by HIPAA; includes name, DOB, diagnosis, Rx history',
  'PII':        'Personally Identifiable Information — any data that can identify a specific individual (name, address, national ID, email)',
  'OBRA':       'Omnibus Budget Reconciliation Act (1990) — US federal law that, among other provisions, requires pharmacists to offer counseling to Medicaid patients at every dispensing',
  'CFR':        'Code of Federal Regulations — the official compilation of US federal agency rules; 21 CFR governs FDA, DEA pharmacy regulations',
  'REMS':       'Risk Evaluation and Mitigation Strategy — FDA-mandated safety program for high-risk drugs (e.g., isotretinoin, clozapine) with strict dispensing controls',
  'EPCS':       'Electronic Prescribing for Controlled Substances — DEA-authorized system for prescribing Schedule II–V drugs electronically (21 CFR Part 1311)',
  'PDMP':       'Prescription Drug Monitoring Program — state-run database tracking all controlled substance prescriptions dispensed; must be queried before dispensing in most states',
  'FDA':        'Food and Drug Administration — US federal agency responsible for approving and regulating drugs, biologics, medical devices, and food',

  // ── Pharmacy Programs ────────────────────────────────────────────────────
  'MTM':        'Medication Therapy Management — pharmacist-led clinical service to optimize drug therapy, improve adherence, and reduce adverse events (billed under Medicare Part D)',
  'CMR':        'Comprehensive Medication Review — a complete, interactive review of all of a patient\'s medications during an MTM session',
  'TMR':        'Targeted Medication Review — a focused MTM review addressing a specific drug-related problem or at-risk condition',
  'MAP':        'Medication Action Plan — patient-friendly document from an MTM session listing medication-related goals and action items',
  '340B':       '340B Drug Pricing Program — US federal program (Section 340B of the PHS Act) requiring manufacturers to sell outpatient drugs at steep discounts to qualifying safety-net providers',
  'LTC':        'Long-Term Care — health and personal care services for patients who cannot care for themselves; includes nursing homes, assisted living, home health',
  'MDS':        'Minimum Data Set — standardized comprehensive assessment tool used in LTC facilities for care planning and Medicare/Medicaid reimbursement',
  'POCT':       'Point-of-Care Testing — diagnostic testing performed at or near the patient rather than in a central lab (e.g., A1C, flu, strep)',
  'GPO':        'Group Purchasing Organization — entity that negotiates volume purchasing contracts with vendors on behalf of member pharmacies or hospitals',
  'TTAC':       'Track and Trace Assistance Center — drug shortage search system (HDA); for shortage lookup only, not product authentication',

  // ── Financial / POS ──────────────────────────────────────────────────────
  'POS':        'Point of Sale — the location and system where a patient pays for their medication; also refers to real-time insurance eligibility check at dispensing',
  'HSA':        'Health Savings Account — tax-advantaged account for individuals with high-deductible health plans; can be used to pay prescription copays',
  'FSA':        'Flexible Spending Account — employer-established benefit allowing employees to set aside pre-tax dollars for healthcare expenses including prescriptions',
  'EOD':        'End of Day — daily reconciliation of cash drawer, claims, and payments',
  'AR':         'Accounts Receivable — money owed to the pharmacy by patients, insurers, or other payers',

  // ── Clinical Programs / Scores ───────────────────────────────────────────
  'MIPS':       'Merit-based Incentive Payment System — CMS program linking Medicare reimbursement to quality and performance measures',
  'HbA1c':      'Glycated Haemoglobin — blood test reflecting average blood glucose over ~3 months; goal <7% for most diabetic patients',
  'BMI':        'Body Mass Index — weight (kg) ÷ height (m²); used to classify underweight/normal/overweight/obese',
  'BP':         'Blood Pressure — force of blood against artery walls; measured as systolic/diastolic (e.g., 120/80 mmHg)',
  'INR':        'International Normalized Ratio — standardized measure of how long blood takes to clot; used to monitor warfarin dosing (target 2.0–3.0 for most indications)',
  'eGFR':       'Estimated Glomerular Filtration Rate — measure of kidney function; used to adjust drug doses in renal impairment',
  'CrCl':       'Creatinine Clearance — estimate of kidney filtration rate used for drug dosing calculations (Cockcroft-Gault equation)',

  // ── ML / Pharmacovigilance ───────────────────────────────────────────────
  'PRR':        'Proportional Reporting Ratio — pharmacovigilance disproportionality measure; signal threshold: PRR ≥ 2.0, χ² ≥ 4.0, n ≥ 3 (WHO guideline)',
  'ROR':        'Reporting Odds Ratio — pharmacovigilance signal metric; signal if ROR ≥ 2.0 and 95% CI lower bound ≥ 1.0',
  'CUSUM':      'Cumulative Sum — sequential statistical test for detecting process shifts; in PharmPilot used for anomaly detection in dispensing patterns',
  'FAISS':      'Facebook AI Similarity Search — open-source library for efficient similarity search and clustering of dense vectors',
  'ONNX':       'Open Neural Network Exchange — open format for representing machine learning models across different frameworks',
  'HOG':        'Histogram of Oriented Gradients — image feature descriptor that captures edge and gradient directions; used as CV fallback when deep learning is unavailable',
  'ML':         'Machine Learning — field of AI where algorithms learn patterns from data without explicit programming',
  'AI':         'Artificial Intelligence — computer systems that perform tasks typically requiring human intelligence (reasoning, pattern recognition, decision-making)',
  'OCR':        'Optical Character Recognition — technology that converts images of typed or printed text into machine-readable text',

  // ── Interoperability / Technical ─────────────────────────────────────────
  'FHIR':       'Fast Healthcare Interoperability Resources — HL7 R4 standard for exchanging healthcare information electronically via REST APIs',
  'HL7':        'Health Level 7 — family of international standards for clinical and administrative healthcare data exchange',
  'EDI':        'Electronic Data Interchange — structured computer-to-computer exchange of business documents (e.g., 810 invoice, 850 PO, 856 ASN)',
  'API':        'Application Programming Interface — set of rules allowing software systems to communicate with each other',
  'REST':       'Representational State Transfer — software architecture style for web APIs using HTTP methods (GET, POST, PUT, DELETE)',
  'JWT':        'JSON Web Token — compact, URL-safe token format for securely transmitting authentication/authorization data between parties',
  'RBAC':       'Role-Based Access Control — access management system that grants permissions based on user roles (pharmacist, technician, admin)',
  'WAL':        'Write-Ahead Logging — database technique where changes are logged before being applied; ensures data integrity after crashes',
  'SQL':        'Structured Query Language — programming language for managing and querying relational databases',
  'ZPL':        'Zebra Programming Language — proprietary printer language used by Zebra thermal label printers (LP2844, GK420d, ZD420)',
  'PDF':        'Portable Document Format — file format that preserves document formatting across devices and platforms',
  'QR':         'Quick Response code — 2D barcode that can store URLs, text, or structured data; scannable by smartphone camera',

  // ── EHR / Prescribing ────────────────────────────────────────────────────
  'EHR':        'Electronic Health Record — comprehensive digital record of a patient\'s health history maintained by a healthcare provider',
  'EMR':        'Electronic Medical Record — digital record of patient encounters within a single healthcare organization',
  'CDSS':       'Clinical Decision Support System — software that analyses patient data and provides evidence-based clinical recommendations',
  'ePrescribing':'Electronic Prescribing — transmission of a prescription from prescriber to pharmacy electronically via NCPDP SCRIPT 10.6 standard',

  // ── Dispensing / Workflow ────────────────────────────────────────────────
  'PMP':        'Pharmacy Management Platform — integrated software system managing the complete dispensing workflow',
  'CII':        'Schedule II Controlled Substance — highest restriction class (e.g., oxycodone, fentanyl); no refills, written Rx required (or EPCS)',
  'CIII':       'Schedule III Controlled Substance — moderate restriction (e.g., codeine combinations); maximum 5 refills in 6 months',
  'CIV':        'Schedule IV Controlled Substance — lower restriction (e.g., benzodiazepines, tramadol); maximum 5 refills in 6 months',
  'CV':         'Schedule V Controlled Substance — lowest restriction (e.g., cough preparations with small amounts of codeine)',
  'DIN':        'Drug Identification Number — Canadian equivalent of NDC; 8-digit product identifier',
  'ISMP':       'Institute for Safe Medication Practices — non-profit organization that educates healthcare professionals on medication error prevention',
  'LASA':       'Look-Alike Sound-Alike — drugs with similar names or packaging that are prone to dispensing errors',
}

/**
 * Build a regex that matches any known abbreviation as a whole word.
 * Sorted longest-first to prevent partial matches (e.g., "NDC-11" before "NDC").
 */
export const GLOSSARY_KEYS = Object.keys(GLOSSARY).sort((a, b) => b.length - a.length)

export const ABBR_REGEX = new RegExp(
  `\\b(${GLOSSARY_KEYS.map(k => k.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})\\b`,
  'g',
)

/**
 * Look up the definition for a term (case-insensitive fallback).
 */
export function lookupTerm(term: string): string | undefined {
  return GLOSSARY[term] ?? GLOSSARY[term.toUpperCase()]
}
