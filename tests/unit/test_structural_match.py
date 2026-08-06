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
    """NFI keeps the salt in the generic while a formulary prints the base.
    Widening is allowed only when exactly ONE NFI ingredient extends the row's
    name — «INSULIN» must never silently become one particular insulin.

    The lane is exercised here with «dipropionate», which normalize() does NOT
    strip and must not: «propionate» and «furoate» pick out different fluticasone
    products. `besilate` used to serve this role and no longer does — it was
    added to the salt vocabulary, so amlodipine now resolves in the exact lane
    (see the test below), which is the better answer.
    """
    cat = [
        _rec("BEC", "beclomethasone dipropionate", "250 ug", "AEROSOL, METERED"),
        _rec("GLA", "insulin glargine", "100 iu/1mL", "INJECTION"),
        _rec("ASP", "insulin aspart", "100 iu/1mL", "INJECTION"),
    ]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, why = sm.match(
        sm.parse_name("BECLOMETHASONE 250 ug AEROSOL, METERED RESPIRATORY", vocab), idx)
    assert rec is not None and rec.irc == "BEC"
    assert conf == sm.CONF_SALT < sm.CONF_EXACT_DOSE and "salt-tolerant" in why
    # ambiguous: two insulins extend "INSULIN" → refuse rather than pick one
    rec2, conf2, _ = sm.match(sm.parse_name("INSULIN 100 iu/1mL INJECTION PARENTERAL", vocab), idx)
    assert rec2 is None and conf2 == 0.0


