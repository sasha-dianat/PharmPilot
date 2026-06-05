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


class DOCXParser:
    """
    Parse Word .docx files — Iranian pharmacopeia references, formulary monographs,
    internal SOPs the owner uploads. Uses python-docx when available, with a
    zero-dependency fallback that reads word/document.xml directly from the zip.
    Handles Persian (RTL) text natively (Unicode throughout).
    """

    def parse(self, docx_path: str) -> list[ParsedChunk]:
        paragraphs = self._extract_paragraphs(docx_path)
        if not paragraphs:
            logger.warning("DOCX produced no text: %s", docx_path)
            return []

        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        current_section = None
        buffer = ""

        def flush(section):
            nonlocal buffer
            text = buffer.strip()
            if not text:
                return
            produced = chunker.chunk(text, section)
            if produced:
                chunks.extend(produced)
            else:
                # Short reference monograph: emit directly so it is NOT dropped
                # by the chunker's minimum-length floor (common for Persian refs).
                chunks.append(ParsedChunk(content=text, section_title=section))
            buffer = ""

        for para in paragraphs:
            text = para.strip()
            if not text:
                continue
            if self._is_heading(text):
                flush(current_section)
                current_section = text
            else:
                buffer += " " + text
        flush(current_section)

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("DOCX parsed: %d chunks from %s", len(chunks), docx_path)
        return chunks

    def _extract_paragraphs(self, docx_path: str) -> list[str]:
        # Preferred: python-docx (also pulls table cell text).
        try:
            import docx  # python-docx
            doc = docx.Document(docx_path)
            paras = [p.text for p in doc.paragraphs]
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        paras.append(" | ".join(cells))
            return paras
        except ImportError:
            pass
        except Exception as e:
            logger.warning("python-docx failed (%s); trying raw XML fallback", e)

        # Fallback: read word/document.xml from the .docx zip and strip tags.
        try:
            import zipfile
            with zipfile.ZipFile(docx_path) as z:
                xml = z.read("word/document.xml").decode("utf-8", errors="ignore")
            # paragraphs are <w:p>…</w:p>; text runs are <w:t>…</w:t>
            paras = []
            for p_match in re.findall(r"<w:p[ >].*?</w:p>", xml, re.DOTALL):
                texts = re.findall(r"<w:t[^>]*>(.*?)</w:t>", p_match, re.DOTALL)
                joined = "".join(texts)
                joined = re.sub(r"<[^>]+>", "", joined)
                if joined.strip():
                    paras.append(joined)
            return paras
        except Exception as e:
            logger.error("DOCX XML fallback failed for %s: %s", docx_path, e)
            return []

    def _is_heading(self, line: str) -> bool:
        # Heading heuristics that also work for Persian (short line, ends with ':')
        if len(line) > 120:
            return False
        return (line.endswith(":") or line.endswith("：") or
                bool(re.match(r"^\d+[\.\-]\s*\S", line)) or
                (line.isupper() and len(line.split()) <= 10))


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
    """Parse plain text files (UTF-8 / ASCII). Works for .txt and raw transcripts."""

    def parse(self, text: str, filename: str = "") -> list[ParsedChunk]:
        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        current_section = None

        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped:
                continue
            # Section heading heuristic — keep it tight to avoid false positives
            # on Persian text containing uppercase English abbreviations (G6PD etc.)
            words = stripped.split()
            is_heading = (
                len(stripped) <= 60 and len(words) <= 8 and (
                    # All-ASCII-uppercase short line (like "WARNINGS" or "DOSE:")
                    (stripped.replace(" ", "").replace(":", "").isascii() and
                     stripped.replace(" ", "").replace(":", "").isupper()) or
                    stripped.endswith(":") or
                    bool(re.match(r"^\d+[\.\)]\s+[A-Z؀-ۿ]", stripped))
                )
            )
            if is_heading:
                current_section = stripped
            else:
                new_chunks = chunker.chunk(stripped, current_section)
                chunks.extend(new_chunks)
                if not new_chunks and len(stripped) > 30:
                    chunks.append(ParsedChunk(content=stripped, section_title=current_section))

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("Text parsed: %d chunks from %s", len(chunks), filename)
        return chunks


