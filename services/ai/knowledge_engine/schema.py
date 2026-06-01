"""
Knowledge Engine data models.
Every ingested document chunk is stored with full provenance for citation.
"""
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from shared.models.base import AuditedBase, TimestampedBase


class SourceType(str, Enum):
    UPTODATE = "uptodate"
    PUBMED_ARTICLE = "pubmed_article"
    FDA_LABEL = "fda_label"
    CLINICAL_GUIDELINE = "clinical_guideline"     # ACC/AHA, ADA, IDSA, etc.
    PHARMACOLOGY_TEXTBOOK = "pharmacology_textbook"
    PACKAGE_INSERT = "package_insert"
    NCPDP_STANDARD = "ncpdp_standard"
    DEA_REGULATION = "dea_regulation"
    USP_CHAPTER = "usp_chapter"
    CUSTOM_DOCUMENT = "custom_document"           # Uploaded by pharmacy staff
    WEB_CRAWL = "web_crawl"


class KnowledgeSource(AuditedBase):
    """A document or data source ingested into the knowledge base."""
    __tablename__ = "knowledge_sources"

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(String(50), nullable=False, index=True)
    url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(100), nullable=True)
    pmid: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PubMed ID
    isbn: Mapped[str | None] = mapped_column(String(20), nullable=True)

    authors: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Ingestion tracking
    ingestion_status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending, crawling, chunking, embedding, complete, failed
    total_chunks: Mapped[int] = mapped_column(Integer, default=0)
    ingested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Evidence quality
    evidence_level: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # I-A (highest) → V (expert opinion)
    peer_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)

    specialty_tags: Mapped[list] = mapped_column(JSONB, default=list)
    # ["cardiology", "nephrology", "pharmacogenomics", ...]

    raw_content_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # S3/blob key to raw content backup

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(back_populates="source")


class KnowledgeChunk(TimestampedBase):
    """
    A paragraph-level chunk from a knowledge source, stored as:
    - raw text (for citation display)
    - embedding vector (in Qdrant vector DB — not here, referenced by chunk_id)
    - metadata for filtering (specialty, drug names mentioned, etc.)
    """
    __tablename__ = "knowledge_chunks"

    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("knowledge_sources.id"), nullable=False, index=True
    )

    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_length: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Qdrant vector ID (UUID matching the point ID in the vector store)
    qdrant_point_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    # Extracted metadata for filtered retrieval
    drugs_mentioned: Mapped[list] = mapped_column(JSONB, default=list)
    # ["warfarin", "aspirin"] — from NER
    conditions_mentioned: Mapped[list] = mapped_column(JSONB, default=list)
    specialty_tags: Mapped[list] = mapped_column(JSONB, default=list)
    evidence_grade: Mapped[str | None] = mapped_column(String(5), nullable=True)

    source: Mapped["KnowledgeSource"] = relationship(back_populates="chunks")


class KnowledgeQuery(TimestampedBase):
    """Audit log of every RAG query — what was asked, what was retrieved, what was cited."""
    __tablename__ = "knowledge_queries"

    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_embedding_model: Mapped[str] = mapped_column(String(100), nullable=False)
    retrieved_chunk_ids: Mapped[list] = mapped_column(JSONB, default=list)
    retrieval_scores: Mapped[list] = mapped_column(JSONB, default=list)
    context_used: Mapped[str | None] = mapped_column(Text, nullable=True)
    llm_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    prescription_id: Mapped[UUID | None] = mapped_column(nullable=True)
    staff_id: Mapped[UUID | None] = mapped_column(nullable=True)
    response_time_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
