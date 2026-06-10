"""
Phase 32 — Rx Scanning & Document Imaging
==========================================
Stores scanned images of physical prescriptions, prior-authorisation letters,
prescriber fax confirmations, and any other documents attached to an Rx.

Storage backend: local filesystem (configurable via RX_DOCS_DIR env var).
In production swap for an S3-compatible bucket — only the `_save_file` and
`_read_file` helpers need changing.

Documents are stored under:
  {RX_DOCS_DIR}/{rx_id}/{doc_id}.{ext}

Metadata is kept in `rx_documents` table.

Endpoints:
  POST   /rx-documents/upload          — upload one or more images/PDFs
  GET    /rx-documents?rx_id=…         — list documents for an Rx
  GET    /rx-documents/{doc_id}        — metadata
  GET    /rx-documents/{doc_id}/file   — binary download
  DELETE /rx-documents/{doc_id}        — soft-delete (marked deleted, file kept)
"""
from __future__ import annotations

import os
import uuid
import base64
import mimetypes
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from services.platform.database import get_db
from services.platform.auth import get_current_user

router = APIRouter(tags=["rx documents"])

# ─── Config ───────────────────────────────────────────────────────────────────

RX_DOCS_DIR = Path(os.getenv("RX_DOCS_DIR", Path.home() / ".pharmpilot" / "rx_documents"))

ALLOWED_MIME = {
    "image/jpeg", "image/png", "image/webp",
    "image/tiff", "image/bmp",
    "application/pdf",
}
MAX_FILE_SIZE = 20 * 1024 * 1024   # 20 MB

DOCUMENT_TYPES = [
    "original_rx",          # Physical written / printed prescription
    "fax_confirmation",     # Fax from prescriber
    "prior_auth",           # Prior-authorisation letter
    "prescriber_note",      # Clinical note attached by prescriber
    "insurance_letter",     # Insurance correspondence
    "compound_formula",     # Compounding formula sheet
    "other",
]

# ─── Pydantic schemas ──────────────────────────────────────────────────────────

class DocumentMeta(BaseModel):
    id:           str
    rx_id:        str
    doc_type:     str
    filename:     str
    mime_type:    str
    file_size:    int
    uploaded_by:  str
    notes:        Optional[str]
    created_at:   str
    deleted:      bool

class DocumentListResponse(BaseModel):
    total:     int
    documents: List[DocumentMeta]

class Base64UploadRequest(BaseModel):
    """Alternative upload via JSON body (for browser webcam captures)."""
    rx_id:       str
    doc_type:    str = "original_rx"
    filename:    str = "scan.jpg"
    base64_data: str
    mime_type:   str = "image/jpeg"
    notes:       Optional[str] = None
    uploaded_by: str

# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _ensure_table(db: AsyncSession) -> None:
    await db.execute(text("""
        CREATE TABLE IF NOT EXISTS rx_documents (
            id           TEXT PRIMARY KEY,
            rx_id        TEXT NOT NULL,
            doc_type     TEXT NOT NULL DEFAULT 'original_rx',
            filename     TEXT NOT NULL,
            mime_type    TEXT NOT NULL,
            file_size    INTEGER DEFAULT 0,
            file_path    TEXT NOT NULL,
            uploaded_by  TEXT,
            notes        TEXT,
            deleted      BOOLEAN DEFAULT FALSE,
            created_at   TIMESTAMPTZ DEFAULT now()
        )
    """))
    await db.commit()

def _doc_dir(rx_id: str) -> Path:
    d = RX_DOCS_DIR / rx_id
    d.mkdir(parents=True, exist_ok=True)
    return d

def _save_file(rx_id: str, doc_id: str, ext: str, data: bytes) -> Path:
    path = _doc_dir(rx_id) / f"{doc_id}{ext}"
    path.write_bytes(data)
    return path

def _ext(mime: str, filename: str) -> str:
    ext = Path(filename).suffix
    if ext:
        return ext.lower()
    return mimetypes.guess_extension(mime, strict=False) or ".bin"

async def _insert_meta(db: AsyncSession, **kwargs) -> None:
    await db.execute(text("""
        INSERT INTO rx_documents
            (id, rx_id, doc_type, filename, mime_type, file_size,
             file_path, uploaded_by, notes, created_at)
        VALUES
            (:id, :rx_id, :doc_type, :filename, :mime_type, :file_size,
             :file_path, :uploaded_by, :notes, :now)
    """), kwargs)
    await db.commit()

