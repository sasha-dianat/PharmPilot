"""
Knowledge Engine API — ingest documents, query the knowledge base,
manage sources. The richer the knowledge base, the smarter the ACB becomes.
"""
import asyncio
import inspect
import logging
import os
import tempfile
from datetime import datetime, timezone
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.auth import get_current_staff, require_permission
from services.platform.config import settings
from services.platform.database import AsyncSessionLocal, get_db
from services.core.pharmacy_workflow.patient_context import load_active_medications_and_diagnoses
from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline
from services.ai.knowledge_engine.retrieval.rag_engine import ClinicalRAGEngine
from services.ai.knowledge_engine.schema import SourceType
from shared.models.auth import Staff

router = APIRouter()
logger = logging.getLogger(__name__)

INGEST_JOBS: dict[str, dict[str, Any]] = {}

# Strong references to in-flight ingest tasks. asyncio only keeps weak
# references to tasks, so a long-running ingest spawned via create_task can be
# garbage-collected mid-execution if nothing else holds it. Retain each task
# until it finishes, then drop it.
_INGEST_TASKS: set[asyncio.Task] = set()


def _spawn_ingest_task(coro) -> None:
    task = asyncio.create_task(coro)
    _INGEST_TASKS.add(task)
    task.add_done_callback(_INGEST_TASKS.discard)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _register_ingest_job(filename: str | None) -> dict[str, Any]:
    job_id = str(uuid4())
    job = {
        "job_id": job_id,
        "status": "queued",
        "filename": filename or "upload",
        "chunks_stored": 0,
        "pages_total": None,
        "pages_done": 0,
        "error": None,
        "started_at": None,
        "finished_at": None,
    }
    INGEST_JOBS[job_id] = job
    return job


def _update_ingest_job(job_id: str, **updates: Any) -> None:
    job = INGEST_JOBS.get(job_id)
    if not job:
        return
    job.update(updates)


def _method_accepts_kwarg(method: Callable[..., Any], kwarg: str) -> bool:
    params = inspect.signature(method).parameters
    return kwarg in params or any(
        param.kind is inspect.Parameter.VAR_KEYWORD
        for param in params.values()
    )


async def _save_upload_to_temp_file(file: UploadFile, suffix: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp_path = tmp.name
        while chunk := await file.read(1024 * 1024):
            tmp.write(chunk)
    return tmp_path


async def _run_ingest_job(
    job_id: str,
    ingest_method: str,
    temp_path: str,
    ingest_kwargs: dict[str, Any],
    pipeline_factory: Optional[Callable[[AsyncSession], Any]] = None,
    session_factory: Callable[[], Any] = AsyncSessionLocal,
    remove_temp_file: bool = True,
) -> None:
    _update_ingest_job(
        job_id,
        status="running",
        started_at=_utc_now_iso(),
        finished_at=None,
        error=None,
    )

    try:
        async with session_factory() as db:
            try:
                pipeline = (
                    pipeline_factory(db)
                    if pipeline_factory
                    else KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
                )
                method = getattr(pipeline, ingest_method)
                call_kwargs = dict(ingest_kwargs)

                if _method_accepts_kwarg(method, "progress_callback"):
                    call_kwargs["progress_callback"] = lambda progress: _update_ingest_job(
                        job_id,
                        **{
                            key: value
                            for key, value in progress.items()
                            if key in {"pages_total", "pages_done"}
                        },
                    )

                result = await method(**call_kwargs)
                if hasattr(db, "commit"):
                    await db.commit()
            except Exception:
                if hasattr(db, "rollback"):
                    await db.rollback()
                raise

        _update_ingest_job(
            job_id,
            status="completed",
            chunks_stored=result.get("chunks_stored", 0),
            finished_at=_utc_now_iso(),
        )
    except Exception as exc:
        logger.exception("Knowledge ingest job %s failed", job_id)
        _update_ingest_job(
            job_id,
            status="failed",
            error=str(exc),
            finished_at=_utc_now_iso(),
        )
    finally:
        if remove_temp_file:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass


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
):
    """
    Upload a PDF document to the clinical knowledge base.
    Accepts: guidelines, package inserts, textbook chapters, research papers, etc.
    Content is immediately searchable after processing (~30 seconds for typical document).
    """
    parsed_source_type = SourceType(source_type)
    tmp_path = await _save_upload_to_temp_file(file, ".pdf")
    tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
    job = _register_ingest_job(file.filename)
    _spawn_ingest_task(_run_ingest_job(
        job_id=job["job_id"],
        ingest_method="ingest_pdf",
        temp_path=tmp_path,
        ingest_kwargs={
            "pdf_path": tmp_path,
            "source_type": parsed_source_type,
            "title": title or file.filename,
            "evidence_level": evidence_level,
            "specialty_tags": tags,
        },
    ))
    return {"job_id": job["job_id"], "status": "queued"}


