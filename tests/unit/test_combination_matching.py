"""A fixed-dose combination must describe itself honestly.

NFI states only the FIRST ingredient in «نام ژنریک» and writes the rest into the
dose — `generic_name='empagliflozin'`, `strength='10 mg/LINAGLIPTIN 5 mg'`. The
record therefore advertised one ingredient while being a two-ingredient product,
and `match()`'s ingredient-set guard (right to demand agreement in both
directions) refused candidates that agreed on molecule set, form AND dose. 164
of the 370 unmatched formulary rows on 2026-08-03 were combinations of this
shape.

The same understatement caused the opposite, worse error: «PIPERACILLIN 4 g»
matched a piperacillin/tazobactam record at 0.93 — above the auto-apply line —
because the record looked like a mono product. A patient entitled to
piperacillin alone would have been quoted Tazocin.
"""
from __future__ import annotations

from decimal import Decimal

from services.core.drug_catalog import structural_match as sm
from services.core.drug_catalog.schema import CatalogRecord


def rec(irc, generic, strength, form, *, generic_full=None):
    return CatalogRecord(
        irc=irc, name_fa=irc, generic_name=generic, dosage_form=form,
        strength=strength, announced_price=Decimal(1000),
        monograph={"generic_full": generic_full} if generic_full else None)


CATALOG = [
    rec("E10", "empagliflozin", "10 mg/LINAGLIPTIN 5 mg", "TABLET",
        generic_full="EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 10 mg/5 mg"),
    rec("E25", "empagliflozin", "25 mg/LINAGLIPTIN 5 mg", "TABLET",
        generic_full="EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 25 mg/5 mg"),
    rec("PT", "piperacillin", "4 g/TAZOBACTAM 0.5 g", "INJECTION, POWDER, FOR SOLUTION",
        generic_full="PIPERACILLIN (AS SODIUM) / TAZOBACTAM (AS SODIUM) "
                     "INJECTION, POWDER, FOR SOLUTION PARENTERAL 4 g/0.5 g"),
    rec("P", "piperacillin", "4 g", "INJECTION, POWDER, FOR SOLUTION",
        generic_full="PIPERACILLIN INJECTION, POWDER, FOR SOLUTION PARENTERAL 4 g"),
    rec("L200", "levodopa", "200 mg/CARBIDOPA 50 mg/ENTACAPONE 200 mg", "TABLET",
        generic_full="LEVODOPA / CARBIDOPA / ENTACAPONE TABLET ORAL 200 mg/50 mg/200 mg"),
    rec("EMPA", "empagliflozin", "10 mg", "TABLET",
        generic_full="EMPAGLIFLOZIN TABLET ORAL 10 mg"),
]
IDX = sm.build_index(CATALOG)
VOCAB = sm.build_form_vocab(CATALOG)


def hit(name):
    got, conf, _why = sm.match(sm.parse_name(name, VOCAB), IDX)
    return (got.irc if got else None), conf


# ── the record's own account of itself ──────────────────────────────────────
def test_components_come_from_generic_full_without_needing_a_mono_product():
    """No mono tazobactam product exists to teach the word, so a vocabulary
    gated on generic_name alone can never learn it. generic_full names every
    ingredient ahead of the form and needs no vocabulary."""
    known = {c for r in CATALOG for c in sm.components(r.generic_name or "")}
    assert "tazobactam" not in known
    assert sm.record_components(CATALOG[2], known) == ["piperacillin", "tazobactam"]


def test_components_are_read_out_of_the_strength_when_the_vocabulary_knows_them():
    known = {"empagliflozin", "linagliptin"}
    assert sm.embedded_components("10 mg/LINAGLIPTIN 5 mg", known) == ["linagliptin"]


def test_a_unit_word_is_never_promoted_to_an_ingredient():
    known = {"salmeterol", "fluticasone"}
    assert sm.embedded_components("25 ug/1{dose}", known) == []
    assert sm.embedded_components("100 [iU]/1mL", known) == []


def test_a_salt_qualified_name_falls_back_to_the_base_ingredient():
    assert sm.embedded_components("25 ug/FLUTICASONE PROPIONATE 250 ug",
                                  {"fluticasone"}) == ["fluticasone"]


# ── the whole dose vector ───────────────────────────────────────────────────
def test_sharing_one_component_dose_is_not_agreement():
    """10 mg/5 mg and 25 mg/5 mg share the 5. Matching on that offers a 25 mg
    tablet against a 10 mg entitlement."""
    assert sm.doses_agree({10.0, 5.0}, {25.0, 5.0}) is True        # the old test
    assert sm.doses_agree_all({10.0, 5.0}, {25.0, 5.0}) is False   # the right one
    assert sm.doses_agree_all({10.0, 5.0}, {10.0, 5.0}) is True


def test_silence_still_is_not_disagreement():
    assert sm.doses_agree_all(set(), {10.0}) is True
    assert sm.doses_agree_all({10.0}, set()) is True


def test_the_combination_lands_on_its_own_strength():
    assert hit("EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 10 mg/5 mg")[0] == "E10"
    assert hit("EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 25 mg/5 mg")[0] == "E25"


def test_a_strength_we_do_not_hold_matches_nothing():
    """Stalevo 125 (levodopa 125 / carbidopa 31.25 / entacapone 200) is a real
    product absent from our catalog. The honest answer is no match — not the
    200 mg tablet because it shares the entacapone 200."""
    assert hit("LEVODOPA / CARBIDOPA / ENTACAPONE TABLET ORAL "
               "125 mg/31.25 mg/200 mg")[0] is None


def test_three_ingredient_combination_matches_on_all_three():
    assert hit("LEVODOPA / CARBIDOPA / ENTACAPONE TABLET ORAL "
               "200 mg/50 mg/200 mg")[0] == "L200"


# ── the crossover this closes ───────────────────────────────────────────────
def test_a_mono_row_never_lands_on_a_combination():
    """Before the fix this returned the piperacillin/tazobactam record at 0.93 —
    above the auto-apply line — because the record understated itself."""
    irc, conf = hit("PIPERACILLIN INJECTION, POWDER, FOR SOLUTION PARENTERAL 4 g")
    assert irc == "P" and conf >= 0.90

    irc, _ = hit("EMPAGLIFLOZIN TABLET ORAL 10 mg")
    assert irc == "EMPA"


def test_a_combination_row_never_lands_on_the_mono_product():
    got, _c, _w = sm.match(
        sm.parse_name("EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 99 mg/99 mg", VOCAB), IDX)
    assert got is None


def test_combinations_stay_below_the_auto_apply_line():
    """A combination match is still evidence to be confirmed, not applied: the
    ingredient sets agree but the insurer's name is terser than the label."""
    assert hit("EMPAGLIFLOZIN / LINAGLIPTIN TABLET ORAL 10 mg/5 mg")[1] <= 0.70
