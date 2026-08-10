"""The anomaly engine as decidable advice rather than an alert stream.

An unmeasured detector is worse than none: it produces a stream that looks like
vigilance on any dashboard counting alerts instead of outcomes. These tests pin
the translation that makes it measurable, and the two asymmetries that must not
be smoothed away.
"""
from __future__ import annotations

from services.core.inventory import anomaly_bridge as AB
from services.core.inventory import recommendations as RC


def alert(**kw) -> dict:
    base = {"ndc11": "N1", "alert_type": "isolation_forest", "severity": "moderate",
            "confidence": 0.8, "description": "on-hand well outside its usual range",
            "metric_name": "days_supply", "metric_value": 3.0,
            "expected_range": (10.0, 40.0), "requires_count": False,
            "controlled_substance": False}
    base.update(kw)
    return base


def test_an_alert_becomes_a_decidable_recommendation():
    p = AB.to_proposal(alert())
    assert p.kind == "anomaly"
    assert p.subject == "N1"
    assert p.confidence == 0.8
    assert RC.fingerprint(p)


def test_the_features_travel_with_it_so_a_rejection_stays_interpretable():
    """Six months on, after a retrain, a rejected row must still say what it was
    rejecting."""
    f = AB.to_proposal(alert()).features
    assert f["metric_name"] == "days_supply"
    assert (f["expected_low"], f["expected_high"]) == (10.0, 40.0)
    assert f["detector_severity"] == "moderate"


def test_an_alert_needing_a_count_becomes_a_count_not_an_anomaly():
    """Naming it an anomaly leaves the pharmacist with a feeling and no next
    action. What they have to do is count the item."""
    p = AB.to_proposal(alert(requires_count=True))
    assert p.kind == "cycle_count"
    assert p.proposal["action"] == "count this item"


def test_a_controlled_substance_signal_never_falls_below_high():
    """Investigating a false diversion signal costs an hour. Missing a real one
    costs a licence. The severities are not symmetric and must not be averaged."""
    p = AB.to_proposal(alert(severity="low", controlled_substance=True))
    assert p.severity == "high"


def test_a_controlled_critical_signal_is_not_downgraded_by_the_floor():
    p = AB.to_proposal(alert(severity="critical", controlled_substance=True))
    assert p.severity == "critical"


def test_detector_severity_maps_to_what_it_costs_the_pharmacy():
    assert AB.to_proposal(alert(severity="low")).severity == "info"
    assert AB.to_proposal(alert(severity="critical")).severity == "critical"


def test_an_unknown_detector_severity_does_not_crash_or_silently_vanish():
    assert AB.to_proposal(alert(severity="spicy")).severity == "medium"


def test_two_detectors_agreeing_on_one_item_are_one_recommendation():
    """An isolation-forest outlier and a CUSUM drift on the same drug are
    usually the same event. Emitting both doubles the denominator and makes the
    detector look twice as noisy as it is."""
    out = AB.proposals_from([alert(alert_type="isolation_forest"),
                             alert(alert_type="isolation_forest")])
    assert len(out) == 1


def test_when_two_readings_collide_the_more_serious_one_survives():
    out = AB.proposals_from([alert(severity="low"), alert(severity="critical")])
    assert len(out) == 1 and out[0].severity == "critical"


def test_an_alert_naming_no_drug_is_dropped_rather_than_filed_against_nothing():
    assert AB.proposals_from([alert(ndc11=None)]) == []


def test_distinct_items_stay_distinct():
    assert len(AB.proposals_from([alert(ndc11="N1"), alert(ndc11="N2")])) == 2


def test_a_dataclass_alert_is_accepted_as_well_as_a_dict():
    from services.ai.inventory_intelligence.anomaly_detector import StockAnomalyAlert
    a = StockAnomalyAlert(
        ndc11="N9", pharmacy_id="p", alert_type="cusum_high", severity="high",
        confidence=0.9, description="sustained upward drift",
        metric_name="on_hand", metric_value=500.0, expected_range=(10.0, 100.0))
    p = AB.to_proposal(a)
    assert p.subject == "N9" and p.severity == "high"


def test_the_bridge_never_moves_stock_or_approves_anything():
    """Deterministic-first: the detector advises, the human decides.

    Asserted on what the module imports and calls rather than on words in its
    prose — the first version of this test grepped for "approve" and matched the
    docstring promising not to.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(AB))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)

    # Nothing that could write stock, post a movement, or decide an approval.
    assert not any(m and ("ledger" in m or "sqlalchemy" in m or "dispense" in m
                          or "approvals" in m) for m in imported), imported

    called = {n.func.attr for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not (called & {"execute", "commit", "add", "flush", "decide"})