@router.post("/ingest/docx", status_code=202)
async def ingest_docx(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    language: str = Form("fa"),
    collection: str = Form("owner_references"),
    evidence_level: Optional[str] = Form(None),
    specialty_tags: Optional[str] = Form(None),
    staff: Staff = Depends(require_permission("clinical:write")),
):
    """
    Owner-fed reference upload: a Word .docx (Iranian pharmacopeia monograph,
    formulary, SOP). Persian-aware; tagged into the owner-reference corpus and
    immediately searchable by the clinical brain.
    """
    tmp_path = await _save_upload_to_temp_file(file, ".docx")
    tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
    job = _register_ingest_job(file.filename)
    _spawn_ingest_task(_run_ingest_job(
        job_id=job["job_id"],
        ingest_method="ingest_docx",
        temp_path=tmp_path,
        ingest_kwargs={
            "docx_path": tmp_path,
            "title": title or file.filename,
            "language": language,
            "collection": collection,
            "evidence_level": evidence_level,
            "specialty_tags": tags,
        },
    ))
    return {"job_id": job["job_id"], "status": "queued"}


@router.get("/ingest/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    staff: Staff = Depends(require_permission("clinical:write")),
):
    job = INGEST_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Ingest job not found")
    return dict(job)


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


@router.post("/ingest/file", status_code=202)
async def ingest_any_file(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    language: str = Form("fa"),
    collection: str = Form("owner_references"),
    evidence_level: Optional[str] = Form(None),
    specialty_tags: Optional[str] = Form(None),
    staff: Staff = Depends(require_permission("clinical:write")),
):
    """
    Universal file upload endpoint — auto-detects format by extension.
    Supported: PDF, DOCX/DOC, MD, TXT, HTML, CSV/TSV, RTF, EPUB, JSON/JSONL.
    Persian-aware throughout. Use this for any owner-fed pharmacopeia reference.
    """
    suffix = os.path.splitext(file.filename or "")[1].lower() or ".txt"
    tmp_path = await _save_upload_to_temp_file(file, suffix)
    tags = [t.strip() for t in specialty_tags.split(",")] if specialty_tags else []
    job = _register_ingest_job(file.filename)
    _spawn_ingest_task(_run_ingest_job(
        job_id=job["job_id"],
        ingest_method="ingest_file",
        temp_path=tmp_path,
        ingest_kwargs={
            "file_path": tmp_path,
            "title": title or file.filename,
            "language": language,
            "collection": collection,
            "evidence_level": evidence_level,
            "specialty_tags": tags,
        },
    ))
    return {"job_id": job["job_id"], "status": "queued"}


class DirectoryImportRequest(BaseModel):
    directory: str
    recursive: bool = True
    language: str = "fa"
    collection: str = "owner_references"


