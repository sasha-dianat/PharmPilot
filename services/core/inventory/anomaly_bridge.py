"""The anomaly engine, wired to the recommendation ledger.

`anomaly_detector.py` has been complete and unreachable: 696 lines of isolation
forest, CUSUM drift monitoring and controlled-substance diversion screening,
with no caller. Wiring it to an alert list would have been the easy move and the
wrong one — an unmeasured detector is worse than no detector, because it
produces a stream of alerts that looks like vigilance on any dashboard counting
alerts rather than outcomes.

So it emits recommendations instead. Every alert becomes a row a human decides
on, with the features it was computed from attached, and its acceptance rate is
visible from the first week. If it turns out to be noise, that shows up as
`noisy` on the scoreboard rather than as staff quietly learning to scroll past
it.

Two translations happen here and nowhere else:

  * An alert's `severity` is the detector's opinion of the signal. A
    recommendation's severity is what it means for the pharmacy. A
    controlled-substance diversion signal is never below `high` whatever the
    model scored it, because the cost of missing one is not symmetric with the
    cost of looking into one.

  * `requires_count` becomes a `cycle_count` recommendation rather than an
    `anomaly` one. What the pharmacist has to *do* is count the item; naming it
    an anomaly leaves them with a feeling and no next action.

Deterministic-first is unchanged. Nothing here moves stock or approves
anything: the detector advises, the human decides, and the ledger records both.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass

from . import recommendations as RC

# The detector's severity mapped to what it costs the pharmacy.
SEVERITY_MAP = {"critical": "critical", "high": "high",
                "moderate": "medium", "low": "info"}

# Anything touching controlled stock floors here. The asymmetry is the point:
# investigating a false diversion signal costs an hour, missing a real one
# costs a licence.
CONTROLLED_FLOOR = "high"
_RANK = {"info": 0, "medium": 1, "high": 2, "critical": 3}


def _as_dict(alert) -> dict:
    if is_dataclass(alert) and not isinstance(alert, type):
        return asdict(alert)
    if isinstance(alert, dict):
        return dict(alert)
    return {k: getattr(alert, k) for k in dir(alert) if not k.startswith("_")}


def severity_for(alert: dict) -> str:
    mapped = SEVERITY_MAP.get(str(alert.get("severity") or "").lower(), "medium")
    if alert.get("controlled_substance"):
        if _RANK[mapped] < _RANK[CONTROLLED_FLOOR]:
            return CONTROLLED_FLOOR
    return mapped


def kind_for(alert: dict) -> str:
    """What the pharmacist has to do, not what the model noticed."""
    return "cycle_count" if alert.get("requires_count") else "anomaly"


def to_proposal(alert, *, model_version: str | None = None) -> RC.Proposal:
    """One detector alert as a decidable recommendation.

    The metric, its expected range and the detector that produced it all travel
    in `features`, so a rejection six months from now is still interpretable
    after the model has been retrained.
    """
    a = _as_dict(alert)
    ndc = str(a.get("ndc11") or "")
    kind = kind_for(a)
    sev = severity_for(a)
    lo, hi = (a.get("expected_range") or (None, None))[:2] or (None, None)

    action = ("count this item" if kind == "cycle_count"
              else "investigate this movement pattern")
    return RC.Proposal(
        kind=kind,
        subject=ndc,
        proposal={"action": action,
                  "ndc11": ndc,
                  "alert_type": a.get("alert_type"),
                  "metric_name": a.get("metric_name"),
                  "metric_value": a.get("metric_value")},
        explanation=str(a.get("description") or "anomaly detected"),
        produced_by=f"anomaly:{a.get('alert_type') or 'unknown'}",
        features={"metric_name": a.get("metric_name"),
                  "metric_value": a.get("metric_value"),
                  "expected_low": lo, "expected_high": hi,
                  "detector_severity": a.get("severity"),
                  "controlled_substance": bool(a.get("controlled_substance")),
                  "requires_count": bool(a.get("requires_count"))},
        confidence=a.get("confidence"),
        severity=sev,
        model_version=model_version,
    )


def proposals_from(alerts: list, *, model_version: str | None = None) -> list[RC.Proposal]:
    """Every alert as a recommendation, de-duplicated by fingerprint.

    One run can raise the same item from two detectors — an isolation-forest
    outlier and a CUSUM drift on the same drug are often the same underlying
    event. Emitting both would double-count it in the denominator and make the
    detector look twice as noisy as it is.
    """
    out: dict[str, RC.Proposal] = {}
    for alert in alerts or []:
        p = to_proposal(alert, model_version=model_version)
        if not p.subject:
            continue
        fp = RC.fingerprint(p)
        existing = out.get(fp)
        # Keep the more serious reading when two detectors agree on an item.
        if existing is None or _RANK.get(p.severity or "info", 0) > \
                _RANK.get(existing.severity or "info", 0):
            out[fp] = p
    return list(out.values())
