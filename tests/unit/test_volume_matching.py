"""Volume is a dimension of product identity, not a decoration.

«IOHEXOL 300 mg/1mL 10 mL» and «IOHEXOL 300 mg/1mL 100 mL» share a molecule, a
form, a route and a dose set. Before this, nothing downstream could tell them
apart: 13 tamin formulary rows collapsed onto one volume-less stub whose
announced price was 1,440 rial against references from 333,700 to 25,000,000
(ratios to x17,361).

Two mechanisms, because the data supports two different kinds of evidence:
  * `_volume_guard` — both sides state a volume and they differ. Certain.
  * `_volume_collision_pass` — the catalog row is SILENT (44% of liquid rows
    are), but several formulary rows each naming a different volume landed on
    it. At most one can be right, so none may auto-apply.
"""
from __future__ import annotations

from services.core.drug_catalog import coverage_import as ci
from services.core.drug_catalog import structural_match as sm
from decimal import Decimal

from services.core.drug_catalog.schema import CatalogRecord


def rec(irc, generic, strength, form, *, generic_full=None, price=1000):
    return CatalogRecord(
        irc=irc, name_fa=irc, generic_name=generic, dosage_form=form,
        strength=strength, announced_price=Decimal(price),
        monograph={"generic_full": generic_full} if generic_full else None)


# ── parsing ─────────────────────────────────────────────────────────────────
def test_concentration_denominator_is_not_a_fill_volume():
    """«300 mg/1mL 50 mL» is a 50 mL vial, not a 1 mL one — the /1mL is the
    concentration's denominator. Reading it as a volume would make every
    concentration-labelled row claim a 1 mL fill."""
    assert sm.volume_set("IOHEXOL 300 mg/1mL 50 mL INJECTION") == {50.0}
    assert sm.volume_set("VALPROATE 200 mg/5mL 300MILLILITER SYRUP") == {300.0}
    assert sm.volume_set("LORAZEPAM INJECTION PARENTERAL 2 mg/1mL") == set()


def test_units_fold_and_non_liquids_stay_silent():
    assert sm.volume_set("POVIDONE IODINE 7.5 % 1LITER") == {1000.0}
    assert sm.volume_set("SOLUTION 250MILLILITRE") == {250.0}
    assert sm.volume_set("WARFARIN SODIUM 5 mg TABLET") == set()


def test_silence_is_not_disagreement():
    """Most catalog rows never recorded a volume. Treating that as a conflict
    would throw away thousands of correct matches."""
    assert sm.volumes_agree({50.0}, set()) is True
    assert sm.volumes_agree(set(), {50.0}) is True
    assert sm.volumes_agree({50.0}, {50.0}) is True
    assert sm.volumes_agree({50.0}, {100.0}) is False


def test_record_volume_is_read_from_generic_full():
    """NFI keeps the presentation volume ONLY in monograph.generic_full — never
    in `strength`, which is why nothing downstream could see it."""
    r = rec("A", "iohexol", "300 mg/1mL", "INJECTION",
            generic_full="IOHEXOL INJECTION PARENTERAL 300 mg/1mL 50MILLILITER")
    assert sm.record_volumes(r) == {50.0}
    assert sm.record_volumes(rec("B", "iohexol", "300 mg", "INJECTION")) == set()


# ── the structural lane ─────────────────────────────────────────────────────
def test_structural_match_refuses_a_contradicting_volume():
    catalog = [rec("V100", "iohexol", "300 mg/1mL", "INJECTION",
                   generic_full="IOHEXOL INJECTION PARENTERAL 300 mg/1mL 100MILLILITER")]
    idx = sm.build_index(catalog)
    vocab = sm.build_form_vocab(catalog)

    wrong = sm.parse_name("IOHEXOL 300 mg/1mL 10 mL INJECTION", vocab)
    assert sm.match(wrong, idx)[0] is None            # 10 mL is not the 100 mL vial

    right = sm.parse_name("IOHEXOL 300 mg/1mL 100 mL INJECTION", vocab)
    got, conf, _why = sm.match(right, idx)
    assert got is not None and conf > 0


# ── the cross-path guard ────────────────────────────────────────────────────
def test_guard_demotes_whatever_method_chose_the_record():
    """Of the 23 bad links left in the review queue on 2026-08-01, ZERO came via
    the structural lane — every one arrived by the code join, the ingredient
    lane or a crosswalk-derived guess. The guard must sit where the record is
    finally chosen."""
    target = rec("V10", "cisplatin", "1 mg/1mL", "INJECTION",
                 generic_full="CISPLATIN INJECTION INTRAVENOUS 1 mg/1mL 10MILLILITER")
    conf, method = ci._volume_guard(
        "CISPLATIN 1 mg/1mL 100 mL INJECTION", target, 1.0, "code")
    assert conf <= 0.60 and method == "code+volume_mismatch"

    ok_conf, ok_method = ci._volume_guard(
        "CISPLATIN 1 mg/1mL 10 mL INJECTION", target, 1.0, "code")
    assert (ok_conf, ok_method) == (1.0, "code")


