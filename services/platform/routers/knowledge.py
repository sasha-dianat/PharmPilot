"""
Knowledge Engine API — ingest documents, query the knowledge base,
manage sources. The richer the knowledge base, the smarter the ACB becomes.
"""
import logging
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.config import settings
from services.platform.database import get_db
from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline
from services.ai.knowledge_engine.retrieval.rag_engine import ClinicalRAGEngine
from services.ai.knowledge_engine.schema import SourceType
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)


def _get_vector_store() -> ClinicalVectorStore:
    return ClinicalVectorStore(
        qdrant_url=getattr(settings, "QDRANT_URL", "http://localhost:6333"),
    )


def _get_rag_engine() -> ClinicalRAGEngine:
    vs = _get_vector_store()
    return ClinicalRAGEngine(
        vector_store=vs,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
    )


# ── Ingestion endpoints ───────────────────────────────────────────────────────

@router.post("/ingest/pdf", status_code=202)
async def ingest_pdf(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    source_type: str = Form("custom_document"),
    evidence_level: Optional[str] = Form(None),
    specialty_tags: Optional[str] = Form(None),  # comma-separated
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a PDF document to the clinical knowledge base.
    Accepts: guidelines, package inserts, textbook chapters, research papers, etc.
    Content is immediately searchable after processing (~30 seconds for typical document).
    """
    import tempfile, os
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name

    try:
        tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
        pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
        result = await pipeline.ingest_pdf(
            pdf_path=tmp_path,
            source_type=SourceType(source_type),
            title=title or file.filename,
            evidence_level=evidence_level,
            specialty_tags=tags,
        )
        return {
            "status": result["status"],
            "source_id": result.get("source_id"),
            "chunks_stored": result.get("chunks_stored", 0),
            "message": f"Successfully ingested {result.get('chunks_stored', 0)} knowledge chunks.",
        }
    finally:
        os.unlink(tmp_path)


@router.post("/ingest/docx", status_code=202)
async def ingest_docx(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    language: str = Form("fa"),
    collection: str = Form("owner_references"),
    evidence_level: Optional[str] = Form(None),
    specialty_tags: Optional[str] = Form(None),
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Owner-fed reference upload: a Word .docx (Iranian pharmacopeia monograph,
    formulary, SOP). Persian-aware; tagged into the owner-reference corpus and
    immediately searchable by the clinical brain.
    """
    import tempfile, os
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
        pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
        result = await pipeline.ingest_docx(
            docx_path=tmp_path,
            title=title or file.filename,
            language=language,
            collection=collection,
            evidence_level=evidence_level,
            specialty_tags=tags,
        )
        return {
            "status": result["status"],
            "source_id": result.get("source_id"),
            "chunks_stored": result.get("chunks_stored", 0),
            "message": f"Ingested {result.get('chunks_stored', 0)} chunks from {file.filename}.",
        }
    finally:
        os.unlink(tmp_path)


class CrawlReferenceRequest(BaseModel):
    start_url: str
    max_pages: int = 200
    same_domain_only: bool = True
    language: str = "fa"
    collection: str = "owner_references"


@router.post("/ingest/crawl", status_code=202)
async def crawl_reference_site(
    body: CrawlReferenceRequest,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Owner-fed reference crawler: point at a public Iranian pharmacopeia / formulary
    site and BFS-ingest its pages into the owner-reference corpus. Same-domain by
    default, rate-limited, Persian-aware.
    """
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.crawl_site(
        start_url=body.start_url,
        max_pages=body.max_pages,
        same_domain_only=body.same_domain_only,
        language=body.language,
        collection=body.collection,
    )
    return result


@router.post("/ingest/url", status_code=202)
async def ingest_url(
    url: str,
    title: Optional[str] = None,
    source_type: str = "web_crawl",
    session_cookie_json: Optional[str] = None,  # JSON-encoded cookies for authenticated sites
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch and ingest a web page into the knowledge base.
    For authenticated sites (UpToDate, Epocrates), provide session cookies.
    """
    import json
    cookies = json.loads(session_cookie_json) if session_cookie_json else None
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_url(
        url=url,
        source_type=SourceType(source_type),
        title=title,
        session_cookies=cookies,
    )
    return result


@router.post("/ingest/uptodate/crawl")
async def crawl_uptodate(
    session_cookie_json: str,
    max_articles: int = 1000,
    start_url: str = "https://www.uptodate.com/contents/table-of-contents",
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Autonomously crawl and ingest the entire UpToDate library.
    Requires active session cookies from a logged-in UpToDate browser session.

    HOW TO GET SESSION COOKIES:
    1. Log in to UpToDate in Chrome/Firefox
    2. Open DevTools → Application → Cookies → www.uptodate.com
    3. Export relevant cookies as JSON and paste here

    The system will then crawl every accessible article and ingest it.
    Progress can be monitored via /knowledge/stats.
    """
    import json
    cookies = json.loads(session_cookie_json)
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)

    # Run in background — this can take hours for a full library crawl
    import asyncio
    asyncio.create_task(
        pipeline.ingest_uptodate_library_crawl(
            session_cookies=cookies,
            start_url=start_url,
            max_articles=max_articles,
        )
    )
    return {
        "status": "started",
        "message": f"UpToDate crawl started. Target: {max_articles} articles. Check /knowledge/stats for progress.",
    }


@router.post("/ingest/pubmed")
async def ingest_pubmed(
    search_query: str,
    max_results: int = 100,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Search PubMed and ingest all matching abstracts.
    PubMed is free and requires no authentication.

    Example queries:
    - "warfarin drug interactions systematic review"
    - "metformin renal impairment dosing"
    - "SGLT2 inhibitor heart failure[MeSH]"
    """
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_pubmed_search(search_query, max_results)
    return result


@router.post("/ingest/text")
async def ingest_text(
    title: str,
    content: str,
    source_type: str = "custom_document",
    specialty_tags: Optional[str] = None,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Paste raw text directly into the knowledge base.
    The fastest way to add content — paste any clinical reference material.
    """
    tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_raw_text(
        text=content,
        title=title,
        source_type=SourceType(source_type),
        specialty_tags=tags,
    )
    return result


# ── Query endpoints ───────────────────────────────────────────────────────────

class ClinicalQueryRequest(BaseModel):
    question: str
    patient_id: Optional[UUID] = None
    prescription_id: Optional[UUID] = None
    filter_drugs: Optional[list[str]] = None
    filter_specialties: Optional[list[str]] = None
    top_k: int = 8


@router.post("/query")
async def query_knowledge_base(
    body: ClinicalQueryRequest,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    """
    Query the clinical knowledge base with a natural language question.
    Returns an AI-synthesized answer with full citations to source documents.
    The quality of the answer improves as more knowledge is ingested.
    """
    if not settings.ANTHROPIC_API_KEY:
        raise HTTPException(503, "ANTHROPIC_API_KEY not configured")

    # Load patient context if provided
    patient_context = None
    if body.patient_id:
        from sqlalchemy import select
        from shared.models.patient import Patient, PatientAllergy, LabResult
        patient_result = await db.execute(select(Patient).where(Patient.id == body.patient_id))
        patient = patient_result.scalar_one_or_none()
        if patient:
            from datetime import date
            age = (date.today() - patient.date_of_birth).days // 365
            allergies_result = await db.execute(
                select(PatientAllergy).where(PatientAllergy.patient_id == body.patient_id)
            )
            labs_result = await db.execute(
                select(LabResult).where(LabResult.patient_id == body.patient_id).limit(10)
            )
            egfr = None
            for lab in labs_result.scalars().all():
                if "egfr" in lab.test_name.lower() or "gfr" in lab.test_name.lower():
                    try:
                        egfr = float(lab.value)
                    except ValueError:
                        pass
            patient_context = {
                "age": age,
                "gender": patient.gender,
                "egfr": egfr,
                "allergies": [{"allergen_name": a.allergen_name} for a in allergies_result.scalars().all()],
                "active_medications": [],
                "diagnoses": [],
            }

    rag = _get_rag_engine()
    response = await rag.query(
        question=body.question,
        patient_context=patient_context,
        filter_drugs=body.filter_drugs,
        filter_specialties=body.filter_specialties,
        top_k=body.top_k,
        staff_id=staff.id,
        prescription_id=body.prescription_id,
    )

    return {
        "query_id": str(response.query_id),
        "answer": response.answer,
        "confidence": response.confidence,
        "sources_retrieved": response.chunks_retrieved,
        "citations": response.citations,
        "knowledge_gaps": response.knowledge_gaps,
        "response_time_ms": response.response_time_ms,
    }


@router.post("/query/drug-interaction")
async def query_drug_interaction(
    drug_a: str,
    drug_b: str,
    patient_id: Optional[UUID] = None,
    staff: Staff = Depends(require_permission("clinical:read")),
    db: AsyncSession = Depends(get_db),
):
    """Query the knowledge base for a specific drug-drug interaction."""
    rag = _get_rag_engine()
    response = await rag.query_drug_interaction(drug_a=drug_a, drug_b=drug_b)
    return {
        "drug_a": drug_a,
        "drug_b": drug_b,
        "answer": response.answer,
        "confidence": response.confidence,
        "citations": response.citations,
    }


@router.post("/query/dosing")
async def query_dosing(
    drug: str,
    indication: str,
    patient_id: Optional[UUID] = None,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Query for drug dosing recommendations."""
    rag = _get_rag_engine()
    response = await rag.query_dosing(drug=drug, indication=indication)
    return {
        "drug": drug,
        "indication": indication,
        "answer": response.answer,
        "confidence": response.confidence,
        "citations": response.citations,
    }


# ── Management endpoints ──────────────────────────────────────────────────────

@router.get("/stats")
async def get_knowledge_stats(
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """Get statistics about the current knowledge base."""
    vs = _get_vector_store()
    try:
        stats = vs.get_stats()
        return {
            **stats,
            "status": "operational",
            "message": f"Knowledge base contains {stats.get('total_vectors', 0):,} indexed passages.",
        }
    except Exception as exc:
        return {"status": "unavailable", "error": str(exc)}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str,
    staff: Staff = Depends(require_permission("clinical:write")),
):
    """Remove a knowledge source (e.g., outdated guideline version)."""
    vs = _get_vector_store()
    vs.delete_source(source_id)
    return {"status": "deleted", "source_id": source_id}
