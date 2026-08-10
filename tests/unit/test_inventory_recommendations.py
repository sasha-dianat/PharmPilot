"""The recommendation ledger — what makes an advisory system falsifiable.

The failure these guard against is subtle and common: an advisory system that
reports how much advice it produced instead of how much was any good. Every
assertion here is about keeping the denominator honest.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.core.inventory import recommendations as RC

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def prop(kind="reorder", subject="N1", **proposal) -> RC.Proposal:
    body = {"action": "order", "ndc11": subject}
    body.update(proposal)
    return RC.Proposal(kind=kind, subject=subject, proposal=body,
                       explanation="because the shelf says so",
                       produced_by="procurement_v1")


def row(status="open", *, kind="reorder", note=None, created=None, fp="f1",
        rid="r1", conf=None) -> dict:
    return {"id": rid, "kind": kind, "status": status, "fingerprint": fp,
            "decision_note": note, "created_at": created or NOW,
            "decided_at": NOW if status in ("accepted", "rejected") else None,
            "ndc11": "N1", "confidence": conf}


# ── identity, not novelty ─────────────────────────────────────────────────
def test_the_same_advice_has_the_same_fingerprint_across_runs():
    assert RC.fingerprint(prop()) == RC.fingerprint(prop())


def test_a_changed_quantity_is_the_same_recommendation():
    """"Reorder metformin" is one recommendation whether today's arithmetic
    says 340 units or 360. Treating the number as identity would reopen it
    every night and make the acceptance rate a measure of the scheduler."""
    assert RC.fingerprint(prop(recommended_order_qty=340)) == \
        RC.fingerprint(prop(recommended_order_qty=360))


def test_a_different_subject_is_a_different_recommendation():
    assert RC.fingerprint(prop(subject="N1")) != RC.fingerprint(prop(subject="N2"))


def test_a_different_kind_is_a_different_recommendation():
    assert RC.fingerprint(prop(kind="reorder")) != \
        RC.fingerprint(prop(kind="expiry_risk"))


def test_a_different_action_is_a_different_recommendation():
    assert RC.fingerprint(prop(action="order")) != RC.fingerprint(prop(action="return"))


def test_an_unknown_kind_is_refused():
    with pytest.raises(RC.RecommendationError):
        RC.fingerprint(RC.Proposal(kind="vibes", subject="N1", proposal={},
                                   explanation="x", produced_by="y"))


# ── decisions ─────────────────────────────────────────────────────────────
def test_accepting_needs_no_note():
    out = RC.decide(row(), status="accepted", note=None, decided_by="u1", now=NOW)
    assert out["status"] == "accepted"
    assert out["decision_note"] is None


def test_rejecting_without_a_reason_is_refused():
    """The reason is the labelled negative — produced by an expert at the moment
    they had full context, and the one thing not recoverable afterwards."""
    with pytest.raises(RC.RecommendationError) as e:
        RC.decide(row(), status="rejected", note="  ", decided_by="u1")
    assert "training signal" in str(e.value)


def test_rejecting_with_a_reason_records_it():
    out = RC.decide(row(), status="rejected", note="supplier discontinued it",
                    decided_by="u1", now=NOW)
    assert out["decision_note"] == "supplier discontinued it"


def test_a_decision_must_name_who_made_it():
    with pytest.raises(RC.RecommendationError):
        RC.decide(row(), status="accepted", note=None, decided_by=None)


def test_an_already_decided_recommendation_cannot_be_redecided():
    """Overwriting the original label destroys the only record of what the
    expert thought at the time."""
    with pytest.raises(RC.RecommendationError) as e:
        RC.decide(row("accepted"), status="rejected", note="changed my mind",
                  decided_by="u1")
    assert "overwrite the original label" in str(e.value)


def test_superseding_is_not_a_decision_a_person_can_make():
    with pytest.raises(RC.RecommendationError):
        RC.decide(row(), status="superseded", note=None, decided_by="u1")


# ── closing out what the run no longer proposes ───────────────────────────
def test_advice_the_run_no_longer_makes_is_superseded_not_rejected():
    """Nobody disagreed; the conditions moved. Counting it as a rejection would
    libel the model and depress every precision figure."""
    out = RC.supersede([row(fp="gone"), row(fp="kept", rid="r2")], {"kept"})
    assert [o["id"] for o in out] == ["r1"]
    assert out[0]["status"] == "superseded"


def test_already_decided_rows_are_not_superseded():
    assert RC.supersede([row("accepted", fp="gone")], set()) == []


def test_stale_advice_expires():
    old = row(created=NOW - timedelta(days=30))
    assert [o["status"] for o in RC.expired([old], now=NOW)] == ["expired"]


def test_fresh_advice_does_not_expire():
    assert RC.expired([row(created=NOW - timedelta(days=1))], now=NOW) == []


def test_different_kinds_go_stale_at_different_speeds():
    """A reorder computed three weeks ago is about stock that has moved; a
    formulary binding is not."""
    age = NOW - timedelta(days=20)
    assert len(RC.expired([row(kind="reorder", created=age)], now=NOW)) == 1
    assert RC.expired([row(kind="formulary_binding", created=age)], now=NOW) == []


# ── the scoreboard ────────────────────────────────────────────────────────
def _many(n, status, kind="anomaly"):
    return [row(status, kind=kind, rid=f"{kind}-{status}-{i}") for i in range(n)]


def test_too_few_decisions_is_unmeasured_rather_than_a_precision_figure():
    s = RC.scoreboard(_many(3, "accepted"))[0]
    assert s.verdict == "unmeasured"
    assert "fraction of a small number" in s.explanation


def test_lots_produced_and_none_decided_is_named_ignored():
    """The most important state to name: it looks like a working detector on
    every dashboard that counts alerts rather than outcomes."""
    s = RC.scoreboard(_many(40, "open"))[0]
    assert s.verdict == "ignored"
    assert s.produced == 40 and s.decided == 0


def test_mostly_rejected_is_noisy():
    s = RC.scoreboard(_many(2, "accepted") + _many(18, "rejected"))[0]
    assert s.verdict == "noisy"
    assert s.acceptance_rate == 0.1


def test_mostly_accepted_is_trusted():
    s = RC.scoreboard(_many(18, "accepted") + _many(2, "rejected"))[0]
    assert s.verdict == "trusted"
    assert s.acceptance_rate == 0.9


def test_superseded_rows_do_not_count_against_the_model():
    """A denominator that counts them measures staffing levels, not quality."""
    with_sup = RC.scoreboard(_many(9, "accepted") + _many(1, "rejected")
                             + _many(50, "superseded"))[0]
    assert with_sup.decided == 10
    assert with_sup.acceptance_rate == 0.9
    assert with_sup.verdict == "trusted"


def test_each_kind_is_scored_separately():
    rows = _many(12, "accepted", kind="reorder") + _many(12, "rejected", kind="anomaly")
    got = {s.kind: s.verdict for s in RC.scoreboard(rows)}
    assert got == {"reorder": "trusted", "anomaly": "noisy"}


def test_an_empty_ledger_scores_nothing_rather_than_dividing_by_zero():
    assert RC.scoreboard([]) == []


# ── the training set ──────────────────────────────────────────────────────
def test_rejection_reasons_are_retrievable_as_the_training_set():
    rows = [row("rejected", note="patient moved away", rid="a"),
            row("accepted", rid="b"),
            row("rejected", note="already on order", rid="c")]
    notes = [r["note"] for r in RC.rejection_reasons(rows)]
    assert sorted(notes) == ["already on order", "patient moved away"]


def test_rejection_reasons_can_be_read_per_kind():
    rows = [row("rejected", kind="anomaly", note="known promo", rid="a"),
            row("rejected", kind="reorder", note="discontinued", rid="b")]
    assert [r["note"] for r in RC.rejection_reasons(rows, kind="anomaly")] == \
        ["known promo"]


def test_agreement_is_recorded_separately_from_correctness():
    """`accepted` says a pharmacist agreed. `outcome` says what happened.
    Reporting the first as accuracy is how an advisory system flatters itself."""
    s = RC.scoreboard(_many(20, "accepted"))[0]
    assert s.verdict == "trusted"
    assert not hasattr(s, "accuracy")
    assert "accepted" in s.explanation


# ── a decision buys quiet ─────────────────────────────────────────────────
def _decided(status, kind="cycle_count", days_ago=0, fp="f1"):
    r = row(status, kind=kind, fp=fp, note="because" if status == "rejected" else None)
    r["decided_at"] = NOW - timedelta(days=days_ago)
    return r


def test_advice_decided_today_is_not_raised_again_tomorrow():
    """The dismissed alert that comes back is how a queue teaches people to
    ignore it — and it re-inflates the denominator."""
    assert RC.suppressed([_decided("accepted")], now=NOW) == {"f1"}


def test_a_rejection_buys_the_same_quiet_as_an_acceptance():
    """Both are a human saying "I have dealt with this". Re-asking either
    tomorrow is the same discourtesy and the same measurement error."""
    assert RC.suppressed([_decided("rejected")], now=NOW) == {"f1"}


def test_the_quiet_runs_out_so_a_condition_that_persists_is_raised_again():
    assert RC.suppressed([_decided("accepted", days_ago=60)], now=NOW) == set()


def test_different_kinds_stay_quiet_for_different_lengths():
    """A reorder is about stock that moves weekly; a formulary binding is not."""
    old = 10
    assert RC.suppressed([_decided("accepted", kind="reorder", days_ago=old)],
                         now=NOW) == set()
    assert RC.suppressed([_decided("accepted", kind="formulary_binding",
                                   days_ago=old)], now=NOW) == {"f1"}


def test_an_open_recommendation_does_not_suppress_anything():
    assert RC.suppressed([row("open")], now=NOW) == set()


def test_a_superseded_recommendation_does_not_buy_quiet():
    """Nobody decided it, so nobody has dealt with it."""
    r = _decided("superseded")
    assert RC.suppressed([r], now=NOW) == set()
