from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

BUNDLE_PATH = Path(__file__).parent / "data" / "bundle.sqlite"
SCHEMA_VERSION = "bundle-v1"

logger = logging.getLogger(__name__)


def _open(path: Path):
    """Read-only connection. Raises if the file is missing or not a DB."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.execute("SELECT 1")  # force a read so a non-DB file fails here, not later
    return con


def _schema_ok(con: sqlite3.Connection) -> bool:
    try:
        row = con.execute("SELECT value FROM bundle_meta WHERE key='schema_version'").fetchone()
        return bool(row) and row[0] == SCHEMA_VERSION
    except sqlite3.Error:
        return False


def _rows(table: str, path: Path | None) -> list[str]:
    path = path or BUNDLE_PATH
    if not path.exists():
        return []
    con = None
    try:
        con = _open(path)
        if not _schema_ok(con):
            logger.warning("[interaction.bundle] schema mismatch/absent in %s — ignoring", path)
            return []
        col = "payload"
        return [r[0] for r in con.execute(f"SELECT {col} FROM {table}")]
    except sqlite3.Error as exc:
        logger.warning("[interaction.bundle] cannot read %s from %s: %s", table, path, exc)
        return []
    finally:
        if con is not None:
            con.close()


def _parse_payloads(payloads: list[str]) -> list[dict]:
    out: list[dict] = []
    for p in payloads:
        try:
            d = json.loads(p)
            if isinstance(d, dict):
                out.append(d)
        except (ValueError, TypeError):
            continue
    return out


def read_rule_payloads(path: Path | None = None) -> list[dict]:
    out = _parse_payloads(_rows("interaction_rules", path))
    for d in out:
        d["source"] = "ddinter"   # precedence is unambiguous regardless of stored value
    return out


def read_attribute_payloads(path: Path | None = None) -> list[dict]:
    return _parse_payloads(_rows("drug_attributes", path))


def bundle_stats(path: Path | None = None) -> dict:
    path = path or BUNDLE_PATH
    if not path.exists():
        return {}
    con = None
    try:
        con = _open(path)
        if not _schema_ok(con):
            return {}
        return {k: v for k, v in con.execute("SELECT key, value FROM bundle_meta")}
    except sqlite3.Error:
        return {}
    finally:
        if con is not None:
            con.close()
