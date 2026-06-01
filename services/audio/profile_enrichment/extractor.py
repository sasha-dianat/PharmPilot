"""
AI-driven clinical information extractor from pharmacy conversation transcripts.
Uses NLP to identify medically relevant information spoken during counter interactions,
counseling sessions, or consultations — and generates profile update proposals
for pharmacist review before any data is committed to the patient record.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass
class ExtractedClinicalItem:
    category: str           # allergy, medication, condition, concern, lab_value, preference
    extracted_text: str     # Raw text from transcript
    structured_value: dict  # Parsed, structured representation
    confidence: float
    source_quote: str       # Exact transcript text that triggered extraction
    timestamp_seconds: float


@dataclass
class TranscriptExtractionResult:
    patient_id: Optional[UUID]
    transcript_id: UUID
    extracted_items: list[ExtractedClinicalItem] = field(default_factory=list)
    action_items: list[dict] = field(default_factory=list)
    sentiment: float = 0.0   # -1 (negative) to +1 (positive)
    urgency_flag: bool = False
    urgency_reason: Optional[str] = None


class ClinicalInformationExtractor:
    """
    Extracts clinically relevant information from diarized pharmacy transcripts.

    Extraction categories:
    - New allergies or adverse reactions mentioned verbally
    - New medications mentioned (not in current profile)
    - New or updated diagnoses/conditions
    - Lab values mentioned (A1c, blood pressure, cholesterol numbers)
    - Patient concerns and questions
    - Adherence information (stopped taking, forgot doses)
    - Pregnancy / breastfeeding status changes
    - Patient preferences and communication needs
    """

    # Regex + semantic patterns for each extraction category
    ALLERGY_PATTERNS = [
        r"(?:allergic|allergy|reaction|broke out|rash|hives|swelling|anaphylaxis).{0,50}(?:to|from|with)\s+([A-Za-z\s]+)",
        r"(?:can't|cannot|don't|doesn't).{0,20}take\s+([A-Za-z\s]+).{0,30}(?:allerg|reaction|sick)",
        r"([A-Za-z\s]+)\s+(?:gives?|gave|makes?|made)\s+(?:me|him|her|them)\s+(?:a|an)?\s*(?:rash|hives|reaction|sick)",
    ]

    MEDICATION_PATTERNS = [
        r"(?:also taking|taking|on|started|began|prescribed)\s+([A-Za-z\s]+?)\s+(?:for|to|mg|mcg|daily|twice)",
        r"(?:my|his|her|their)\s+(?:doctor|physician|cardiologist|endocrinologist|neurologist|psychiatrist)\s+(?:put|started|added)\s+(?:me|him|her|them)\s+on\s+([A-Za-z\s]+)",
        r"(?:stopped|discontinued|no longer taking|quit taking)\s+(?:the\s+)?([A-Za-z\s]+)",
    ]

    CONDITION_PATTERNS = [
        r"(?:diagnosed with|have|has|history of|suffer from|been told I have)\s+([A-Za-z\s]+(?:disease|disorder|syndrome|condition|failure|diabetes|hypertension|cancer|depression|anxiety|asthma|copd|afib|fibrillation))",
        r"(?:my|his|her|their)\s+([A-Za-z]+(?:etes|ension|oma|itis|osis|emia))",
    ]

    LAB_VALUE_PATTERNS = [
        r"(?:a1c|A1C|hemoglobin a1c)\s+(?:is|was|came back)\s+([\d.]+)",
        r"(?:blood pressure|BP)\s+(?:is|was|reading)\s+([\d]+/[\d]+)",
        r"(?:cholesterol|LDL|HDL|triglycerides)\s+(?:is|was|came back)\s+([\d]+)",
        r"(?:eGFR|creatinine|kidney function)\s+(?:is|was|dropped to|at)\s+([\d.]+)",
        r"(?:blood sugar|glucose|fasting glucose)\s+(?:is|was|reading)\s+([\d]+)",
    ]

    ADHERENCE_PATTERNS = [
        r"(?:forget|forgot|miss|missed|skip|skipped|don't always take|run out)",
        r"(?:stopped taking|quit taking|discontinued|no longer taking)",
        r"(?:side effects?|bothers?|makes? me (?:sick|nauseous|dizzy|tired))",
        r"(?:can't afford|too expensive|insurance won't cover|out of pocket)",
        r"(?:hard to remember|difficult to take|trouble taking)",
    ]

    PREGNANCY_PATTERNS = [
        r"(?:pregnant|pregnancy|expecting|due|trimester|breastfeeding|nursing|breast feeding)",
        r"(?:trying to (?:get pregnant|conceive|have a baby))",
    ]

    URGENCY_PATTERNS = [
        r"(?:chest pain|shortness of breath|can't breathe|stroke|heart attack|seizure|unconscious|emergency)",
        r"(?:thought about hurting|want to hurt|suicidal|end my life|kill myself)",
        r"(?:severe reaction|can't swallow|throat closing|anaphylaxis|epi pen)",
        r"(?:overdose|took too many|accidental|gave wrong)",
    ]

    def __init__(self, drug_database_client=None):
        self._drug_db = drug_database_client
        self._nlp_model = None

    def _load_nlp(self):
        if self._nlp_model is None:
            import spacy
            try:
                self._nlp_model = spacy.load("en_core_web_trf")
            except OSError:
                import subprocess
                subprocess.run(["python", "-m", "spacy", "download", "en_core_web_trf"], check=True)
                self._nlp_model = spacy.load("en_core_web_trf")

    def extract(
        self,
        full_transcript: str,
        diarized_segments: list[dict],
        patient_id: Optional[UUID],
        transcript_id: UUID,
    ) -> TranscriptExtractionResult:

        self._load_nlp()

        # Only extract from patient/caregiver speech, not staff speech
        patient_text_segments = [
            seg for seg in diarized_segments
            if seg.get("role") in ("patient", "caregiver", "unknown")
        ]

        result = TranscriptExtractionResult(
            patient_id=patient_id,
            transcript_id=transcript_id,
        )

        all_patient_text = " ".join(s.get("text", "") for s in patient_text_segments)

        # Check urgency first
        for pattern in self.URGENCY_PATTERNS:
            match = re.search(pattern, all_patient_text, re.IGNORECASE)
            if match:
                result.urgency_flag = True
                result.urgency_reason = f"Urgency keyword detected: '{match.group()}'"
                logger.warning("URGENCY FLAG in transcript %s: %s", transcript_id, result.urgency_reason)
                break

        # Extract per category
        for seg in patient_text_segments:
            text = seg.get("text", "")
            ts = seg.get("start", 0.0)

            result.extracted_items.extend(
                self._extract_allergies(text, ts)
            )
            result.extracted_items.extend(
                self._extract_medications(text, ts)
            )
            result.extracted_items.extend(
                self._extract_conditions(text, ts)
            )
            result.extracted_items.extend(
                self._extract_lab_values(text, ts)
            )
            result.extracted_items.extend(
                self._extract_adherence_info(text, ts)
            )
            result.extracted_items.extend(
                self._extract_pregnancy_status(text, ts)
            )

        # Generate action items for pharmacist
        result.action_items = self._generate_action_items(result.extracted_items)

        # Sentiment analysis
        result.sentiment = self._analyze_sentiment(all_patient_text)

        logger.info(
            "Extraction complete for transcript %s: %d items, urgency=%s",
            transcript_id,
            len(result.extracted_items),
            result.urgency_flag,
        )
        return result

    def _extract_allergies(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.ALLERGY_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                drug_candidate = match.group(1).strip() if match.lastindex else ""
                if drug_candidate and len(drug_candidate) > 2:
                    items.append(ExtractedClinicalItem(
                        category="allergy",
                        extracted_text=drug_candidate,
                        structured_value={
                            "allergen_name": drug_candidate,
                            "allergen_type": "drug",
                            "source": "verbal_transcript",
                            "reaction": self._extract_reaction_text(text, drug_candidate),
                        },
                        confidence=0.75,
                        source_quote=text[:200],
                        timestamp_seconds=timestamp,
                    ))
        return items

    def _extract_medications(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.MEDICATION_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                drug_candidate = match.group(1).strip() if match.lastindex else ""
                if drug_candidate and len(drug_candidate) > 3:
                    stopped = bool(re.search(r"stop|quit|discontinu|no longer", text[:match.start()], re.IGNORECASE))
                    items.append(ExtractedClinicalItem(
                        category="medication",
                        extracted_text=drug_candidate,
                        structured_value={
                            "drug_name": drug_candidate,
                            "status": "discontinued" if stopped else "active",
                            "source": "verbal_transcript",
                        },
                        confidence=0.70,
                        source_quote=text[:200],
                        timestamp_seconds=timestamp,
                    ))
        return items

    def _extract_conditions(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.CONDITION_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                condition = match.group(1).strip() if match.lastindex else ""
                if condition and len(condition) > 3:
                    items.append(ExtractedClinicalItem(
                        category="condition",
                        extracted_text=condition,
                        structured_value={
                            "condition_name": condition,
                            "source": "verbal_transcript",
                        },
                        confidence=0.65,
                        source_quote=text[:200],
                        timestamp_seconds=timestamp,
                    ))
        return items

    def _extract_lab_values(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.LAB_VALUE_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                test_name = re.sub(r"(?:is|was|came back|reading|at|dropped to|=).*", "", match.group()).strip()
                value = match.group(1) if match.lastindex else ""
                items.append(ExtractedClinicalItem(
                    category="lab_value",
                    extracted_text=match.group(),
                    structured_value={
                        "test_name": test_name,
                        "value": value,
                        "source": "verbal_transcript",
                    },
                    confidence=0.85,  # Lab values are specific — higher confidence
                    source_quote=text[:200],
                    timestamp_seconds=timestamp,
                ))
        return items

    def _extract_adherence_info(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.ADHERENCE_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                items.append(ExtractedClinicalItem(
                    category="adherence_concern",
                    extracted_text=text[:100],
                    structured_value={
                        "concern_type": "adherence",
                        "description": text[:300],
                        "source": "verbal_transcript",
                    },
                    confidence=0.70,
                    source_quote=text[:200],
                    timestamp_seconds=timestamp,
                ))
                break  # One adherence flag per segment
        return items

    def _extract_pregnancy_status(self, text: str, timestamp: float) -> list[ExtractedClinicalItem]:
        items = []
        for pattern in self.PREGNANCY_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                nursing = bool(re.search(r"nursing|breastfeed|breast feed", text, re.IGNORECASE))
                trying = bool(re.search(r"trying to", text, re.IGNORECASE))
                items.append(ExtractedClinicalItem(
                    category="pregnancy_status",
                    extracted_text=text[:100],
                    structured_value={
                        "status": "breastfeeding" if nursing else ("trying_to_conceive" if trying else "pregnant"),
                        "source": "verbal_transcript",
                    },
                    confidence=0.80,
                    source_quote=text[:200],
                    timestamp_seconds=timestamp,
                ))
                break
        return items

    def _extract_reaction_text(self, text: str, drug_name: str) -> Optional[str]:
        pattern = rf"{re.escape(drug_name)}.{{0,100}}(?:rash|hives|swelling|nausea|vomiting|reaction|sick|dizzy)"
        match = re.search(pattern, text, re.IGNORECASE)
        return match.group() if match else None

    def _generate_action_items(self, items: list[ExtractedClinicalItem]) -> list[dict]:
        actions = []
        for item in items:
            if item.category == "allergy":
                actions.append({
                    "type": "add_allergy",
                    "priority": "high",
                    "description": f"New allergy mentioned: {item.extracted_text}",
                    "requires_pharmacist_review": True,
                    "extracted_item": item.structured_value,
                })
            elif item.category == "medication":
                actions.append({
                    "type": "update_medication_list",
                    "priority": "medium",
                    "description": f"Medication update: {item.extracted_text}",
                    "requires_pharmacist_review": True,
                    "extracted_item": item.structured_value,
                })
            elif item.category == "lab_value":
                actions.append({
                    "type": "add_lab_result",
                    "priority": "medium",
                    "description": f"Lab value mentioned: {item.extracted_text}",
                    "requires_pharmacist_review": True,
                    "extracted_item": item.structured_value,
                })
            elif item.category == "pregnancy_status":
                actions.append({
                    "type": "update_pregnancy_status",
                    "priority": "high",
                    "description": f"Pregnancy status mentioned: {item.structured_value.get('status')}",
                    "requires_pharmacist_review": True,
                    "extracted_item": item.structured_value,
                })
        return actions

    def _analyze_sentiment(self, text: str) -> float:
        try:
            from transformers import pipeline as hf_pipeline
            classifier = hf_pipeline(
                "sentiment-analysis",
                model="distilbert-base-uncased-finetuned-sst-2-english",
            )
            result = classifier(text[:512])[0]
            score = result["score"]
            return score if result["label"] == "POSITIVE" else -score
        except Exception:
            return 0.0
