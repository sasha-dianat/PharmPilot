from services.ai.clinical_decision_support.interaction.severity import (
    InteractionSeverity as S, normalize_severity, step, magnitude_to_severity,
)


def test_ordering_and_rank():
    assert S.CONTRAINDICATED.rank > S.MAJOR.rank > S.MODERATE.rank > S.MINOR.rank


def test_normalize_legacy_and_ddinter():
    assert normalize_severity("CRITICAL") is S.MAJOR          # not flagged contraindicated
    assert normalize_severity("CRITICAL", contraindicated=True) is S.CONTRAINDICATED
    assert normalize_severity("HIGH") is S.MAJOR
    assert normalize_severity("MODERATE") is S.MODERATE
    assert normalize_severity("INFO") is S.MINOR
    assert normalize_severity("Major") is S.MAJOR             # DDInter / curated already-canonical


def test_step_up_and_down_caps():
    assert step(S.MAJOR, +1) is S.CONTRAINDICATED
    assert step(S.CONTRAINDICATED, +1) is S.CONTRAINDICATED   # capped
    assert step(S.MINOR, -1) is S.MINOR                       # floored
    assert step(S.MAJOR, -1) is S.MODERATE


def test_magnitude_matrix():
    assert magnitude_to_severity("strong", "high") is S.MAJOR
    assert magnitude_to_severity("strong", "low") is S.MODERATE
    assert magnitude_to_severity("moderate", "high") is S.MODERATE
    assert magnitude_to_severity("weak", "low") is S.MINOR
