"""Read the official فهرست رسمی دارویی / NFI export (Excel or CSV) into the catalog.

Column headers are normalized (spaces/ZWNJ → '_', lowercased) and matched against
the alias table in importer.py, so the real FDA export's Persian headers map with
no per-file config. Persian/Arabic digits and thousands separators are handled.
Usage:  records = records_from_file("darou_list.xlsx");  then upsert_catalog(...).
"""
from __future__ import annotations

from pathlib import Path

from .importer import build_records, normalize_header
from .schema import CatalogRecord


def read_table(path: str | Path) -> list[dict]:
    """Read .xlsx/.xls/.csv → list of row dicts with normalized header keys."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm", ".xls"):
        rows = _read_excel(path)
    elif suffix in (".csv", ".tsv", ".txt"):
        rows = _read_csv(path, sep="\t" if suffix == ".tsv" else None)
    elif suffix in (".html", ".htm"):
        rows = _read_html(path)
    else:
        raise ValueError(f"Unsupported catalog file type: {suffix} (use .xlsx, .csv or .html)")
    out: list[dict] = []
    for r in rows:
        norm = {}
        for k, v in r.items():
            if k is None:
                continue
            # NUL bytes (legacy .xls padding) are rejected by Postgres JSONB —
            # strip them here so every ingestion path is safe to persist.
            val = "" if v is None else str(v).replace("\x00", "").strip()
            if val.lower() in ("nan", "none"):
                val = ""
            norm[normalize_header(str(k).replace("\x00", ""))] = val
        out.append(norm)
    return out


def _read_excel(path: Path) -> list[dict]:
    try:
        import pandas as pd
    except Exception as e:  # pragma: no cover
        raise RuntimeError("pandas is required to read Excel catalogs") from e
    df = pd.read_excel(path, dtype=str)
    return df.to_dict(orient="records")


def _read_csv(path: Path, sep=None) -> list[dict]:
    try:
        import pandas as pd
        df = pd.read_csv(path, dtype=str, sep=sep, engine="python",
                         encoding="utf-8-sig")
        return df.to_dict(orient="records")
    except Exception:
        # stdlib fallback (no pandas)
        import csv
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh, delimiter=sep or ","))


def _read_html(path: Path) -> list[dict]:
    """Extract the largest <table> from a saved HTML page (insurer دارونامه pages)."""
    try:
        import pandas as pd
        tables = pd.read_html(path, flavor="lxml" if _has_lxml() else None)
        if not tables:
            return []
        df = max(tables, key=len).astype(str)
        return df.to_dict(orient="records")
    except Exception:
        return []


def _has_lxml() -> bool:
    try:
        import lxml  # noqa: F401
        return True
    except Exception:
        return False


def records_from_file(path: str | Path) -> list[CatalogRecord]:
    """Full pipeline: read the export file → validated CatalogRecords."""
    return build_records(read_table(path))


async def ingest_file(db, path: str | Path, *, source: str = "nfi-export") -> int:
    """Read an export file and upsert it into the catalog. Returns rows written."""
    from .importer import upsert_catalog
    return await upsert_catalog(db, records_from_file(path), source=source)
