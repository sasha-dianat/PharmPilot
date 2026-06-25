from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .bundle import read_rule_payloads
from .severity import InteractionSeverity, normalize_severity

_DATA = Path(__file__).parent / "data" / "interaction_rules.yaml"


@dataclass(frozen=True)
class InteractionRule:
    kind: str                # drug_drug | drug_disease | drug_context
    left: str
    right: str
    severity: InteractionSeverity
    mechanism: str
    action: str
    evidence: tuple[str, ...]
    confidence: float
    source: str              # curated | ddinter
    requires_lab: str | None = None
    thresholds: tuple[dict, ...] = ()
    missing_severity: InteractionSeverity | None = None
    requires_age_min: int | None = None
    lab_escalation: dict | None = None


@dataclass(frozen=True)
class RuleIndex:
    drug_drug: tuple[InteractionRule, ...]
    drug_disease: tuple[InteractionRule, ...]
    drug_context: tuple[InteractionRule, ...]

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

    def find_drug_context(self, drug_tokens: set[str]) -> list[InteractionRule]:
        return [r for r in self.drug_context if r.left in drug_tokens]


def _parse(e: dict) -> InteractionRule:
    sev_missing = e.get("missing_severity")
    lab_esc = e.get("lab_escalation")
    if lab_esc:
        lab_esc = {"lab": lab_esc["lab"],
                   "steps": [{"min": s["min"], "severity": normalize_severity(s["severity"])}
                             for s in lab_esc["steps"]]}
    return InteractionRule(
        kind=e["kind"], left=e["left"], right=e.get("right", ""),
        severity=normalize_severity(e.get("severity", "Moderate")),
        mechanism=e.get("mechanism", ""), action=e.get("action", ""),
        evidence=tuple(e.get("evidence", [])), confidence=float(e.get("confidence", 0.8)),
        source=e.get("source", "curated"),
        requires_lab=e.get("requires_lab"),
        thresholds=tuple({"max": t["max"], "severity": normalize_severity(t["severity"]),
                          "note": t.get("note", "")} for t in e.get("thresholds", [])),
        missing_severity=normalize_severity(sev_missing) if sev_missing else None,
        requires_age_min=e.get("requires_age_min"),
        lab_escalation=lab_esc,
    )


@lru_cache(maxsize=1)
def load_rule_index() -> RuleIndex:
    raw = yaml.safe_load(_DATA.read_text()) or []
    rules = [_parse(e) for e in raw] + [_parse(p) for p in read_rule_payloads()]
    return RuleIndex(
        drug_drug=tuple(r for r in rules if r.kind == "drug_drug"),
        drug_disease=tuple(r for r in rules if r.kind == "drug_disease"),
        drug_context=tuple(r for r in rules if r.kind == "drug_context"),
    )