@router.post("/ingest/directory", status_code=202)
async def ingest_directory(
    body: DirectoryImportRequest,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Batch-ingest an entire directory of reference documents (server-side path).
    Recursively processes all supported formats (PDF/DOCX/MD/TXT/HTML/CSV/RTF/EPUB/JSON).
    Use with the scripts/import_references.py CLI for large libraries.
    """
    from pathlib import Path
    if not Path(body.directory).is_dir():
        raise HTTPException(404, f"Directory not found: {body.directory}")
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_directory(
        directory=body.directory,
        recursive=body.recursive,
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


class PubMedIngestRequest(BaseModel):
    # Both names are accepted (see ingest_pubmed below) — neither is required
    # on its own so a body supplying only one of them still validates. The
    # handler raises 422 if BOTH are missing/blank.
    query: Optional[str] = None             # frontend sends "query"
    search_query: Optional[str] = None      # alternate name accepted too (legacy CLI)
    max_results: int = 100
    language: str = "en"
    collection: str = "owner_references"


@router.post("/ingest/pubmed")
async def ingest_pubmed(
    body: PubMedIngestRequest,
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
    # Accept both "query" (frontend) and "search_query" (legacy CLI) field names
    q = body.search_query or body.query
    if not q:
        raise HTTPException(422, "Must provide 'query' or 'search_query'")
    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_pubmed_search(q, body.max_results)
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
            clinical_context = await load_active_medications_and_diagnoses(db, body.patient_id)
            patient_context = {
                "age": age,
                "gender": patient.gender,
                "egfr": egfr,
                "allergies": [{"allergen_name": a.allergen_name} for a in allergies_result.scalars().all()],
                "active_medications": clinical_context["active_medications"],
                "diagnoses": clinical_context["diagnoses"],
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


@router.get("/sources")
async def list_sources(
    limit: int = 100,
    staff: Staff = Depends(require_permission("clinical:read")),
):
    """
    List all ingested knowledge sources with metadata.
    Used by the Knowledge Manager UI to show the training library.
    """
    vs = _get_vector_store()
    try:
        # Scroll Qdrant points to collect unique source metadata
        from qdrant_client.models import Filter
        client = vs._get_client()
        collection = vs.collection

        # Scroll through points and collect unique source_ids
        seen: dict[str, dict] = {}
        offset = None
        while len(seen) < limit:
            scroll_result = client.scroll(
                collection_name=collection,
                limit=500,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            points, next_offset = scroll_result
            if not points:
                break
            for pt in points:
                payload = pt.payload or {}
                sid = payload.get("source_id", "")
                if sid and sid not in seen:
                    seen[sid] = {
                        "source_id": sid,
                        "title": payload.get("source_title", "—"),
                        "source_type": payload.get("source_type", "—"),
                        "language": payload.get("language", "en"),
                        "collection": payload.get("collection", "—"),
                        "url": payload.get("url"),
                        "file_path": payload.get("file_path"),
                    }
            if next_offset is None:
                break
            offset = next_offset

        # Count chunks per source
        chunk_counts: dict[str, int] = {}
        for sid in list(seen.keys()):
            try:
                from qdrant_client.models import FieldCondition, MatchValue
                cnt = client.count(
                    collection_name=collection,
                    count_filter=Filter(
                        must=[FieldCondition(key="source_id", match=MatchValue(value=sid))]
                    ),
                    exact=False,
                )
                chunk_counts[sid] = cnt.count
            except Exception:
                chunk_counts[sid] = 0

        sources = sorted(
            [{"chunk_count": chunk_counts.get(s["source_id"], 0), **s} for s in seen.values()],
            key=lambda x: x["chunk_count"],
            reverse=True,
        )
        return {"sources": sources, "total": len(sources)}

    except Exception as exc:
        return {"sources": [], "total": 0, "error": str(exc)}


class SQLiteIngestRequest(BaseModel):
    db_path: str
    tables: list[str] | None = None
    title: str | None = None
    language: str = "fa"
    collection: str = "owner_references"


@router.post("/ingest/sqlite", status_code=202)
async def ingest_sqlite_db(
    body: SQLiteIngestRequest,
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Ingest a SQLite / .db database from a local path or mounted network share.
    Auto-discovers all tables; pass `tables` to restrict to specific ones.
    Supports Persian (RTL) column names and values natively.
    No file upload needed — the file must be accessible on the server filesystem
    (local path or mounted NFS/SMB share).
    """
    from pathlib import Path
    if not Path(body.db_path).exists():
        raise HTTPException(404, f"Database file not found: {body.db_path}")
    if not Path(body.db_path).suffix.lower() in (".sqlite", ".sqlite3", ".db", ".db3"):
        raise HTTPException(422, "File must be a SQLite database (.sqlite/.db/.db3)")

    pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
    result = await pipeline.ingest_sqlite(
        db_path=body.db_path,
        tables=body.tables,
        title=body.title,
        language=body.language,
        collection=body.collection,
    )
    return {
        "status": result.get("status"),
        "source_id": result.get("source_id"),
        "chunks_stored": result.get("chunks_stored", 0),
        "message": f"Ingested {result.get('chunks_stored', 0)} knowledge chunks from {body.db_path}.",
    }


@router.post("/ingest/sqlite/upload", status_code=202)
async def upload_sqlite_db(
    file: UploadFile = File(...),
    title: Optional[str] = Form(None),
    language: str = Form("fa"),
    collection: str = Form("owner_references"),
    tables_filter: Optional[str] = Form(None),  # comma-separated table names
    staff: Staff = Depends(require_permission("clinical:write")),
    db: AsyncSession = Depends(get_db),
):
    """
    Upload a SQLite .db file directly from the browser.
    Use this when the DB is on the user's machine, not the server.
    """
    import tempfile, os
    content = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        tables = [t.strip() for t in tables_filter.split(",")] if tables_filter else None
        pipeline = KnowledgeIngestionPipeline(vector_store=_get_vector_store(), db=db)
        result = await pipeline.ingest_sqlite(
            db_path=tmp_path,
            tables=tables,
            title=title or file.filename,
            language=language,
            collection=collection,
        )
        return {
            "status": result.get("status"),
            "source_id": result.get("source_id"),
            "chunks_stored": result.get("chunks_stored", 0),
            "message": f"Ingested {result.get('chunks_stored', 0)} chunks from {file.filename}.",
        }
    finally:
        os.unlink(tmp_path)


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: str,
    staff: Staff = Depends(require_permission("clinical:write")),
):
    """Remove a knowledge source (e.g., outdated guideline version)."""
    vs = _get_vector_store()
    vs.delete_source(source_id)
    return {"status": "deleted", "source_id": source_id}
