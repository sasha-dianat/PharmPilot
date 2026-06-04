#!/usr/bin/env python3
"""
PharmPilot Knowledge Base — Owner Reference Import Tool
=========================================================
Feed the clinical brain with any local reference library.
Supports every common document format:

    PDF  DOCX/DOC  TXT  HTML/HTM  MD  CSV/TSV  RTF  EPUB  JSON/JSONL

Usage:
  # Import a single file
  python scripts/import_references.py --file /path/to/pharmacopeia.pdf

  # Import an entire directory tree
  python scripts/import_references.py --dir /path/to/references/

  # Import a website (BFS crawler)
  python scripts/import_references.py --url https://formulary.example.ir/ --max-pages 200

  # Show what would be ingested without actually doing it (dry run)
  python scripts/import_references.py --dir /path/to/refs/ --dry-run

  # Override language / collection tag
  python scripts/import_references.py --dir /refs/ --language fa --collection iranian_pharmacopeia

Supported extensions (auto-detected):
  .pdf .docx .doc .md .markdown .txt .text
  .html .htm .xhtml .csv .tsv .rtf .epub .json .jsonl

The script connects directly to Qdrant + PostgreSQL (same credentials as the
running application). After import, run:
  curl http://localhost:8001/api/v1/knowledge/stats
to see the updated vector count.
"""
import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("import-references")

QDRANT_URL    = os.getenv("QDRANT_URL", "http://localhost:6334")
DATABASE_URL  = os.getenv("DATABASE_URL", "postgresql://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot")

SUPPORTED_EXTENSIONS = {
    ".pdf", ".docx", ".doc", ".md", ".markdown",
    ".txt", ".text", ".html", ".htm", ".xhtml",
    ".csv", ".tsv", ".rtf", ".epub", ".json", ".jsonl",
}


def _count_files(directory: str, recursive: bool) -> dict[str, list[Path]]:
    """Count supported files by extension, for the dry-run report."""
    root = Path(directory)
    glob = "**/*" if recursive else "*"
    by_ext: dict[str, list[Path]] = {}
    for f in root.glob(glob):
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS:
            by_ext.setdefault(f.suffix.lower(), []).append(f)
    return by_ext


async def _ingest_file(file_path: str, language: str, collection: str, title: str | None):
    from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
    from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline

    vs = ClinicalVectorStore(qdrant_url=QDRANT_URL)
    vs.ensure_collection()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)
    result = await pipeline.ingest_file(
        file_path=file_path,
        title=title,
        language=language,
        collection=collection,
    )
    return result


async def _ingest_directory(directory: str, language: str, collection: str, recursive: bool):
    from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
    from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline

    vs = ClinicalVectorStore(qdrant_url=QDRANT_URL)
    vs.ensure_collection()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)
    result = await pipeline.ingest_directory(
        directory=directory,
        recursive=recursive,
        language=language,
        collection=collection,
    )
    return result


async def _crawl_url(url: str, max_pages: int, language: str, collection: str):
    from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
    from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline

    vs = ClinicalVectorStore(qdrant_url=QDRANT_URL)
    vs.ensure_collection()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)
    result = await pipeline.crawl_site(
        start_url=url,
        max_pages=max_pages,
        language=language,
        collection=collection,
    )
    return result


async def _seed_pubmed(queries: list[tuple[str, int]], language: str, collection: str):
    """PubMed search queries: list of (query_string, max_results)."""
    from services.ai.knowledge_engine.vector_store import ClinicalVectorStore
    from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline
    import asyncio, time

    vs = ClinicalVectorStore(qdrant_url=QDRANT_URL)
    vs.ensure_collection()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)
    total = 0
    for query, max_res in queries:
        log.info("PubMed search: '%s' (max %d)…", query, max_res)
        result = await pipeline.ingest_pubmed_search(query=query, max_results=max_res)
        n = result.get("chunks_stored", 0)
        total += n
        log.info("  → %d chunks", n)
        await asyncio.sleep(1.0)
    log.info("PubMed total: %d chunks ingested", total)
    return total