def test_guard_is_silent_when_either_side_says_nothing():
    silent = rec("S", "lorazepam", "2 mg/1mL", "INJECTION")
    assert ci._volume_guard("LORAZEPAM 2 mg/1mL 2 mL INJECTION", silent, 0.9,
                            "ingredient") == (0.9, "ingredient")
    stated = rec("T", "x", "1 mg", "TABLET", generic_full="X TABLET ORAL 1 mg")
    assert ci._volume_guard("X 1 mg TABLET", stated, 0.9,
                            "structural") == (0.9, "structural")


# ── the collision pass ──────────────────────────────────────────────────────
def _link(name, record, conf, method):
    return ci.LinkResult({"drug_name": name}, record, conf, method)


def test_collision_demotes_every_claimant_of_a_volume_silent_record():
    stub = rec("STUB", "iohexol", "300 mg", "INJECTION")       # no generic_full
    out = [_link("IOHEXOL 300 mg/1mL 10 mL INJECTION", stub, 1.0, "code"),
           _link("IOHEXOL 300 mg/1mL 50 mL INJECTION", stub, 1.0, "code"),
           _link("IOHEXOL 300 mg/1mL 100 mL INJECTION", stub, 1.0, "code")]
    assert ci._volume_collision_pass(out) == 3
    assert all(l.confidence <= 0.60 and "volume_collision" in l.method for l in out)


def test_collision_leaves_the_correct_claimant_alone_when_the_catalog_speaks():
    """piperazine 100 mL is right and 60 mL is wrong; the guard already demoted
    the wrong one, so sweeping the group here would punish the correct claimant."""
    stated = rec("P100", "piperazine", "750 mg/5 mL", "SYRUP",
                 generic_full="PIPERAZINE SYRUP ORAL 750 mg/5 mL 100MILLILITER")
    out = [_link("PIPERAZINE 750 mg/5 mL 100 mL SYRUP", stated, 1.0, "code"),
           _link("PIPERAZINE 750 mg/5 mL 60 mL SYRUP", stated, 0.6,
                 "code+volume_mismatch")]
    assert ci._volume_collision_pass(out) == 0
    assert out[0].confidence == 1.0 and out[0].method == "code"


def test_collision_never_touches_an_irc_or_owner_ruling():
    stub = rec("STUB2", "x", "1 mg", "SOLUTION")
    out = [_link("X 1 mg 10 mL SOLUTION", stub, 1.0, "irc"),
           _link("X 1 mg 50 mL SOLUTION", stub, 1.0, "crosswalk")]
    assert ci._volume_collision_pass(out) == 0
    assert all(l.confidence == 1.0 for l in out)


def test_a_single_volume_claimant_is_not_a_collision():
    stub = rec("STUB3", "x", "1 mg", "SOLUTION")
    out = [_link("X 1 mg 10 mL SOLUTION", stub, 1.0, "code"),
           _link("X 1 mg 10 mL SOLUTION SECOND BRAND", stub, 1.0, "code")]
    assert ci._volume_collision_pass(out) == 0


# ── ingredient_key ──────────────────────────────────────────────────────────
def test_ingredient_key_separates_volumes():
    """A 50 mL vial and a 100 mL vial of the same concentration are not
    interchangeable: the insurer prices them separately, so coverage must not
    spread between them, and neither may be offered as the other's cheaper
    alternative."""
    a = rec("A", "iohexol", "300 mg/1mL", "INJECTION",
            generic_full="IOHEXOL INJECTION PARENTERAL 300 mg/1mL 50MILLILITER")
    b = rec("B", "iohexol", "300 mg/1mL", "INJECTION",
            generic_full="IOHEXOL INJECTION PARENTERAL 300 mg/1mL 100MILLILITER")
    assert a.ingredient_key != b.ingredient_key
    assert a.ingredient_key.endswith("|50ml")
    assert b.ingredient_key.endswith("|100ml")


def test_a_volume_less_row_keeps_the_key_it_always_had():
    """29,705 of 39,184 catalog rows state no volume. Appending an empty
    segment would rekey every one of them for nothing — and silently orphan the
    coverage keyed to the old value."""
    from services.core.drug_catalog.schema import ingredient_key as ik
    stub = rec("S", "iohexol", "300 mg", "INJECTION")
    assert stub.ingredient_key == ik("iohexol", "300 mg", "INJECTION")
    assert not stub.ingredient_key.endswith("ml")


def test_same_volume_written_differently_still_groups():
    a = rec("A", "x", "1 mg/1mL", "SOLUTION", generic_full="X SOLUTION 1 mg/1mL 250MILLILITER")
    b = rec("B", "x", "1 mg/1mL", "SOLUTION", generic_full="X SOLUTION 1 mg/1mL 250 mL")
    c = rec("C", "x", "1 mg/1mL", "SOLUTION", generic_full="X SOLUTION 1 mg/1mL 0.25LITER")
    assert a.ingredient_key == b.ingredient_key == c.ingredient_key


def test_the_concentration_denominator_never_becomes_the_group_volume():
    """«2 mg/1mL» with no stated fill must not group as a 1 mL presentation —
    that would split every concentration-labelled product away from its own
    volume-less siblings."""
    r = rec("R", "lorazepam", "2 mg/1mL", "INJECTION",
            generic_full="LORAZEPAM INJECTION PARENTERAL 2 mg/1mL")
    assert not r.ingredient_key.endswith("ml")
