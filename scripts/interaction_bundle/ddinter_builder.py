"""Build a bundle-v1 interaction SQLite from DDInter rows.

Format-agnostic: the Colab notebook adapts DDInter's CSVs into RawInteraction
rows and calls build_rules() + write_bundle(). Tokens are produced by the
engine's own normalize(), so a bundle rule matches the same real-Rx names the
engine derives — see tests/unit/test_ddinter_builder.py::test_end_to_end_alignment.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from services.ai.clinical_decision_support.normalizer import normalize
from services.ai.clinical_decision_support.interaction.bundle import SCHEMA_VERSION

_LEVEL_MAP = {"major": "Major", "moderate": "Moderate", "minor": "Minor",
              "contraindicated": "Contraindicated"}
_RANK = {"Minor": 0, "Moderate": 1, "Major": 2, "Contraindicated": 3}
_IMPORT_CONFIDENCE = 0.7


@dataclass
class RawInteraction:
    drug_a: str
    drug_b: str
    level: str
    mechanism: str = ""


def build_rules(rows: Iterable[RawInteraction]) -> list[dict]:
    best: dict[frozenset, dict] = {}
    for row in rows:
        a, b = normalize(row.drug_a), normalize(row.drug_b)
        if not a or not b or a == b:
            continue
        severity = _LEVEL_MAP.get((row.level or "").strip().lower(), "Minor")
        rule = {
            "kind": "drug_drug", "left": a, "right": b, "severity": severity,
            "mechanism": (row.mechanism or "").strip() or "DDInter-listed interaction.",
            "evidence": ["DDInter 2.0"], "confidence": _IMPORT_CONFIDENCE,
        }
        key = frozenset((a, b))
        if key not in best or _RANK[severity] > _RANK[best[key]["severity"]]:
            best[key] = rule
    return list(best.values())


def write_bundle(rules: list[dict], dest: Path, datasets: dict) -> dict:
    dest = Path(dest)
    if dest.exists():
        dest.unlink()
    payloads = sorted(json.dumps(r, sort_keys=True) for r in rules)
    checksum = hashlib.sha256("\n".join(payloads).encode()).hexdigest()
    meta = {
        "schema_version": SCHEMA_VERSION,
        "datasets": json.dumps(datasets),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "checksum": checksum,
        "rule_count": len(rules),
        "attribute_count": 0,
    }
    con = sqlite3.connect(dest)
    try:
        con.execute("CREATE TABLE interaction_rules (id INTEGER PRIMARY KEY, source TEXT, payload TEXT)")
        con.execute("CREATE TABLE drug_attributes (ingredient TEXT PRIMARY KEY, payload TEXT)")
        con.execute("CREATE TABLE bundle_meta (key TEXT PRIMARY KEY, value TEXT)")
        con.executemany("INSERT INTO interaction_rules (source, payload) VALUES ('ddinter', ?)",
                        [(json.dumps(r),) for r in rules])
        con.executemany("INSERT INTO bundle_meta (key, value) VALUES (?, ?)", list(meta.items()))
        con.commit()
    finally:
        con.close()
    return meta
