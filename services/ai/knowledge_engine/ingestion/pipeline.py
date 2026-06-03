"""
Knowledge ingestion pipeline.
Accepts any supported source, extracts text, chunks, embeds, and stores.
Runs continuously — new content is immediately searchable after ingestion.
"""
import asyncio
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from uuid import UUID, uuid4

from services.ai.knowledge_engine.parsers.document_parser import (
    HTMLParser, PDFParser, PlainTextParser, PubMedParser, ParsedChunk, DOCXParser
)
from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
from services.ai.knowledge_engine.schema import SourceType

logger = logging.getLogger(__name__)


class MedicalNERTagger:
    """
    Lightweight Named Entity Recognition to extract drug/condition mentions.
    Used to populate filter metadata on each chunk.
    """

    # Common drug classes for fast keyword matching
    DRUG_PATTERNS = [
        r"\b[a-z]+(?:mab|nib|zumab|kinib|statin|prazole|sartan|olol|pril|ine|ide|ate)\b"
    ]

    def tag_chunk(self, text: str) -> dict:
        """Extract drug and condition mentions from text."""
        drugs = self._extract_drugs(text)
        conditions = self._extract_conditions(text)
        specialties = self._infer_specialties(text)
        evidence = self._extract_evidence_grade(text)
        return {
            "drugs_mentioned": list(set(d.lower() for d in drugs)),
            "conditions_mentioned": list(set(c.lower() for c in conditions)),
            "specialty_tags": specialties,
            "evidence_grade": evidence,
        }

    def _extract_drugs(self, text: str) -> list[str]:
        import re
        drugs = []
        # Drug suffix pattern
        for pattern in self.DRUG_PATTERNS:
            drugs.extend(re.findall(pattern, text.lower()))
        # Known drug names (first 50 most common)
        COMMON_DRUGS = [
            "metformin", "lisinopril", "atorvastatin", "amlodipine", "omeprazole",
            "metoprolol", "albuterol", "gabapentin", "sertraline", "escitalopram",
            "levothyroxine", "warfarin", "insulin", "aspirin", "acetaminophen",
            "ibuprofen", "prednisone", "amoxicillin", "azithromycin", "ciprofloxacin",
            "furosemide", "hydrochlorothiazide", "losartan", "pantoprazole",
            "clonazepam", "alprazolam", "oxycodone", "hydrocodone", "tramadol",
            "morphine", "fentanyl", "codeine", "naloxone", "buprenorphine",
            "clopidogrel", "apixaban", "rivaroxaban", "dabigatran", "enoxaparin",
        ]
        text_lower = text.lower()
        for drug in COMMON_DRUGS:
            if drug in text_lower:
                drugs.append(drug)
        return list(set(drugs))[:20]  # Cap at 20

    def _extract_conditions(self, text: str) -> list[str]:
        CONDITIONS = [
            "diabetes", "hypertension", "heart failure", "atrial fibrillation",
            "chronic kidney disease", "copd", "asthma", "depression", "anxiety",
            "hypothyroidism", "hyperlipidemia", "osteoporosis", "gout",
            "pneumonia", "sepsis", "stroke", "myocardial infarction",
        ]
        text_lower = text.lower()
        return [c for c in CONDITIONS if c in text_lower][:10]

    def _infer_specialties(self, text: str) -> list[str]:
        SPECIALTY_KEYWORDS = {
            "cardiology": ["cardiac", "heart", "atrial", "ventricular", "coronary", "arrhythmia"],
            "endocrinology": ["diabetes", "insulin", "thyroid", "glucose", "a1c", "hba1c"],
            "nephrology": ["renal", "kidney", "egfr", "creatinine", "dialysis"],
            "psychiatry": ["depression", "anxiety", "schizophrenia", "bipolar", "ssri", "antidepressant"],
            "infectious_disease": ["antibiotic", "antimicrobial", "bacterial", "viral", "infection"],
            "oncology": ["cancer", "chemotherapy", "tumor", "malignancy", "neoplasm"],
            "geriatrics": ["elderly", "geriatric", "beers", "falls", "dementia"],
            "pain_management": ["opioid", "pain", "naloxone", "morphine", "mme"],
            "pulmonology": ["asthma", "copd", "inhaler", "bronchial", "respiratory"],
        }
        text_lower = text.lower()
        tags = []
        for specialty, keywords in SPECIALTY_KEYWORDS.items():
            if any(kw in text_lower for kw in keywords):
                tags.append(specialty)
        return tags

    def _extract_evidence_grade(self, text: str) -> Optional[str]:
        import re
        match = re.search(
            r"(?:evidence|grade|class|level)[\s:]+([IA-VB]{1,3})",
            text, re.IGNORECASE
        )
        if match:
            return match.group(1).upper()
        return None


