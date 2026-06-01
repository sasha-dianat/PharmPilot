"""
Document parsers for every ingestion source.
Converts raw content → clean text chunks with metadata.
"""
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Target chunk size and overlap for clinical text
CHUNK_SIZE_CHARS = 1200    # ~300 tokens — good balance for PubMedBERT context
CHUNK_OVERLAP_CHARS = 200  # Overlap to preserve context across chunk boundaries


@dataclass
class ParsedChunk:
    content: str
    section_title: Optional[str] = None
    page_number: Optional[int] = None
    chunk_index: int = 0
    drugs_mentioned: list[str] = field(default_factory=list)
    conditions_mentioned: list[str] = field(default_factory=list)
    specialty_tags: list[str] = field(default_factory=list)
    evidence_grade: Optional[str] = None


class TextChunker:
    """
    Splits text into overlapping chunks that respect sentence boundaries.
    Preserves section headings as metadata.
    """

    def chunk(
        self,
        text: str,
        section_title: Optional[str] = None,
        page_number: Optional[int] = None,
    ) -> list[ParsedChunk]:
        # Split into sentences
        sentences = self._split_sentences(text)
        chunks = []
        current_chunk = []
        current_len = 0
        chunk_idx = 0

        for sentence in sentences:
            sent_len = len(sentence)
            if current_len + sent_len > CHUNK_SIZE_CHARS and current_chunk:
                chunk_text = " ".join(current_chunk).strip()
                if len(chunk_text) > 100:  # Discard tiny chunks
                    chunks.append(ParsedChunk(
                        content=chunk_text,
                        section_title=section_title,
                        page_number=page_number,
                        chunk_index=chunk_idx,
                    ))
                    chunk_idx += 1
                # Overlap: keep last N characters
                overlap_text = chunk_text[-CHUNK_OVERLAP_CHARS:]
                current_chunk = [overlap_text]
                current_len = len(overlap_text)
            current_chunk.append(sentence)
            current_len += sent_len

        if current_chunk:
            chunk_text = " ".join(current_chunk).strip()
            if len(chunk_text) > 100:
                chunks.append(ParsedChunk(
                    content=chunk_text,
                    section_title=section_title,
                    page_number=page_number,
                    chunk_index=chunk_idx,
                ))

        return chunks

    def _split_sentences(self, text: str) -> list[str]:
        """Split on sentence boundaries, preserving clinical abbreviations."""
        text = re.sub(r"\s+", " ", text).strip()
        # Don't split on common medical abbreviations
        text = re.sub(r"(?<=[A-Z])\.", r"<ABBR>", text)
        sentences = re.split(r"(?<=[.!?])\s+(?=[A-Z])", text)
        return [s.replace("<ABBR>", ".").strip() for s in sentences if s.strip()]


class PDFParser:
    """Parse PDF files — drug package inserts, guidelines, USP chapters."""

    def parse(self, pdf_path: str) -> list[ParsedChunk]:
        try:
            import PyPDF2
        except ImportError:
            logger.error("PyPDF2 not installed")
            return []

        chunks = []
        chunker = TextChunker()

        with open(pdf_path, "rb") as f:
            reader = PyPDF2.PdfReader(f)
            current_section = None

            for page_num, page in enumerate(reader.pages, start=1):
                text = page.extract_text() or ""
                if not text.strip():
                    continue

                # Detect section headings (ALL CAPS lines or lines ending with colon)
                lines = text.split("\n")
                page_text = ""
                for line in lines:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    if self._is_heading(stripped):
                        if page_text.strip():
                            chunks.extend(chunker.chunk(page_text, current_section, page_num - 1))
                            page_text = ""
                        current_section = stripped
                    else:
                        page_text += " " + stripped

                if page_text.strip():
                    chunks.extend(chunker.chunk(page_text, current_section, page_num))

        logger.info("PDF parsed: %d chunks from %s", len(chunks), pdf_path)
        return chunks

    def _is_heading(self, line: str) -> bool:
        return (
            len(line) < 100 and
            (line.isupper() or
             line.endswith(":") or
             re.match(r"^\d+\.\s+[A-Z]", line) or
             re.match(r"^[A-Z][A-Z\s]+$", line))
        )