def _print_stats():
    """Print current Qdrant vector count."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=QDRANT_URL)
        info = client.get_collection("pharmpilot_clinical_knowledge")
        count = getattr(info, "vectors_count", None) or getattr(info, "points_count", 0) or 0
        log.info("Current knowledge base: %d vectors in Qdrant", count)
    except Exception as e:
        log.warning("Could not read Qdrant stats: %s", e)


# ── Default PubMed queries (can be overridden by --pubmed-queries) ────────────
DEFAULT_PUBMED = [
    ("drug interaction clinical pharmacology review", 200),
    ("opioid benzodiazepine respiratory depression concurrent use", 150),
    ("metformin renal impairment contraindication lactic acidosis", 100),
    ("warfarin drug interaction management INR monitoring", 150),
    ("beers criteria elderly inappropriate medications harm", 100),
    ("antibiotic stewardship pharmacist intervention outcomes", 100),
    ("medication adherence intervention pharmacist diabetes hypertension", 100),
    ("statin myopathy rhabdomyolysis drug interaction CYP3A4", 100),
    ("SSRI serotonin syndrome drug interaction symptoms management", 80),
    ("QT prolongation drug-induced torsades de pointes prevention", 80),
    ("polypharmacy elderly adverse drug events falls hospitalization", 100),
    ("naloxone opioid overdose reversal community pharmacy dispensing", 60),
    ("G6PD deficiency oxidant drugs hemolysis favism pharmacist", 60),
    ("NSAIDs renal toxicity cardiovascular risk elderly", 80),
    ("medication therapy management pharmacist clinical outcomes", 80),
]


def main():
    parser = argparse.ArgumentParser(
        description="PharmPilot reference library importer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file",    help="Single file path to ingest")
    group.add_argument("--dir",     help="Directory to ingest recursively")
    group.add_argument("--url",     help="Website URL to crawl and ingest")
    group.add_argument("--pubmed",  action="store_true", help="Seed from default PubMed queries")
    group.add_argument("--stats",   action="store_true", help="Show current knowledge base stats")

    parser.add_argument("--title",      default=None, help="Override document title")
    parser.add_argument("--language",   default="fa", help="Content language (fa=Persian, en=English)")
    parser.add_argument("--collection", default="owner_references",
                        help="Collection tag (owner_references, iranian_pharmacopeia, guidelines, …)")
    parser.add_argument("--max-pages",  type=int, default=200,
                        help="Max pages for --url crawler (default: 200)")
    parser.add_argument("--no-recursive", action="store_true",
                        help="Do not recurse into subdirectories with --dir")
    parser.add_argument("--dry-run",   action="store_true",
                        help="Show what would be ingested without actually ingesting")

    args = parser.parse_args()

    if args.stats:
        _print_stats()
        return

    if args.dry_run:
        if args.dir:
            by_ext = _count_files(args.dir, not args.no_recursive)
            total = sum(len(v) for v in by_ext.values())
            log.info("DRY RUN — %d supported files found in %s:", total, args.dir)
            for ext, files in sorted(by_ext.items()):
                log.info("  %5s  %4d files", ext, len(files))
                for f in files[:3]:
                    log.info("         %s", f.name)
                if len(files) > 3:
                    log.info("         … and %d more", len(files) - 3)
        elif args.file:
            ext = Path(args.file).suffix.lower()
            log.info("DRY RUN — would ingest: %s (format: %s)", args.file, ext)
        return

    if args.file:
        log.info("Ingesting single file: %s", args.file)
        result = asyncio.run(_ingest_file(args.file, args.language, args.collection, args.title))
        log.info("Result: status=%s chunks=%d", result.get("status"), result.get("chunks_stored", 0))

    elif args.dir:
        log.info("Ingesting directory: %s (recursive=%s)", args.dir, not args.no_recursive)
        result = asyncio.run(_ingest_directory(args.dir, args.language, args.collection, not args.no_recursive))
        log.info("Done: ingested=%d failed=%d skipped=%d",
                 result.get("ingested", 0), result.get("failed", 0), result.get("skipped", 0))
        # Print per-file summary
        for f in result.get("files", []):
            icon = "✅" if f["status"] == "complete" else ("⚠️" if f["status"] == "empty" else "❌")
            log.info("  %s %s → %d chunks", icon, f["path"], f.get("chunks", 0))

    elif args.url:
        log.info("Crawling URL: %s (max_pages=%d)", args.url, args.max_pages)
        result = asyncio.run(_crawl_url(args.url, args.max_pages, args.language, args.collection))
        log.info("Done: ingested=%d pages failed=%d", result.get("ingested", 0), result.get("failed", 0))

    elif args.pubmed:
        log.info("Seeding from %d default PubMed query groups…", len(DEFAULT_PUBMED))
        total = asyncio.run(_seed_pubmed(DEFAULT_PUBMED, args.language, args.collection))
        log.info("PubMed seeding complete: %d total chunks", total)

    _print_stats()
    log.info("Done. Restart the API to warm the embedding cache.")


if __name__ == "__main__":
    main()