async def _fetch_meta(db: AsyncSession, doc_id: str) -> dict:
    result = await db.execute(
        text("SELECT * FROM rx_documents WHERE id = :id AND deleted = FALSE"),
        {"id": doc_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(404, "Document not found")
    return dict(row)

def _to_meta(r: dict) -> DocumentMeta:
    return DocumentMeta(
        id=r["id"], rx_id=r["rx_id"], doc_type=r["doc_type"],
        filename=r["filename"], mime_type=r["mime_type"],
        file_size=r.get("file_size", 0),
        uploaded_by=r.get("uploaded_by", ""),
        notes=r.get("notes"),
        created_at=str(r["created_at"]),
        deleted=bool(r.get("deleted", False)),
    )

# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("/document-types")
async def document_types():
    return {"types": DOCUMENT_TYPES}


@router.post("/upload", response_model=DocumentMeta, status_code=201)
async def upload_document(
    rx_id:       str        = Form(...),
    doc_type:    str        = Form("original_rx"),
    notes:       str        = Form(""),
    uploaded_by: str        = Form(""),
    file:        UploadFile = File(...),
    db:          AsyncSession = Depends(get_db),
    current:     dict         = Depends(get_current_user),
):
    await _ensure_table(db)

    if file.content_type not in ALLOWED_MIME:
        raise HTTPException(415, f"Unsupported file type: {file.content_type}")

    data = await file.read()
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 20 MB limit")

    doc_id = str(uuid.uuid4())
    ext    = _ext(file.content_type, file.filename or "doc")
    path   = _save_file(rx_id, doc_id, ext, data)
    now    = datetime.now(timezone.utc)

    await _insert_meta(
        db, id=doc_id, rx_id=rx_id, doc_type=doc_type,
        filename=file.filename or f"scan{ext}",
        mime_type=file.content_type, file_size=len(data),
        file_path=str(path),
        uploaded_by=uploaded_by or current.get("sub", ""),
        notes=notes or None, now=now,
    )

    return DocumentMeta(
        id=doc_id, rx_id=rx_id, doc_type=doc_type,
        filename=file.filename or f"scan{ext}",
        mime_type=file.content_type, file_size=len(data),
        uploaded_by=uploaded_by or current.get("sub", ""),
        notes=notes or None, created_at=now.isoformat(), deleted=False,
    )


@router.post("/upload-base64", response_model=DocumentMeta, status_code=201)
async def upload_base64(
    body:    Base64UploadRequest,
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    """Used by webcam captures in the browser (no multipart form)."""
    await _ensure_table(db)

    if body.mime_type not in ALLOWED_MIME:
        raise HTTPException(415, f"Unsupported file type: {body.mime_type}")

    # Strip data-URL prefix if present
    raw_b64 = body.base64_data
    if "," in raw_b64:
        raw_b64 = raw_b64.split(",", 1)[1]

    try:
        data = base64.b64decode(raw_b64)
    except Exception:
        raise HTTPException(400, "Invalid base64 data")

    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(413, "File exceeds 20 MB limit")

    doc_id = str(uuid.uuid4())
    ext    = _ext(body.mime_type, body.filename)
    path   = _save_file(body.rx_id, doc_id, ext, data)
    now    = datetime.now(timezone.utc)

    await _insert_meta(
        db, id=doc_id, rx_id=body.rx_id, doc_type=body.doc_type,
        filename=body.filename, mime_type=body.mime_type,
        file_size=len(data), file_path=str(path),
        uploaded_by=body.uploaded_by or current.get("sub", ""),
        notes=body.notes, now=now,
    )

    return DocumentMeta(
        id=doc_id, rx_id=body.rx_id, doc_type=body.doc_type,
        filename=body.filename, mime_type=body.mime_type,
        file_size=len(data),
        uploaded_by=body.uploaded_by or current.get("sub", ""),
        notes=body.notes, created_at=now.isoformat(), deleted=False,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    rx_id:    str           = Query(...),
    doc_type: Optional[str] = Query(None),
    db:       AsyncSession  = Depends(get_db),
    _current: dict          = Depends(get_current_user),
):
    await _ensure_table(db)

    filters = ["rx_id = :rx_id", "deleted = FALSE"]
    params: dict = {"rx_id": rx_id}
    if doc_type:
        filters.append("doc_type = :doc_type"); params["doc_type"] = doc_type

    where = " AND ".join(filters)
    count_result = await db.execute(
        text(f"SELECT COUNT(*) FROM rx_documents WHERE {where}"), params
    )
    total = count_result.scalar() or 0

    rows = await db.execute(text(f"""
        SELECT * FROM rx_documents
        WHERE {where}
        ORDER BY created_at DESC
    """), params)

    docs = [_to_meta(dict(r)) for r in rows.mappings()]
    return DocumentListResponse(total=total, documents=docs)


@router.get("/{doc_id}", response_model=DocumentMeta)
async def get_document_meta(
    doc_id:   str,
    db:       AsyncSession = Depends(get_db),
    _current: dict         = Depends(get_current_user),
):
    await _ensure_table(db)
    r = await _fetch_meta(db, doc_id)
    return _to_meta(r)


@router.get("/{doc_id}/file")
async def download_document(
    doc_id:   str,
    db:       AsyncSession = Depends(get_db),
    _current: dict         = Depends(get_current_user),
):
    await _ensure_table(db)
    r     = await _fetch_meta(db, doc_id)
    fpath = Path(r["file_path"])
    if not fpath.exists():
        raise HTTPException(404, "File not found on disk")
    return FileResponse(
        str(fpath),
        media_type=r["mime_type"],
        filename=r["filename"],
    )


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    doc_id:  str,
    db:      AsyncSession = Depends(get_db),
    current: dict         = Depends(get_current_user),
):
    await _ensure_table(db)
    await _fetch_meta(db, doc_id)     # raises 404 if not found

    await db.execute(
        text("UPDATE rx_documents SET deleted = TRUE WHERE id = :id"),
        {"id": doc_id},
    )
    await db.commit()