class MarkdownParser:
    """Parse Markdown (.md) files — preserves heading hierarchy as section titles."""

    def parse(self, text: str, filename: str = "") -> list[ParsedChunk]:
        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        current_section: str | None = None
        buffer = ""

        def flush(section: str | None) -> None:
            nonlocal buffer
            t = buffer.strip()
            if not t:
                buffer = ""
                return
            produced = chunker.chunk(t, section)
            if produced:
                chunks.extend(produced)
            elif len(t) > 20:
                chunks.append(ParsedChunk(content=t, section_title=section))
            buffer = ""

        for line in text.split("\n"):
            m = re.match(r"^(#{1,6})\s+(.*)", line.rstrip())
            if m:
                flush(current_section)
                current_section = m.group(2).strip()
            else:
                # strip inline Markdown markers
                clean = re.sub(r"\*{1,2}|_{1,2}|`{1,3}|~~|\[([^\]]+)\]\([^\)]+\)", r"\1", line)
                buffer += " " + clean.strip()

        flush(current_section)
        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("Markdown parsed: %d chunks from %s", len(chunks), filename)
        return chunks


class CSVParser:
    """
    Parse CSV/TSV files into knowledge chunks.
    Each row becomes a sentence: 'Header1: value. Header2: value.'
    Useful for drug formulary tables, interaction matrices, dosing tables.
    """

    def parse(self, text: str, filename: str = "", delimiter: str | None = None) -> list[ParsedChunk]:
        import csv, io
        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        dialect = "excel-tab" if filename.endswith(".tsv") else "excel"
        sep = delimiter or ("\t" if filename.endswith(".tsv") else ",")
        reader = csv.DictReader(io.StringIO(text), dialect=dialect, delimiter=sep)
        buffer = ""
        section = filename or "Table"

        for row_num, row in enumerate(reader, 1):
            # Represent each row as "Key: value; Key: value" prose
            parts = [f"{k.strip()}: {v.strip()}" for k, v in row.items()
                     if v and v.strip() and k]
            if parts:
                sentence = ". ".join(parts) + "."
                buffer += " " + sentence
                if len(buffer) > 900:
                    produced = chunker.chunk(buffer.strip(), section)
                    if produced:
                        chunks.extend(produced)
                    elif buffer.strip():
                        chunks.append(ParsedChunk(content=buffer.strip(), section_title=section))
                    buffer = ""

        if buffer.strip():
            chunks.append(ParsedChunk(content=buffer.strip(), section_title=section))

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("CSV parsed: %d chunks from %s (%d rows)", len(chunks), filename, row_num if chunks else 0)
        return chunks


class RTFParser:
    """
    Parse RTF (.rtf) files — strips RTF control words, keeps Unicode/Persian text.
    Uses the stdlib `codecs` approach with zero extra deps for basic files;
    falls back to python-striprtf if available.
    """

    def parse(self, content: bytes | str, filename: str = "") -> list[ParsedChunk]:
        text = self._strip_rtf(content if isinstance(content, str) else content.decode("latin-1", errors="ignore"))
        if not text.strip():
            return []
        return PlainTextParser().parse(text, filename)

    def _strip_rtf(self, rtf: str) -> str:
        try:
            from striprtf.striprtf import rtf_to_text
            return rtf_to_text(rtf)
        except ImportError:
            pass
        # Minimal built-in strip: remove control words {\word}, \word, \word123
        # and keep printable Unicode (incl. Persian \uN? sequences)
        def decode_unicode(m: re.Match) -> str:
            try:
                return chr(int(m.group(1)))
            except Exception:
                return ""
        text = re.sub(r"\\u(-?\d+)\?", decode_unicode, rtf)
        text = re.sub(r"\{[^{}]*\}", " ", text)       # remove groups
        text = re.sub(r"\\[a-zA-Z]+\d*\s?", " ", text)  # remove control words
        text = re.sub(r"\\[^a-zA-Z]", " ", text)     # remove control symbols
        text = re.sub(r"[{}]", "", text)
        return re.sub(r"  +", " ", text).strip()


