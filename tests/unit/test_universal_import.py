"""
Tests for the universal document import engine (P6 enhanced).
Covers every new parser + auto-detect routing + directory batch import.
"""
import json
import os
import tempfile
from pathlib import Path

import pytest

from services.ai.knowledge_engine.parsers.document_parser import (
    MarkdownParser, CSVParser, RTFParser, JSONParser, PlainTextParser, DOCXParser,
)
from services.ai.knowledge_engine.ingestion.pipeline import KnowledgeIngestionPipeline


# ── Markdown ──────────────────────────────────────────────────────────────────

def test_markdown_heading_becomes_section():
    chunks = MarkdownParser().parse("# G6PD\nPersons with G6PD deficiency should avoid oxidant drugs.", "ref.md")
    assert len(chunks) >= 1
    assert any(c.section_title == "G6PD" for c in chunks)


def test_markdown_persian_content():
    md = "# کمبود G6PD\nبیماران مبتلا به فاویسم نباید نیتروفورانتوئین مصرف کنند."
    chunks = MarkdownParser().parse(md, "fa.md")
    joined = " ".join(c.content for c in chunks)
    assert "فاویسم" in joined or "G6PD" in joined


def test_markdown_strips_bold_markers():
    chunks = MarkdownParser().parse("**Metformin** is contraindicated in eGFR < 30 mg/min.", "test.md")
    assert "**" not in chunks[0].content


# ── CSV ───────────────────────────────────────────────────────────────────────

def test_csv_rows_become_prose():
    csv = "drug,dose,renal\nMetformin,500mg,contraindicated eGFR<30\nWarfarin,5mg,INR monitoring\n"
    chunks = CSVParser().parse(csv, "drugs.csv")
    combined = " ".join(c.content for c in chunks)
    assert "Metformin" in combined
    assert "Warfarin" in combined


def test_csv_persian_values():
    csv = "دارو,دوز\nمتفورمین,۵۰۰ میلی‌گرم\n"
    chunks = CSVParser().parse(csv, "fa.csv")
    combined = " ".join(c.content for c in chunks)
    assert "متفورمین" in combined


# ── JSON ──────────────────────────────────────────────────────────────────────

def test_json_list_of_dicts():
    data = [{"drug": "Amlodipine", "class": "CCB", "use": "Hypertension"},
            {"drug": "Metformin",  "class": "Biguanide", "limit": "eGFR>=30"}]
    chunks = JSONParser().parse(json.dumps(data), "drugs.json")
    combined = " ".join(c.content for c in chunks)
    assert "Amlodipine" in combined and "Metformin" in combined


def test_json_nested_dict():
    data = {"metformin": {"indication": "Type 2 diabetes", "contraindication": "eGFR < 30"}}
    chunks = JSONParser().parse(json.dumps(data), "test.json")
    combined = " ".join(c.content for c in chunks)
    assert "diabetes" in combined or "eGFR" in combined


def test_json_invalid_returns_empty():
    chunks = JSONParser().parse("{not json}", "bad.json")
    assert chunks == []


# ── RTF ───────────────────────────────────────────────────────────────────────

def test_rtf_basic_strip():
    rtf = r"{\rtf1\ansi{\fonttbl\f0 Arial;}\f0\pard Metformin is used for diabetes.\par}"
    chunks = RTFParser().parse(rtf.encode("latin-1"), "test.rtf")
    combined = " ".join(c.content for c in chunks)
    assert "Metformin" in combined or "diabetes" in combined


# ── PlainText (Persian) ───────────────────────────────────────────────────────

def test_plaintext_persian():
    text = "بیماران با کمبود G6PD نباید داروهای اکسیدکننده مصرف کنند."
    chunks = PlainTextParser().parse(text, "g6pd.txt")
    assert len(chunks) >= 1
    assert "G6PD" in chunks[0].content


# ── Auto-detect routing ───────────────────────────────────────────────────────

def test_extension_map_covers_all_formats():
    from unittest.mock import MagicMock
    pipeline = KnowledgeIngestionPipeline(vector_store=MagicMock())
    required = {".pdf", ".docx", ".md", ".txt", ".html", ".csv", ".rtf", ".epub", ".json"}
    for ext in required:
        assert ext in pipeline._EXT_MAP, f"Missing extension: {ext}"


def test_ingest_file_auto_detects_markdown(tmp_path):
    """ingest_file routes .md to MarkdownParser without needing to specify type."""
    from unittest.mock import MagicMock, AsyncMock
    p = tmp_path / "test.md"
    p.write_text("# Drug Safety\nWarfarin increases bleeding risk with NSAIDs.", encoding="utf-8")

    # Mock vector store so no actual Qdrant connection needed
    vs = MagicMock()
    vs.upsert_chunks_batch = MagicMock(return_value=1)
    vs.embed_batch = MagicMock(return_value=[[0.0] * 768])
    vs.ensure_collection = MagicMock()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)

    async def fake_process(chunks, source_id, source_meta):
        return {"status": "complete", "chunks_stored": len(chunks), "source_id": source_id}

    import asyncio
    pipeline._process_chunks = fake_process
    result = asyncio.run(pipeline.ingest_file(str(p)))
    assert result["status"] == "complete"
    assert result["chunks_stored"] >= 1


def test_ingest_directory_batch(tmp_path):
    """ingest_directory processes every supported file in a folder."""
    (tmp_path / "a.md").write_text("# Drug A\nDrug A is used for condition B.", encoding="utf-8")
    (tmp_path / "b.txt").write_text("Drug B is contraindicated in renal failure.", encoding="utf-8")
    (tmp_path / "c.csv").write_text("drug,use\nDrug C,Hypertension\n", encoding="utf-8")
    (tmp_path / "ignored.xyz").write_text("not supported", encoding="utf-8")

    from unittest.mock import MagicMock
    vs = MagicMock()
    pipeline = KnowledgeIngestionPipeline(vector_store=vs)

    async def fake_process(chunks, source_id, source_meta):
        return {"status": "complete", "chunks_stored": len(chunks), "source_id": source_id}

    import asyncio
    pipeline._process_chunks = fake_process
    result = asyncio.run(pipeline.ingest_directory(str(tmp_path), recursive=False))
    assert result["total"] == 3              # .xyz not counted
    assert result["ingested"] == 3
    assert result["failed"] == 0