class KnowledgeIngestionPipeline:
    """
    Main ingestion coordinator.
    Accepts files, URLs, PubMed IDs, or raw text.
    Processes through parse → tag → embed → store pipeline.
    """

    def __init__(
        self,
        vector_store: ClinicalVectorStore,
        db=None,
    ):
        self.vs = vector_store
        self.db = db
        self.ner = MedicalNERTagger()
        self.pdf_parser = PDFParser()
        self.html_parser = HTMLParser()
        self.text_parser = PlainTextParser()
        self.pubmed_parser = PubMedParser()

    # ── Primary ingestion methods ─────────────────────────────────────────────

    async def ingest_pdf(
        self,
        pdf_path: str,
        source_type: SourceType = SourceType.CUSTOM_DOCUMENT,
        title: Optional[str] = None,
        evidence_level: Optional[str] = None,
        specialty_tags: Optional[list] = None,
    ) -> dict:
        """Ingest a PDF file (guideline, package insert, textbook chapter)."""
        path = Path(pdf_path)
        title = title or path.stem.replace("_", " ").title()

        logger.info("Ingesting PDF: %s", title)
        chunks = self.pdf_parser.parse(pdf_path)

        source_id = str(uuid4())
        return await self._process_chunks(
            chunks=chunks,
            source_id=source_id,
            source_meta={
                "source_id": source_id,
                "source_title": title,
                "source_type": source_type.value,
                "evidence_level": evidence_level,
                "base_specialty_tags": specialty_tags or [],
            },
        )

    async def ingest_docx(
        self,
        docx_path: str,
        source_type: SourceType = SourceType.CUSTOM_DOCUMENT,
        title: Optional[str] = None,
        language: str = "fa",
        collection: str = "owner_references",
        evidence_level: Optional[str] = None,
        specialty_tags: Optional[list] = None,
    ) -> dict:
        """
        Ingest a Word .docx the owner uploads (Iranian pharmacopeia monograph,
        formulary, SOP). Persian-aware. Tagged into the owner-reference corpus.
        """
        path = Path(docx_path)
        title = title or path.stem.replace("_", " ").strip()
        logger.info("Ingesting DOCX reference: %s (lang=%s)", title, language)
        parser = DOCXParser()
        chunks = parser.parse(docx_path)
        if not chunks:
            return {"status": "empty", "title": title}

        source_id = str(uuid4())
        return await self._process_chunks(
            chunks=chunks,
            source_id=source_id,
            source_meta={
                "source_id": source_id,
                "source_title": title,
                "source_type": source_type.value,
                "language": language,
                "collection": collection,
                "evidence_level": evidence_level,
                "base_specialty_tags": specialty_tags or [],
            },
        )

    async def crawl_site(
        self,
        start_url: str,
        max_pages: int = 200,
        same_domain_only: bool = True,
        language: str = "fa",
        collection: str = "owner_references",
        session_cookies: Optional[dict] = None,
    ) -> dict:
        """
        Generic owner-fed reference crawler. BFS over links starting at start_url
        (optionally constrained to the same domain), ingesting each HTML page into
        the owner-reference corpus. Use for public Iranian pharmacopeia / formulary
        sites the owner points us at. Polite (rate-limited, sets a UA).
        """
        import httpx
        from urllib.parse import urljoin, urlparse
        from bs4 import BeautifulSoup

        root_domain = urlparse(start_url).netloc
        seen: set[str] = set()
        queue: list[str] = [start_url]
        result = {"ingested": 0, "failed": 0, "pages_visited": 0}

        headers = {"User-Agent": "PharmPilot-KnowledgeBot/1.0 (owner-fed references)"}
        async with httpx.AsyncClient(timeout=30, follow_redirects=True,
                                     cookies=session_cookies or {}) as client:
            while queue and result["pages_visited"] < max_pages:
                url = queue.pop(0)
                if url in seen:
                    continue
                seen.add(url)
                result["pages_visited"] += 1
                try:
                    resp = await client.get(url, headers=headers)
                    resp.raise_for_status()
                    html = resp.text
                except Exception as e:
                    result["failed"] += 1
                    logger.warning("crawl_site fetch failed %s: %s", url, e)
                    continue

                chunks = self.html_parser.parse(html, url)
                if chunks:
                    source_id = str(uuid4())
                    ingest = await self._process_chunks(
                        chunks=chunks,
                        source_id=source_id,
                        source_meta={
                            "source_id": source_id,
                            "source_title": self._extract_title_from_html(html) or url,
                            "source_type": SourceType.WEB_CRAWL.value,
                            "url": url,
                            "language": language,
                            "collection": collection,
                        },
                    )
                    if ingest.get("status") == "complete":
                        result["ingested"] += 1

                # enqueue same-domain links
                try:
                    soup = BeautifulSoup(html, "html.parser")
                    for a in soup.find_all("a", href=True):
                        nxt = urljoin(url, a["href"]).split("#")[0]
                        if nxt in seen:
                            continue
                        if same_domain_only and urlparse(nxt).netloc != root_domain:
                            continue
                        if nxt.startswith("http"):
                            queue.append(nxt)
                except Exception:
                    pass
                await asyncio.sleep(1.0)  # polite delay

        logger.info("crawl_site done: %d ingested / %d visited",
                    result["ingested"], result["pages_visited"])
        return result

    async def ingest_url(
        self,
        url: str,
        source_type: SourceType = SourceType.WEB_CRAWL,
        title: Optional[str] = None,
        requires_login: bool = False,
        session_cookies: Optional[dict] = None,
    ) -> dict:
        """
        Fetch and ingest a web page.
        Supports authenticated sessions (UpToDate, Epocrates, etc.)
        when session_cookies are provided.
        """
        import httpx
        logger.info("Fetching URL: %s", url)

        headers = {
            "User-Agent": "PharmPilot-KnowledgeBot/1.0 (Clinical AI System; education@pharmpilot.ai)",
        }

        try:
            async with httpx.AsyncClient(
                timeout=30,
                cookies=session_cookies or {},
                follow_redirects=True,
            ) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                html = response.text
                inferred_title = title or self._extract_title_from_html(html) or url
        except Exception as exc:
            logger.error("URL fetch failed for %s: %s", url, exc)
            return {"status": "error", "error": str(exc)}

        chunks = self.html_parser.parse(html, url)
        if not chunks:
            return {"status": "empty", "url": url}

        source_id = str(uuid4())
        return await self._process_chunks(
            chunks=chunks,
            source_id=source_id,
            source_meta={
                "source_id": source_id,
                "source_title": inferred_title,
                "source_type": source_type.value,
                "url": url,
            },
        )

    async def ingest_uptodate_session(
        self,
        session_cookies: dict,
        article_urls: list[str],
        max_concurrent: int = 3,
    ) -> dict:
        """
        Bulk ingest UpToDate articles using an authenticated session.
        Respects rate limiting — max_concurrent requests at a time.

        Usage:
            After logging into UpToDate in a browser, extract session cookies
            (e.g., using browser DevTools → Application → Cookies) and pass here.
            The system will browse each article and ingest the full content.
        """
        semaphore = asyncio.Semaphore(max_concurrent)
        results = {"ingested": 0, "failed": 0, "total": len(article_urls)}

        async def ingest_one(url: str):
            async with semaphore:
                result = await self.ingest_url(
                    url=url,
                    source_type=SourceType.UPTODATE,
                    session_cookies=session_cookies,
                )
                if result.get("status") == "complete":
                    results["ingested"] += 1
                    logger.info("UpToDate ✓ %s (%d chunks)", url[-60:], result.get("chunks_stored", 0))
                else:
                    results["failed"] += 1
                    logger.warning("UpToDate ✗ %s: %s", url[-60:], result.get("error", "unknown"))
                await asyncio.sleep(1.5)  # Polite delay between requests

        await asyncio.gather(*[ingest_one(url) for url in article_urls])
        logger.info(
            "UpToDate bulk ingest complete: %d/%d articles ingested",
            results["ingested"], results["total"]
        )
        return results

    async def ingest_uptodate_library_crawl(
        self,
        session_cookies: dict,
        start_url: str = "https://www.uptodate.com/contents/table-of-contents",
        max_articles: int = 5000,
    ) -> dict:
        """
        Autonomously crawl the UpToDate table of contents and ingest all articles.
        With valid session cookies, this will systematically ingest the entire
        clinical library — the knowledge base grows richer with every article.
        """
        import httpx
        from bs4 import BeautifulSoup

        logger.info("Starting UpToDate library crawl — target: %d articles", max_articles)
        article_urls = set()
        to_crawl = {start_url}
        crawled = set()

        async with httpx.AsyncClient(
            cookies=session_cookies, timeout=30, follow_redirects=True
        ) as client:
            while to_crawl and len(article_urls) < max_articles:
                url = to_crawl.pop()
                if url in crawled:
                    continue
                crawled.add(url)

                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "html.parser")

                    # Find article links
                    for link in soup.find_all("a", href=True):
                        href = link["href"]
                        if "/contents/" in href and not "/table-of-contents" in href:
                            full_url = f"https://www.uptodate.com{href}" if href.startswith("/") else href
                            if full_url not in crawled:
                                article_urls.add(full_url)

                    await asyncio.sleep(0.8)  # Respectful crawl rate

                except Exception as exc:
                    logger.warning("Crawl failed for %s: %s", url, exc)

        logger.info("Discovered %d UpToDate articles — beginning ingestion", len(article_urls))
        return await self.ingest_uptodate_session(
            session_cookies=session_cookies,
            article_urls=list(article_urls)[:max_articles],
        )

    async def ingest_pubmed_search(
        self,
        search_query: str,
        max_results: int = 100,
    ) -> dict:
        """
        Search PubMed and ingest all matching abstracts.
        Example query: "drug interaction warfarin aspirin[MeSH]"
        """
        logger.info("PubMed search: '%s' (max %d)", search_query, max_results)
        articles = await self.pubmed_parser.search_and_fetch(search_query, max_results)

        total_chunks = 0
        for article in articles:
            if not article.get("abstract"):
                continue
            chunks = self.text_parser.parse(
                f"# {article['title']}\n\n{article['abstract']}",
                filename=f"pubmed_{article['pmid']}",
            )
            source_id = str(uuid4())
            result = await self._process_chunks(
                chunks=chunks,
                source_id=source_id,
                source_meta={
                    "source_id": source_id,
                    "source_title": article["title"],
                    "source_type": SourceType.PUBMED_ARTICLE.value,
                    "url": article.get("url"),
                    "doi": article.get("doi"),
                    "authors": article.get("authors"),
                    "pmid": article.get("pmid"),
                },
            )
            total_chunks += result.get("chunks_stored", 0)

        return {
            "status": "complete",
            "articles_ingested": len(articles),
            "chunks_stored": total_chunks,
        }

    async def ingest_raw_text(
        self,
        text: str,
        title: str,
        source_type: SourceType = SourceType.CUSTOM_DOCUMENT,
        specialty_tags: Optional[list] = None,
    ) -> dict:
        """
        Ingest raw text directly — paste in a guideline, drug information, etc.
        This is the quickest way to add new knowledge.
        """
        chunks = self.text_parser.parse(text, title)
        source_id = str(uuid4())
        return await self._process_chunks(
            chunks=chunks,
            source_id=source_id,
            source_meta={
                "source_id": source_id,
                "source_title": title,
                "source_type": source_type.value,
                "base_specialty_tags": specialty_tags or [],
            },
        )

    # ── Core processing ───────────────────────────────────────────────────────

    async def _process_chunks(
        self,
        chunks: list[ParsedChunk],
        source_id: str,
        source_meta: dict,
    ) -> dict:
        """Tag, embed, and store a list of parsed chunks."""
        if not chunks:
            return {"status": "empty", "chunks_stored": 0}

        # NER tagging
        tagged_chunks = []
        for chunk in chunks:
            tags = self.ner.tag_chunk(chunk.content)
            merged_tags = {
                **source_meta,
                "content": chunk.content,
                "section_title": chunk.section_title,
                "page_number": chunk.page_number,
                "chunk_index": chunk.chunk_index,
                "drugs_mentioned": tags["drugs_mentioned"],
                "conditions_mentioned": tags["conditions_mentioned"],
                "specialty_tags": list(set(
                    tags["specialty_tags"] + source_meta.get("base_specialty_tags", [])
                )),
                "evidence_grade": tags["evidence_grade"] or source_meta.get("evidence_level"),
            }
            tagged_chunks.append({
                "id": str(uuid4()),
                "content": chunk.content,
                "metadata": merged_tags,
            })

        # Batch embed and store
        stored = self.vs.upsert_chunks_batch(tagged_chunks)

        logger.info(
            "Ingested '%s': %d chunks stored (source_id=%s)",
            source_meta.get("source_title", "?")[:60],
            stored,
            source_id,
        )
        return {"status": "complete", "source_id": source_id, "chunks_stored": stored}

    def _extract_title_from_html(self, html: str) -> Optional[str]:
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            tag = soup.find("h1") or soup.find("title")
            return tag.get_text(strip=True)[:200] if tag else None
        except Exception:
            return None
