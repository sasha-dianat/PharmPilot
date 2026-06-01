"""
RAG (Retrieval-Augmented Generation) engine for clinical consultations.
At query time: retrieves relevant knowledge chunks → injects as context → LLM reasons.
Every answer is grounded in retrieved sources with full citations.
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import UUID, uuid4

import anthropic

from services.ai.knowledge_engine.vector_store import ClinicalVectorStore, RetrievedChunk

logger = logging.getLogger(__name__)

RAG_SYSTEM_PROMPT = """You are PharmPilot's Clinical Knowledge Engine — an expert clinical pharmacist AI
with access to a continuously updated medical knowledge base containing clinical guidelines,
drug information, pharmacology references, and peer-reviewed literature.

You answer clinical questions by:
1. Drawing on the RETRIEVED KNOWLEDGE provided to you (these are excerpts from verified sources)
2. Integrating the specific patient context provided
3. Applying clinical reasoning to synthesize a practical, actionable answer
4. ALWAYS citing the specific source for each clinical claim

CITATION FORMAT: [Source: {title}, {section}]

RULES:
- Only make claims that are supported by the retrieved knowledge or established pharmacology
- Explicitly state when a question is outside your retrieved knowledge base
- Grade your confidence: HIGH (directly in retrieved sources), MODERATE (inferred), LOW (general knowledge)
- Never invent citations or hallucinate drug information
- Flag immediately any patient safety issue regardless of the question asked"""


@dataclass
class RAGResponse:
    answer: str
    sources_used: list[RetrievedChunk]
    confidence: str          # high, moderate, low
    knowledge_gaps: list[str]  # Topics not found in knowledge base
    citations: list[dict]
    query_id: UUID
    response_time_ms: int
    chunks_retrieved: int


class ClinicalRAGEngine:
    """
    The core RAG engine that powers all clinical knowledge queries.
    Retrieves, augments, generates, and cites.
    """

    def __init__(
        self,
        vector_store: ClinicalVectorStore,
        anthropic_api_key: str,
        model: str = "claude-opus-4-5",
    ):
        self.vs = vector_store
        self.client = anthropic.Anthropic(api_key=anthropic_api_key)
        self.model = model

    async def query(
        self,
        question: str,
        patient_context: Optional[dict] = None,
        filter_drugs: Optional[list[str]] = None,
        filter_specialties: Optional[list[str]] = None,
        top_k: int = 8,
        staff_id: Optional[UUID] = None,
        prescription_id: Optional[UUID] = None,
    ) -> RAGResponse:
        """
        Answer a clinical question using RAG.
        The knowledge base grows with every ingestion — richer knowledge = better answers.
        """
        start = time.monotonic()
        query_id = uuid4()

        # Step 1: Retrieve relevant knowledge chunks
        retrieved = self.vs.search(
            query=question,
            top_k=top_k,
            score_threshold=0.50,
            filter_drugs=filter_drugs,
            filter_specialties=filter_specialties,
        )

        if not retrieved:
            # Try broader search without filters
            retrieved = self.vs.search(query=question, top_k=top_k, score_threshold=0.40)

        # Step 2: Build context from retrieved chunks
        context_parts = []
        for i, chunk in enumerate(retrieved, start=1):
            context_parts.append(
                f"[KNOWLEDGE SOURCE {i}]\n"
                f"Title: {chunk.source_title}\n"
                f"Section: {chunk.section_title or 'General'}\n"
                f"Relevance: {chunk.similarity_score:.2f}\n"
                f"Evidence: {chunk.evidence_grade or 'Not graded'}\n"
                f"Content: {chunk.content}\n"
            )
        knowledge_context = "\n---\n".join(context_parts) if context_parts else ""

        # Step 3: Build patient context string
        patient_context_str = ""
        if patient_context:
            pc = patient_context
            patient_context_str = f"""
