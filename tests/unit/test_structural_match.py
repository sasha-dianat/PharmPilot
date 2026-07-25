"""Structural matching on the shared IRC/FDA controlled vocabulary."""
from decimal import Decimal

from services.core.drug_catalog import structural_match as sm
from services.core.drug_catalog.coverage_import import link_rows
from services.core.drug_catalog.schema import CatalogRecord


def _rec(irc, generic, strength, form, name_fa="دارو"):
    return CatalogRecord(irc=irc, name_fa=name_fa, generic_name=generic,
                         dosage_form=form, strength=strength,
                         announced_price=Decimal("1000"))


CAT = [
    _rec("C100", "ciclosporin", "100 mg", "CAPSULE, LIQUID FILLED"),
    _rec("C25", "ciclosporin", "25 mg", "CAPSULE, LIQUID FILLED"),
    _rec("C_EYE", "ciclosporin", "0.05 %", "SOLUTION, DROPS"),
    _rec("R37", "risperidone", "37.5 mg", "INJECTION, POWDER, FOR SUSPENSION, EXTENDED RELEASE"),
    _rec("R1", "risperidone", "1 mg", "TABLET"),
    _rec("FUR", "furosemide", "10 mg/1mL", "INJECTION"),
    _rec("SOF", "sofosbuvir", "400 mg", "TABLET"),
    _rec("COMBO", "daclatasvir / sofosbuvir", "60 mg/400 mg", "TABLET"),
]


def test_dose_set_folds_mass_and_keeps_pct_and_iu_separate():
    assert sm.dose_set("50 microgram") == sm.dose_set("0.05 mg")      # mcg → mg
    assert sm.dose_set("1 g") == {1000.0}
    assert sm.dose_set("0.1 %") == {("pct", 0.1)}
    assert sm.dose_set("2000 [iU]") == {("iu", 2000.0)}
    # a percentage is NOT the same number as a mass, nor is an IU
    assert not sm.doses_agree(sm.dose_set("0.1 %"), sm.dose_set("0.1 mg"))
    assert not sm.doses_agree(sm.dose_set("100 [iU]"), sm.dose_set("100 mg"))
    assert sm.doses_agree(sm.dose_set("100 mg"), sm.dose_set("0.1 g"))


def test_parse_name_splits_the_controlled_vocabulary():
    vocab = sm.build_form_vocab(CAT)
    p = sm.parse_name(
        "CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL 100MG CAPSULE", vocab)
    # the INN spelling is canonicalized to the USAN name NFI actually stores
    assert p["generics"] == ["cyclosporine"]
    assert p["form"] == "CAPSULE, LIQUID FILLED"        # most specific form wins
    assert p["route"] == "ORAL"
    assert 100.0 in p["doses"] and not p["combo"]
    # salt notes never leak into the generic head
    p2 = sm.parse_name("TRIMIPRAMINE (AS MALEATE) 100 mg TABLET ORAL", vocab + ["TABLET"])
    assert p2["generics"] == ["trimipramine"]
    # combination products are flagged and both components extracted
    p3 = sm.parse_name("DACLATASVIR / SOFOSBUVIR 60 mg/400 mg TABLET ORAL", vocab + ["TABLET"])
    assert p3["combo"] and set(p3["generics"]) == {"daclatasvir", "sofosbuvir"}


def test_exact_form_and_dose_pick_the_right_variant():
    idx, vocab = sm.build_index(CAT), sm.build_form_vocab(CAT)
    for name, want in (
        ("CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL", "C100"),
        ("CICLOSPORIN 25 mg CAPSULE, LIQUID FILLED ORAL", "C25"),
        ("CICLOSPORIN 0.05% 10 mL SOLUTION, DROPS OPHTHALMIC", "C_EYE"),
        ("RISPERIDONE 37.5 mg INJECTION, POWDER, FOR SUSPENSION, EXTENDED RELEASE PARENTERAL", "R37"),
        ("RISPERIDONE 1 mg TABLET ORAL", "R1"),
        ("FUROSEMIDE 10 mg/1mL 25 mL INJECTION INTRAVENOUS", "FUR"),
    ):
        rec, conf, _why = sm.match(sm.parse_name(name, vocab), idx)
        assert rec is not None and rec.irc == want, f"{name} → {rec and rec.irc}"
        assert conf >= sm.CONF_EXACT_DOSE - 0.001


def test_dose_conflict_refuses_rather_than_guessing():
    idx, vocab = sm.build_index(CAT), sm.build_form_vocab(CAT)
    # 500 mg ciclosporin capsule does not exist → no structural claim
    rec, conf, _ = sm.match(sm.parse_name(
        "CICLOSPORIN 500 mg CAPSULE, LIQUID FILLED ORAL", vocab), idx)
    assert rec is None and conf == 0.0


def test_combination_never_silently_lands_on_the_mono_product():
    idx, vocab = sm.build_index(CAT), sm.build_form_vocab(CAT)
    rec, conf, why = sm.match(sm.parse_name(
        "DACLATASVIR / SOFOSBUVIR 60 mg/400 mg TABLET ORAL", vocab), idx)
    assert rec is not None and rec.irc == "COMBO"      # the combination row, not SOF
    assert conf <= sm.CONF_COMBO_CAP and "combination" in why