class EPUBParser:
    """
    Parse EPUB e-books — extracts all chapter HTML files, concatenates text.
    Zero-dep: reads the EPUB ZIP directly; uses HTMLParser for each chapter.
    """

    def parse(self, epub_path: str) -> list[ParsedChunk]:
        import zipfile
        from bs4 import BeautifulSoup
        chunks: list[ParsedChunk] = []
        html_p = HTMLParser()

        try:
            with zipfile.ZipFile(epub_path) as z:
                names = sorted(n for n in z.namelist()
                               if n.endswith((".html", ".xhtml", ".htm")))
                for name in names:
                    html = z.read(name).decode("utf-8", errors="ignore")
                    chapter_chunks = html_p.parse(html, url=name)
                    chunks.extend(chapter_chunks)
        except Exception as e:
            logger.error("EPUB parse failed for %s: %s", epub_path, e)

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("EPUB parsed: %d chunks from %s", len(chunks), epub_path)
        return chunks


class JSONParser:
    """
    Parse JSON files containing clinical data (drug lists, interaction tables, etc.).
    Handles: list-of-dicts (each dict → sentence), or nested dicts (walk leaves).
    """

    def parse(self, text: str, filename: str = "") -> list[ParsedChunk]:
        import json
        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON parse failed for %s: %s", filename, e)
            return []

        sentences: list[str] = []
        self._walk(data, sentences)

        buffer = ""
        for s in sentences:
            buffer += " " + s
            if len(buffer) > 900:
                produced = chunker.chunk(buffer.strip(), filename)
                chunks.extend(produced or [ParsedChunk(content=buffer.strip())])
                buffer = ""
        if buffer.strip():
            chunks.append(ParsedChunk(content=buffer.strip()))

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("JSON parsed: %d chunks from %s", len(chunks), filename)
        return chunks

    def _walk(self, node, acc: list[str], path: str = "") -> None:
        if isinstance(node, dict):
            parts = []
            for k, v in node.items():
                if isinstance(v, (str, int, float)) and str(v).strip():
                    parts.append(f"{k}: {v}")
                else:
                    self._walk(v, acc, path=k)
            if parts:
                acc.append(". ".join(parts) + ".")
        elif isinstance(node, list):
            for item in node:
                self._walk(item, acc, path)
        elif isinstance(node, (str, int, float)):
            s = str(node).strip()
            if len(s) > 20:
                acc.append(s)