PATIENT CONTEXT:
- Age: {pc.get('age', 'Unknown')}, Gender: {pc.get('gender', 'Unknown')}
- eGFR: {pc.get('egfr', 'Not available')} mL/min/1.73m²
- Active medications ({len(pc.get('active_medications', []))}): {', '.join(m.get('drug_name', '') for m in pc.get('active_medications', [])[:10])}
- Allergies: {', '.join(a.get('allergen_name', '') for a in pc.get('allergies', []))}
- Diagnoses: {', '.join(pc.get('diagnoses', [])[:8])}
- Pregnancy status: {pc.get('pregnancy_status', 'Not documented')}
"""

        # Step 4: LLM synthesis
        user_message = f"""CLINICAL QUESTION:
{question}
{patient_context_str}
RETRIEVED KNOWLEDGE BASE ({len(retrieved)} sources):
{knowledge_context if knowledge_context else "No directly matching knowledge found in database. Answering from general clinical knowledge."}

Please provide a comprehensive clinical answer with citations to the sources above."""

        response = self.client.messages.create(
            model=self.model,
            max_tokens=2000,
            system=RAG_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )

        answer = response.content[0].text
        elapsed_ms = int((time.monotonic() - start) * 1000)

        # Extract citations
        citations = [
            {
                "source_number": i + 1,
                "title": chunk.source_title,
                "section": chunk.section_title,
                "url": chunk.url,
                "doi": chunk.doi,
                "evidence_grade": chunk.evidence_grade,
                "similarity_score": chunk.similarity_score,
            }
            for i, chunk in enumerate(retrieved)
        ]

        # Detect knowledge gaps
        knowledge_gaps = []
        if not retrieved:
            knowledge_gaps.append(
                f"No matching content for '{question[:80]}' in knowledge base. "
                "Consider ingesting relevant guidelines or articles."
            )

        confidence = (
            "high" if retrieved and retrieved[0].similarity_score > 0.75 else
            "moderate" if retrieved and retrieved[0].similarity_score > 0.60 else
            "low"
        )

        logger.info(
            "RAG query answered in %dms | %d sources | confidence=%s",
            elapsed_ms, len(retrieved), confidence,
        )

        return RAGResponse(
            answer=answer,
            sources_used=retrieved,
            confidence=confidence,
            knowledge_gaps=knowledge_gaps,
            citations=citations,
            query_id=query_id,
            response_time_ms=elapsed_ms,
            chunks_retrieved=len(retrieved),
        )

    async def query_drug_interaction(
        self,
        drug_a: str,
        drug_b: str,
        patient_context: Optional[dict] = None,
    ) -> RAGResponse:
        """Specialized drug interaction query."""
        question = (
            f"What is the clinical significance of the drug interaction between "
            f"{drug_a} and {drug_b}? Include mechanism, severity, clinical consequences, "
            f"monitoring parameters, and management recommendations."
        )
        return await self.query(
            question=question,
            patient_context=patient_context,
            filter_drugs=[drug_a.lower(), drug_b.lower()],
            top_k=10,
        )

    async def query_dosing(
        self,
        drug: str,
        indication: str,
        patient_context: Optional[dict] = None,
    ) -> RAGResponse:
        """Specialized dosing query with patient-specific adjustments."""
        question = (
            f"What is the recommended dosing for {drug} for {indication}? "
            f"Include initial dose, titration, maintenance dose, maximum dose, "
            f"renal/hepatic adjustment requirements, and monitoring parameters."
        )
        return await self.query(
            question=question,
            patient_context=patient_context,
            filter_drugs=[drug.lower()],
            top_k=8,
        )

    async def query_adverse_effects(self, drug: str) -> RAGResponse:
        """Query for adverse effects and monitoring."""
        question = (
            f"What are the clinically significant adverse effects of {drug}? "
            f"Include frequency, severity, monitoring parameters, and management of each."
        )
        return await self.query(question=question, filter_drugs=[drug.lower()], top_k=8)

    async def query_guideline(
        self,
        condition: str,
        aspect: str = "pharmacotherapy",
    ) -> RAGResponse:
        """Query current guideline recommendations for a condition."""
        question = (
            f"What do current clinical guidelines recommend for {aspect} of {condition}? "
            f"Include first-line agents, alternatives, monitoring, and key evidence."
        )
        return await self.query(question=question, top_k=10)

    def get_knowledge_base_stats(self) -> dict:
        """Return statistics about the current knowledge base."""
        try:
            return self.vs.get_stats()
        except Exception as exc:
            return {"error": str(exc)}
