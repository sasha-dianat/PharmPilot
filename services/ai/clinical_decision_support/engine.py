from __future__ import annotations

from .rules import ALL_RULES
from .schema import AlertDict, CDSContext

MODEL_VERSION = "cds-rules-v1"
SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MODERATE": 2, "LOW": 3, "INFO": 4}


def evaluate(context: CDSContext) -> list[AlertDict]:
    alerts: list[AlertDict] = []
    for rule in ALL_RULES:
        alerts.extend(rule(context))

    deduped: dict[tuple[str, str, str], AlertDict] = {}
    for alert in alerts:
        key = (alert["rule_id"], alert["severity"], alert["title"])
        deduped.setdefault(key, alert)

    return sorted(
        deduped.values(),
        key=lambda alert: (SEVERITY_ORDER.get(alert["severity"], 99), alert["rule_id"]),
    )
