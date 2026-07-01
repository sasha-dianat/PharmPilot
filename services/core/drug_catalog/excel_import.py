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
    else:
        raise ValueError(f"Unsupported catalog file type: {suffix} (use .xlsx or .csv)")
    out: list[dict] = []
    for r in rows:
        norm = {}
        for k, v in r.items():
            if k is None:
                continue
            val = "" if v is None else str(v).strip()
            if val.lower() in ("nan", "none"):
                val = ""
            norm[normalize_header(k)] = val
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
        df = pd.read_csv(path, dtype=str, sep=sep, engine="python")
        return df.to_dict(orient="records")
    except Exception:
        # stdlib fallback (no pandas)
        import csv
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh, delimiter=sep or ","))


def records_from_file(path: str | Path) -> list[CatalogRecord]:
    """Full pipeline: read the export file → validated CatalogRecords."""
    return build_records(read_table(path))


async def ingest_file(db, path: str | Path, *, source: str = "nfi-export") -> int:
    """Read an export file and upsert it into the catalog. Returns rows written."""
    from .importer import upsert_catalog
    return await upsert_catalog(db, records_from_file(path), source=source)