class HTMLParser:
    """
    Parse HTML pages — UpToDate, FDA drug labels, PubMed abstracts.
    Strips navigation, ads, headers; extracts article content.
    """

    def parse(self, html: str, url: str = "") -> list[ParsedChunk]:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("BeautifulSoup not installed")
            return []

        soup = BeautifulSoup(html, "html.parser")
        chunker = TextChunker()

        # Remove noise elements
        for tag in soup(["nav", "header", "footer", "script", "style",
                         "advertisement", "aside", ".sidebar", ".related-articles"]):
            tag.decompose()

        chunks = []
        current_section = None

        # Extract structured content by heading hierarchy
        for element in soup.find_all(["h1", "h2", "h3", "h4", "p", "li", "td"]):
            tag = element.name
            text = element.get_text(separator=" ", strip=True)

            if not text or len(text) < 20:
                continue

            if tag in ("h1", "h2", "h3", "h4"):
                current_section = text[:200]
            else:
                new_chunks = chunker.chunk(text, section_title=current_section)
                chunks.extend(new_chunks)

        logger.info("HTML parsed: %d chunks from %s", len(chunks), url[:80])
        return chunks


class PubMedParser:
    """
    Fetch and parse PubMed abstracts via the NCBI E-utilities API.
    Free, no authentication required for basic access.
    """

    PUBMED_FETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    PUBMED_SEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"

    async def fetch_abstract(self, pmid: str) -> Optional[dict]:
        """Fetch a single PubMed article by PMID."""
        import httpx
        params = {
            "db": "pubmed", "id": pmid, "retmode": "xml",
            "rettype": "abstract", "tool": "pharmpilot", "email": "admin@pharmpilot.ai",
        }
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(self.PUBMED_FETCH_URL, params=params)
            if response.status_code == 200:
                return self._parse_pubmed_xml(response.text, pmid)
        return None

    async def search_and_fetch(
        self,
        query: str,
        max_results: int = 50,
    ) -> list[dict]:
        """Search PubMed and fetch abstracts for all matching articles."""
        import httpx

        search_params = {
            "db": "pubmed", "term": query, "retmax": max_results,
            "retmode": "json", "tool": "pharmpilot", "email": "admin@pharmpilot.ai",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            search_resp = await client.get(self.PUBMED_SEARCH_URL, params=search_params)
            if search_resp.status_code != 200:
                return []

            pmids = search_resp.json().get("esearchresult", {}).get("idlist", [])
            logger.info("PubMed search '%s': %d results", query, len(pmids))

            articles = []
            for pmid in pmids:
                article = await self.fetch_abstract(pmid)
                if article:
                    articles.append(article)
            return articles

    def _parse_pubmed_xml(self, xml_text: str, pmid: str) -> Optional[dict]:
        try:
            import xml.etree.ElementTree as ET
            root = ET.fromstring(xml_text)
            article = root.find(".//PubmedArticle")
            if not article:
                return None

            title = "".join(t.itertext() for t in article.findall(".//ArticleTitle"))
            abstract_parts = article.findall(".//AbstractText")
            abstract = " ".join("".join(p.itertext()) for p in abstract_parts)

            authors = []
            for author in article.findall(".//Author")[:5]:
                last = author.findtext("LastName", "")
                first = author.findtext("ForeName", "")
                if last:
                    authors.append(f"{last} {first}".strip())

            journal = article.findtext(".//Journal/Title", "")
            year = article.findtext(".//PubDate/Year", "")

            return {
                "pmid": pmid,
                "title": title,
                "abstract": abstract,
                "authors": ", ".join(authors),
                "journal": journal,
                "year": year,
                "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            }
        except Exception as exc:
            logger.error("PubMed XML parse failed: %s", exc)
            return None


class PlainTextParser:
    """Parse plain text or markdown files."""

    def parse(self, text: str, filename: str = "") -> list[ParsedChunk]:
        chunker = TextChunker()
        chunks = []
        current_section = None

        # Detect markdown-style headings
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                current_section = stripped.lstrip("#").strip()
            elif stripped:
                new_chunks = chunker.chunk(stripped, current_section)
                chunks.extend(new_chunks)

        # Re-index
        for i, chunk in enumerate(chunks):
            chunk.chunk_index = i

        logger.info("Text parsed: %d chunks from %s", len(chunks), filename)
        return chunks