def test_a_stripped_salt_resolves_in_the_exact_lane_not_the_tolerant_one():
    """«amlodipine besilate» IS amlodipine, so once besilate joined the salt
    vocabulary the row lands at full confidence rather than the widened 0.76."""
    cat = [_rec("AML", "amlodipine besilate", "5 mg", "TABLET")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, _why = sm.match(sm.parse_name("AMLODIPINE 5 mg TABLET ORAL", vocab), idx)
    assert rec is not None and rec.irc == "AML" and conf == sm.CONF_EXACT_DOSE


def test_different_salts_of_one_molecule_are_different_products():
    """Diclofenac potassium is Cataflam — rapid onset, acute pain and migraine.
    Diclofenac sodium is Voltaren — enteric-coated and sustained-release, for
    chronic inflammatory disease. In the Iranian catalog they are 24 products at
    23,000–39,000 rial and 222 at 3,300–1,350,000. They are not interchangeable
    and must never match each other's formulary row.

    normalize() folds both to «diclofenac», because it exists for the interaction
    engine and answers with the drug CLASS. That is right for a DUR lookup and
    wrong for identity, so the matcher keeps the salt whenever the catalog sells
    more than one of them.
    """
    cat = [
        _rec("DNA", "diclofenac sodium", "100 mg", "TABLET, DELAYED RELEASE"),
        _rec("DK", "diclofenac potassium", "50 mg", "TABLET"),
        _rec("MS", "metoprolol succinate", "47.5 mg", "TABLET, EXTENDED RELEASE"),
        _rec("MT", "metoprolol tartrate", "50 mg", "TABLET"),
    ]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    hit = lambda n: (lambda r: r[0].irc if r[0] else None)(
        sm.match(sm.parse_name(n, vocab), idx))

    assert "diclofenac" in sm.identity_bearing_bases(cat)
    assert hit("DICLOFENAC POTASSIUM TABLET ORAL 50 mg") == "DK"
    assert hit("DICLOFENAC SODIUM TABLET, DELAYED RELEASE ORAL 100 mg") == "DNA"
    assert hit("METOPROLOL SUCCINATE TABLET, EXTENDED RELEASE ORAL 47.5 mg") == "MS"
    assert hit("METOPROLOL TARTRATE TABLET ORAL 50 mg") == "MT"


def test_a_row_naming_no_salt_reaches_none_of_them():
    """The salt-tolerant lane applies at 0.76, ABOVE the 0.75 line. A bare
    «DICLOFENAC 50 mg TABLET» would otherwise resolve to the potassium salt just
    because it is the only PLAIN tablet, silently choosing between a 23,000 and a
    1,350,000 rial product. Withhold it for review instead."""
    cat = [_rec("DNA", "diclofenac sodium", "50 mg", "TABLET"),
           _rec("DK", "diclofenac potassium", "50 mg", "TABLET")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, _why = sm.match(sm.parse_name("DICLOFENAC TABLET ORAL 50 mg", vocab), idx)
    assert rec is None and conf == 0.0


def test_a_row_naming_no_dose_will_not_be_handed_one():
    """«IBUPROFEN INJECTION» with no strength resolved to the 100 mg/mL adult
    product at 0.80 — ABOVE the 0.75 line — while the catalog also holds PEDEA at
    5 mg/mL, the preterm-neonate dose for closing a ductus arteriosus. A
    twenty-fold difference settled by index iteration order. Where the strengths
    differ the row is genuinely ambiguous and belongs to a human.
    """
    cat = [_rec("ADULT", "ibuprofen", "100 mg/1mL", "INJECTION"),
           _rec("PEDEA", "ibuprofen", "5 mg/1mL", "INJECTION")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, _why = sm.match(sm.parse_name("IBUPROFEN INJECTION INTRAVENOUS", vocab), idx)
    assert rec is None and conf == 0.0

    # naming the strength resolves it precisely, both ways
    hit = lambda n: (lambda r: r[0].irc if r[0] else None)(
        sm.match(sm.parse_name(n, vocab), idx))
    assert hit("IBUPROFEN INJECTION INTRAVENOUS 5 mg/1mL") == "PEDEA"
    assert hit("IBUPROFEN INJECTION PARENTERAL 100 mg/1mL") == "ADULT"


def test_one_strength_still_matches_without_a_dose():
    """The refusal is about AMBIGUITY, not about missing doses: when the catalog
    holds a single strength for that form there is nothing to choose between."""
    cat = [_rec("ONLY", "acetazolamide", "250 mg", "TABLET")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, _why = sm.match(sm.parse_name("ACETAZOLAMIDE TABLET ORAL", vocab), idx)
    assert rec is not None and rec.irc == "ONLY" and conf == sm.CONF_EXACT_NO_DOSE


def test_a_single_variant_can_still_be_identity_bearing_by_judgement():
    """«ibuprofen lysine» is the intravenous neonatal product for closing a
    patent ductus arteriosus. It shares a molecule with the oral analgesic and
    nothing else — different route, indication, patient and price — but the
    catalog lists only ONE variant, so the count rule cannot see it. Owner's
    ruling, recorded in _IDENTITY_BEARING_MODIFIERS.
    """
    cat = [_rec("IBU", "ibuprofen", "400 mg", "TABLET"),
           _rec("LYS", "ibuprofen lysine", "10 mg/1mL", "INJECTION")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    assert "ibuprofen" in sm.identity_bearing_bases(cat)
    hit = lambda n: (lambda r: r[0].irc if r[0] else None)(
        sm.match(sm.parse_name(n, vocab), idx))
    assert hit("IBUPROFEN TABLET ORAL 400 mg") == "IBU"
    assert hit("IBUPROFEN LYSINE INJECTION INTRAVENOUS 10 mg/1mL") == "LYS"


def test_a_hydration_state_is_not_a_second_salt():
    """«azithromycin anhydrous» and «azithromycin dihydrate» are one substance
    dried two ways — folding them is correct and must keep working."""
    cat = [_rec("A1", "azithromycin anhydrous", "500 mg", "TABLET"),
           _rec("A2", "azithromycin dihydrate", "250 mg", "TABLET")]
    assert "azithromycin" not in sm.identity_bearing_bases(cat)


def test_a_single_salt_base_still_folds():
    """Only one losartan salt is marketed, so «LOSARTAN» must still reach it."""
    cat = [_rec("L", "losartan potassium", "50 mg", "TABLET")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, _c, _w = sm.match(sm.parse_name("LOSARTAN TABLET ORAL 50 mg", vocab), idx)
    assert rec is not None and rec.irc == "L"


def test_a_mineral_counter_ion_is_never_folded_away():
    """A mineral counter-ion stays in the name, whichever position it holds.

    Adding sodium/potassium/calcium/magnesium to the salt vocabulary on
    2026-08-04 made «losartan potassium» reach a row naming the base — and made
    diclofenac sodium indistinguishable from diclofenac potassium, which are two
    products at 3,300–1,350,000 and 23,000–39,000 rial. It was reverted the same
    day. Where a base really has one marketed salt, the salt-tolerant lane
    resolves it by consulting the CATALOG, which a fixed word list cannot do.
    """
    for whole in ("losartan potassium", "pantoprazole sodium",
                  "calcium carbonate", "calcium citrate", "sodium chloride",
                  "sodium valproate", "potassium chloride", "magnesium oxide",
                  "sodium polystyrene sulfonate", "zinc sulfate"):
        assert sm.normalize(whole) == whole


def test_the_insurer_spelling_reaches_the_nfi_name():
    """salamat drops the «o» from hydrochlorothiazide and writes REFAMPICIN.
    Both targets exist as NFI generics and neither source does, which is the
    synonym file's own admission rule."""
    from services.core.drug_catalog.schema import canonical_ingredient
    assert canonical_ingredient("hydrochlorthiazide") == "hydrochlorothiazide"
    assert canonical_ingredient("refampicin") == "rifampin"


def test_two_spellings_of_one_salt_meet():
    """NFI writes «betahistine hydrochloride», the insurer «BETAHISTINE
    DIHYDROCHLORIDE». One stripped and the other did not, so the two spellings
    of a single substance could never meet and all 30 betahistine tablets were
    unreachable from the insurer list."""
    cat = [_rec("BET", "betahistine hydrochloride", "8 mg", "TABLET")]
    idx, vocab = sm.build_index(cat), sm.build_form_vocab(cat)
    rec, conf, _why = sm.match(
        sm.parse_name("BETAHISTINE DIHYDROCHLORIDE 8 mg TABLET ORAL", vocab), idx)
    assert rec is not None and rec.irc == "BET" and conf == sm.CONF_EXACT_DOSE


def test_an_inhaler_reaches_its_own_form_family():
    """NFI spells a respiratory inhaler three ways — AEROSOL METERED, INHALANT,
    POWDER METERED — and splitting on the comma put them in three families, so a
    row saying INHALANT could not reach a pMDI. Nasal SPRAY stays out: fluticasone
    nasal is not fluticasone inhaled."""
    assert sm.form_family("INHALANT") == sm.form_family("AEROSOL, METERED")
    assert sm.form_family("POWDER, METERED") == sm.form_family("INHALANT")
    assert sm.form_family("SPRAY, METERED") != sm.form_family("INHALANT")
    assert sm.form_family("POWDER, FOR SOLUTION") != sm.form_family("POWDER, METERED")


def test_one_substance_under_two_names_is_not_a_combination():
    """generic_name and generic_full routinely spell one substance two ways.
    Counting both made a mono product look like a two-ingredient combination,
    and the ingredient-set guard then rejected its own formulary row."""
    import dataclasses
    r = dataclasses.replace(
        _rec("BET", "betahistine hydrochloride", "8 mg", "TABLET"),
        monograph={"generic_full": "BETAHISTINE DIHYDROCHLORIDE TABLET ORAL 8 mg"})
    assert len(sm.record_components(r, {"betahistine"})) == 1


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


def test_price_picks_the_form_a_truncated_name_omits():
    """The real Phenergan case: «PROMETHAZINE HCL» states no dosage form, and the
    generic exists as a 180﷼ tablet and a 550,000﷼ injection. Matching on the
    ingredient alone chose the tablet, so the insurer's 539,362﷼ landed on it and
    produced a +299,546% price proposal. The price identifies the form."""
    cat = [
        _rec("TAB", "promethazine hydrochloride", "25 mg", "TABLET"),
        _rec("INJ", "promethazine hydrochloride", "25 mg/1mL", "INJECTION"),
    ]
    cat[0] = CatalogRecord(irc="TAB", name_fa="قرص", generic_name="promethazine hydrochloride",
                           dosage_form="TABLET", strength="25 mg",
                           announced_price=Decimal("180"))
    cat[1] = CatalogRecord(irc="INJ", name_fa="آمپول", generic_name="promethazine hydrochloride",
                           dosage_form="INJECTION", strength="25 mg/1mL",
                           announced_price=Decimal("550000"))
    picked = sm.price_picks_form(cat, 539362)
    assert picked is not None and picked.irc == "INJ"
    # the tablet's own price also resolves correctly
    assert sm.price_picks_form(cat, 200).irc == "TAB"
    # indecisive evidence must NOT pick. "Between" is in RATIO space, so the
    # ambiguous point is the geometric mean √(180 × 550,000) ≈ 9,950 — both
    # candidates are then 55× away and neither wins.
    assert sm.price_picks_form(cat, 9950) is None
    # a genuinely stale price (same form family, both far) is left alone
    assert sm.price_picks_form(cat[:1], 539362) is None      # single candidate
    assert sm.price_picks_form(cat, 0) is None and sm.price_picks_form(cat, None) is None


def test_link_rows_uses_price_to_pick_the_form():
    cat = [
        CatalogRecord(irc="TAB", name_fa="قرص", generic_name="promethazine hydrochloride",
                      dosage_form="TABLET", strength="25 mg", announced_price=Decimal("180")),
        CatalogRecord(irc="INJ", name_fa="آمپول", generic_name="promethazine hydrochloride",
                      dosage_form="INJECTION", strength="25 mg/1mL",
                      announced_price=Decimal("550000")),
    ]
    row = {"drug_name": "PROMETHAZINE  HCL", "reference_price": 539362}
    link = link_rows([row], cat)[0]
    assert link.matched and link.record.irc == "INJ", link.record and link.record.irc
    assert link.method.endswith("price_form")
    assert link.confidence <= 0.74          # inferred from price ⇒ review, not auto-apply
