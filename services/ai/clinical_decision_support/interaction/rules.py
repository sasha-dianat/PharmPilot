from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .severity import InteractionSeverity, normalize_severity

_DATA = Path(__file__).parent / "data" / "interaction_rules.yaml"


@dataclass(frozen=True)
class InteractionRule:
    kind: str                # drug_drug | drug_disease
    left: str
    right: str
    severity: InteractionSeverity
    mechanism: str
    action: str
    evidence: tuple[str, ...]
    confidence: float
    source: str              # curated | ddinter


@dataclass(frozen=True)
class RuleIndex:
    drug_drug: tuple[InteractionRule, ...]
    drug_disease: tuple[InteractionRule, ...]

    def find_drug_drug(self, a_tokens: set[str], b_tokens: set[str]) -> list[InteractionRule]:
        out = []
        for r in self.drug_drug:
            if (r.left in a_tokens and r.right in b_tokens) or \
               (r.left in b_tokens and r.right in a_tokens):
                out.append(r)
        return out

    def find_drug_disease(self, drug_tokens: set[str], condition: str) -> list[InteractionRule]:
        return [r for r in self.drug_disease
                if r.left in drug_tokens and r.right == condition]


def _parse(e: dict) -> InteractionRule:
    return InteractionRule(
        kind=e["kind"], left=e["left"], right=e["right"],
        severity=normalize_severity(e["severity"]),
        mechanism=e.get("mechanism", ""), action=e.get("action", ""),
        evidence=tuple(e.get("evidence", [])), confidence=float(e.get("confidence", 0.8)),
        source=e.get("source", "curated"),
    )


@lru_cache(maxsize=1)
def load_rule_index() -> RuleIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    rules = [_parse(e) for e in raw]
    return RuleIndex(
        drug_drug=tuple(r for r in rules if r.kind == "drug_drug"),
        drug_disease=tuple(r for r in rules if r.kind == "drug_disease"),
    )