def test_form_family_fallback_scores_below_exact():
    cat = [_rec("INJ", "amikacin", "500 mg", "INJECTION, SOLUTION")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    # row states only the family "INJECTION" — still reaches it, lower confidence
    rec, conf, _ = sm.match(sm.parse_name("AMIKACIN 500 mg INJECTION PARENTERAL",
                                          vocab + ["INJECTION"]), idx)
    assert rec is not None and rec.irc == "INJ"
    assert conf == sm.CONF_FAMILY_DOSE < sm.CONF_EXACT_DOSE


def test_link_rows_uses_structural_and_code_join_stays_review_first():
    row = {"drug_name": "CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL"}
    link = link_rows([row], CAT)[0]
    assert link.matched and link.record.irc == "C100" and link.method == "structural"
    assert link.confidence >= 0.75                     # own name → may auto-apply

    # salamat's truncated name + tamin's rich name for the same national code:
    # resolves to the right variant but is held for owner confirmation
    row2 = {"drug_name": "CICLOSPORIN", "generic_code": "00287"}
    reg = {"00287": {"name": "CICLOSPORIN 100 mg CAPSULE, LIQUID FILLED ORAL"}}
    link2 = link_rows([row2], CAT, code_registry=reg, insurer="salamat")[0]
    assert link2.matched and link2.record.irc == "C100"
    assert link2.method == "structural+code"
    assert link2.confidence <= 0.74                    # never auto-applies

    # structural=False restores the pure fuzzy path
    assert link_rows([row], CAT, structural=False)[0].method != "structural"


def test_salt_tolerant_lane_resolves_base_name_but_refuses_ambiguity():
    """NFI keeps the salt in the generic ("amlodipine besilate" — normalize()
    strips maleate/hydrochloride but not besilate) while a formulary prints the
    base. Widening is allowed only when exactly ONE NFI ingredient extends the
    row's name."""
    cat = [
        _rec("AML", "amlodipine besilate", "5 mg", "TABLET"),
        _rec("GLA", "insulin glargine", "100 iu/1mL", "INJECTION"),
        _rec("ASP", "insulin aspart", "100 iu/1mL", "INJECTION"),
    ]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, why = sm.match(sm.parse_name("AMLODIPINE 5 mg TABLET ORAL", vocab), idx)
    assert rec is not None and rec.irc == "AML"
    assert conf == sm.CONF_SALT < sm.CONF_EXACT_DOSE and "salt-tolerant" in why
    # ambiguous: two insulins extend "INSULIN" → refuse rather than pick one
    rec2, conf2, _ = sm.match(sm.parse_name("INSULIN 100 iu/1mL INJECTION PARENTERAL", vocab), idx)
    assert rec2 is None and conf2 == 0.0


def test_inn_synonyms_bridge_to_the_usan_name_nfi_uses():
    """Iranian formularies use INN spellings; NFI stores USAN ones."""
    from services.core.drug_catalog.schema import canonical_ingredient
    for inn, usan in (("ciclosporin", "cyclosporine"), ("aciclovir", "acyclovir"),
                      ("salbutamol", "albuterol"), ("adrenaline", "epinephrine")):
        assert canonical_ingredient(inn) == usan
    cat = [_rec("CYC", "cyclosporine", "100 mg", "CAPSULE")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, _c, _w = sm.match(sm.parse_name("CICLOSPORIN 100 mg CAPSULE ORAL", vocab), idx)
    assert rec is not None and rec.irc == "CYC"


def test_ingredient_agreement_ignores_shared_filler_words():
    """Shared salt/qualifier words inflate string similarity and conceal a
    different molecule — the residual mismatch class after the similarity floor.
    Agreement is decided on what is LEFT after removing shared tokens."""
    from services.core.drug_catalog.schema import canonical_ingredient as ci
    from services.ai.clinical_decision_support.normalizer import normalize
    def c(x): return ci(normalize(x) or x.lower())

    # different drugs sharing a filler word (raw similarity 0.788 / 0.889)
    assert not sm.ingredient_agrees(c("calcium folinate"), c("calcium gluconate"))
    assert not sm.ingredient_agrees(c("trientine dihydrochloride"),
                                    c("trimetazidine dihydrochloride"))
    # different drugs with no shared token but a common stem
    for a, b in (("cephalexin", "cefazolin"), ("amino acid", "amikacin"),
                 ("prostaglandin e2", "protamine sulfate"),
                 ("plastic container", "placenta")):
        assert not sm.ingredient_agrees(c(a), c(b)), f"{a} vs {b}"
    # same drug: salt/acid pair, spelling variant, filler-only difference,
    # concatenated name, token reorder, synonym
    for a, b in (("alendronate", "alendronic acid"), ("cefalexin", "cephalexin"),
                 ("dextrose", "anhydrous dextrose"),
                 ("metformin hydrochloride", "metformin"),
                 ("potassiumchlorideconcentrated", "potassium chloride"),
                 ("valproate sodium", "sodium valproate"),
                 ("ursodeoxycholic acid", "ursodiol")):
        assert sm.ingredient_agrees(c(a), c(b)), f"{a} vs {b}"
    assert not sm.ingredient_agrees("", "metformin")


def test_link_rows_refuses_the_shared_filler_class():
    cat = [_rec("CG", "calcium gluconate", "100 mg/1mL", "INJECTION"),
           _rec("CZ", "cefazolin", "1 g", "INJECTION, POWDER, FOR SOLUTION")]
    for name in ("CALCIUM FOLINATE 100 mg/1mL INJECTION PARENTERAL",
                 "CEPHALEXIN 1 g INJECTION, POWDER, FOR SOLUTION PARENTERAL"):
        link = link_rows([{"drug_name": name}], cat)[0]
        assert not link.matched, f"{name} → {link.record and link.record.generic_name}"