class SQLiteParser:
    """
    Extract clinical knowledge from a SQLite / .db database file.

    Works for any SQLite file on a local path or a mounted network share.
    Auto-discovers tables and maps columns to clinical semantics — drug names,
    indications, contraindications, dosing, interactions — without requiring
    the owner to configure anything.

    Each table row becomes one or more prose sentences:
        "دارو: متفورمین. نشانه: دیابت نوع ۲. منع مصرف: نارسایی کلیه (eGFR<30)."

    Supports:
      - Persian column names and values (Unicode throughout)
      - Mixed English/Persian schemas
      - Binary blobs are skipped automatically
      - Tables with ≥2 text columns are included; pure-numeric tables skipped
    """

    # Column name keywords → semantic role
    _COL_ROLES: dict[str, str] = {
        # English
        "drug": "Drug", "medication": "Drug", "medicine": "Drug", "compound": "Drug",
        "brand": "Brand", "generic": "Generic", "trade": "Brand",
        "indication": "Indication", "use": "Use", "disease": "Disease",
        "contraindication": "Contraindication", "avoid": "Avoid",
        "dose": "Dose", "dosage": "Dose", "strength": "Strength", "concentration": "Strength",
        "route": "Route", "administration": "Route",
        "interaction": "Interaction", "ddi": "Drug Interaction",
        "side_effect": "Side Effect", "adverse": "Adverse Effect",
        "warning": "Warning", "caution": "Caution", "precaution": "Caution",
        "mechanism": "Mechanism", "action": "Mechanism",
        "category": "Category", "class": "Class", "group": "Class",
        "note": "Note", "comment": "Note", "description": "Description",
        "pregnancy": "Pregnancy", "renal": "Renal", "hepatic": "Hepatic",
        "pediatric": "Pediatric", "geriatric": "Geriatric",
        # Persian / Arabic
        "دارو": "دارو", "دوا": "دارو", "قرص": "دارو",
        "نشانه": "نشانه", "اندیکاسیون": "نشانه",
        "منع": "منع مصرف", "کنتراندیکاسیون": "منع مصرف",
        "دوز": "دوز", "مقدار": "دوز",
        "تداخل": "تداخل دارویی", "واکنش": "عوارض",
        "عوارض": "عوارض جانبی", "هشدار": "هشدار",
        "توضیح": "توضیحات", "یادداشت": "یادداشت",
    }

    def parse(self, db_path: str, tables: list[str] | None = None) -> list[ParsedChunk]:
        """
        Parse a SQLite database. If `tables` is None, auto-discovers all
        tables with sufficient text content.
        """
        import sqlite3
        from services.core.localization.digits import normalize_digits

        chunks: list[ParsedChunk] = []
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
        except Exception as e:
            logger.error("SQLite open failed for %s: %s", db_path, e)
            return []

        try:
            cursor = conn.cursor()
            # Discover tables
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            all_tables = [r[0] for r in cursor.fetchall()]
            target = [t for t in all_tables if t in tables] if tables else all_tables

            for table in target:
                table_chunks = self._process_table(cursor, table, normalize_digits)
                chunks.extend(table_chunks)
                logger.info("SQLite table '%s': %d chunks", table, len(table_chunks))
        finally:
            conn.close()

        for i, c in enumerate(chunks):
            c.chunk_index = i
        logger.info("SQLite parsed: %d total chunks from %s", len(chunks), db_path)
        return chunks

    def _process_table(self, cursor, table: str, normalize_digits) -> list[ParsedChunk]:
        """Convert one table to ParsedChunks."""
        try:
            cursor.execute(f'PRAGMA table_info("{table}")')   # nosec — table names from sqlite_master
            col_info = cursor.fetchall()
        except Exception as e:
            logger.warning("PRAGMA failed for table %s: %s", table, e)
            return []

        # Filter to text/numeric columns (skip blobs)
        cols = [
            {"name": row[1], "type": (row[2] or "").upper()}
            for row in col_info
            if (row[2] or "").upper() not in ("BLOB",)
        ]
        text_cols = [c for c in cols if not c["type"].startswith("INT")
                     and c["type"] not in ("REAL", "NUMERIC", "FLOAT")]

        if len(text_cols) < 2:
            return []  # Not enough text columns to form meaningful prose

        chunker = TextChunker()
        chunks: list[ParsedChunk] = []
        buffer = ""
        section = table

        try:
            col_names_safe = ", ".join(f'"{c["name"]}"' for c in text_cols)
            cursor.execute(f'SELECT {col_names_safe} FROM "{table}" LIMIT 5000')   # nosec
        except Exception as e:
            logger.warning("SELECT failed for table %s: %s", table, e)
            return []

        for row in cursor.fetchall():
            parts = []
            for col in text_cols:
                raw = row[col["name"]]
                if raw is None:
                    continue
                val = normalize_digits(str(raw).strip())
                if not val or val.lower() in ("null", "none", "", "-"):
                    continue
                # Map column name to clinical role label
                role = self._infer_role(col["name"])
                parts.append(f"{role}: {val}")

            if not parts:
                continue

            sentence = ". ".join(parts) + "."
            buffer += " " + sentence

            if len(buffer) > 900:
                produced = chunker.chunk(buffer.strip(), section)
                if produced:
                    chunks.extend(produced)
                elif buffer.strip():
                    chunks.append(ParsedChunk(content=buffer.strip(), section_title=section))
                buffer = ""

        if buffer.strip():
            produced = chunker.chunk(buffer.strip(), section)
            if produced:
                chunks.extend(produced)
            else:
                chunks.append(ParsedChunk(content=buffer.strip(), section_title=section))

        return chunks

    def _infer_role(self, col_name: str) -> str:
        """Map a raw column name to a human-readable clinical role label."""
        # Normalise: drug_a_name -> "drug a name"
        lower = col_name.lower().replace("_", " ").replace("-", " ").strip()
        # Handle positional suffixes: drug_a / drug_b -> Drug A / Drug B
        m = re.match(r"^(drug|medication|دارو|قرص)\s+([a-z0-9])$", lower)
        if m:
            return f"{self._COL_ROLES.get(m.group(1), m.group(1).title())} {m.group(2).upper()}"
        # Exact-word match (longest key first to avoid substring collisions)
        for key in sorted(self._COL_ROLES, key=len, reverse=True):
            # word-boundary check: key must appear as a whole word
            if re.search(rf"\b{re.escape(key)}\b", lower):
                return self._COL_ROLES[key]
        # Title-case the column name as fallback
        return col_name.replace("_", " ").title()
