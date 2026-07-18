"""هوش تطبیق — Fellegi–Sunter fit/score, learned price bands, demotion seam."""
from decimal import Decimal

from services.core.drug_catalog.coverage_import import LinkResult, link_rows
from services.core.drug_catalog.match_intel import (
    extract_features, fit, price_verdict, score, verify_links)
from services.core.drug_catalog.schema import CatalogRecord


def _rec(**kw):
    base = dict(irc="R1", name_fa="آ", generic_name="metformin hydrochloride",
                dosage_form="TABLET", strength="500 mg",
                announced_price=Decimal("100000"))
    base.update(kw)
    return CatalogRecord(**base)


def test_features_levels():
    f = extract_features("METFORMIN 500MG TABLET", _rec())
    assert f["name"] in ("high", "mid") and f["strength"] == "exact" and f["form"] == "same"
    f2 = extract_features("METFORMIN 850MG SYRUP", _rec())
    assert f2["strength"] == "conflict" and f2["form"] == "different"


def _toy_model():
    good = {"name": "high", "strength": "exact", "form": "same", "brand": "no"}
    bad = {"name": "low", "strength": "conflict", "form": "different", "brand": "no"}
    pairs = [(good, True)] * 20 + [(bad, False)] * 20
    return fit(pairs, {"salamat": [0.9, 1.0, 1.0, 1.1, 0.95, 1.05, 1.0, 0.98, 1.02]})


def test_fit_and_score_separate_good_from_bad():
    m = _toy_model()
    good_s = score({"name": "high", "strength": "exact", "form": "same", "brand": "no"}, m)
    bad_s = score({"name": "low", "strength": "conflict", "form": "different", "brand": "no"}, m)
    assert good_s > 0 > bad_s          # evidence for vs against


def test_price_band_learned_and_outlier_flagged():
    m = _toy_model()
    assert price_verdict(100000, 100000, m, "salamat") == "in_band"
    assert price_verdict(2000000, 100000, m, "salamat") == "out_band"   # 20× → wrong product
    assert price_verdict(100000, 100000, m, "tamin") == "unknown"       # no band learned
    assert price_verdict(None, 100000, m, "salamat") == "unknown"


def test_verify_links_demotes_price_outlier_with_reason():
    m = _toy_model()
    rec = _rec()
    link = LinkResult({"drug_name": "METFORMIN 500MG TABLET",
                       "reference_price": "2000000"}, rec, 0.9, "fuzzy")
    assert link.matched
    demoted = verify_links([link], m, "salamat")
    assert demoted == 1 and link.confidence <= 0.74
    assert "قیمت" in link.row["هشدار_هوش_تطبیق"] and link.method.endswith("+intel")


def test_verify_links_never_touches_irc_or_good_matches():
    m = _toy_model()
    good = LinkResult({"drug_name": "METFORMIN 500MG TABLET",
                       "reference_price": "100000"}, _rec(), 0.9, "fuzzy")
    exact = LinkResult({"drug_name": "whatever", "reference_price": "2000000"},
                       _rec(), 1.0, "irc")
    assert verify_links([good, exact], m, "salamat") == 0
    assert good.confidence == 0.9 and exact.method == "irc"


def test_stamp_reject_reasons_only_on_refused_items():
    from services.core.drug_catalog.coverage_harvest import stamp_reject_reasons
    review = [{"id": 0, "irc": "A"}, {"id": 1, "irc": "B"}, {"id": 2, "irc": "C"}]
    out = stamp_reject_reasons(review, accepted={1},
                               reasons={"0": {"code": "wrong_strength", "note": "۱۵ نه ۳۰"},
                                        "1": {"code": "wrong_form"},        # accepted → ignored
                                        "2": {"code": "bogus_code", "note": "n"}})
    assert out[0]["reject_reason"] == "wrong_strength" and out[0]["reject_note"] == "۱۵ نه ۳۰"
    assert "reject_reason" not in out[1]                 # accepted item untouched
    assert "reject_reason" not in out[2] and out[2]["reject_note"] == "n"  # bad code dropped, note kept
    assert review[0] is not out[0]                        # new objects (JSONB reassignment)


def test_coded_refusals_train_with_double_weight():
    # Same negative evidence, once coded — the coded corpus must push u for the
    # conflicting level higher (the model becomes more suspicious of it).
    good = {"name": "high", "strength": "exact", "form": "same", "brand": "no"}
    bad = {"name": "high", "strength": "conflict", "form": "same", "brand": "no"}
    base = [(good, True)] * 20 + [(bad, False)] * 20
    coded = base + [(bad, False)] * 20                    # what double-weight produces
    m_plain, m_coded = fit(base, {}), fit(coded, {})
    u_plain = m_plain["mu"]["strength"]["conflict"][1]
    u_coded = m_coded["mu"]["strength"]["conflict"][1]
    assert u_coded > u_plain
    assert score(bad, m_coded) < score(bad, m_plain)      # stricter after coding


def test_transliteration_bridges_scripts():
    # «سالبوتامول» must be seen as similar to "salbutamol" — SequenceMatcher
    # alone scores cross-script pairs near zero.
    from services.core.drug_catalog.match_intel import _translit
    from difflib import SequenceMatcher
    assert SequenceMatcher(None, _translit("سالبوتامول"),
                           "salbutamol").ratio() >= 0.7
    f = extract_features("سالبوتامول ۲ میلی گرم قرص",
                         _rec(generic_name="salbutamol", strength="2 mg",
                              dosage_form="TABLET"))
    assert f["name"] in ("high", "mid")          # was "low" pre-transliteration
    assert _translit("aspirin 100") == "aspirin 100"   # latin passes through


def test_score_suggestion_second_boundary():
    # Same engine, research boundary: coherent research scores high, research
    # for a DIFFERENT drug scores low; no model → None (never a fake number).
    from services.core.drug_catalog.match_intel import score_suggestion
    m = _toy_model()
    good = score_suggestion("METFORMIN 500MG TABLET",
                            {"generic_name": "metformin hydrochloride",
                             "dosage_form": "tablet", "strengths": ["500 mg"]}, m)
    bad = score_suggestion("METFORMIN 500MG TABLET",
                           {"generic_name": "salbutamol", "dosage_form": "syrup",
                            "strengths": ["2 mg/5 mL"]}, m)
    assert good is not None and bad is not None and good > bad
    assert score_suggestion("x", {"generic_name": "y"}, None) is None


def test_score_suggestion_reads_variant_strengths():
    from services.core.drug_catalog.match_intel import score_suggestion
    m = _toy_model()
    s = score_suggestion("TOLMETIN 600MG CAPSULE",
                         {"generic_name": "tolmetin", "dosage_form": None,
                          "strengths": None,
                          "variants": [{"dosage_form": "capsule", "strength": "400 mg"},
                                       {"dosage_form": "capsule", "strength": "600 mg"}]}, m)
    weak = score_suggestion("TOLMETIN 600MG CAPSULE",
                            {"generic_name": "tolmetin"}, m)
    assert s is not None and weak is not None and s > weak   # variants supplied evidence


def test_annotate_review_fs_orders_boundary_first():
    from services.core.drug_catalog.match_intel import annotate_review_fs
    m = _toy_model()
    rec = _rec()
    review = [{"id": 0, "irc": "R1", "row": {"drug_name": "METFORMIN 500MG TABLET"}},
              {"id": 1, "irc": "MISSING", "row": {"drug_name": "x"}},
              "not-a-dict"]
    out = annotate_review_fs(review, {"R1": rec}, m)
    assert out[0]["fs"] > 0                        # scored
    assert "fs" not in out[1] and out[2] == "not-a-dict"   # tolerant
    assert annotate_review_fs(review, {"R1": rec}, None) == review  # no model → untouched


def test_verify_is_noop_without_model():
    link = LinkResult({"drug_name": "x"}, _rec(), 0.9, "fuzzy")
    assert verify_links([link], None, "salamat") == 0 and link.confidence == 0.9


def test_price_spreads_only_to_interchangeable_group_never_other_strengths():
    # EPHEDRINE 15 mg row: price reaches same-strength brand siblings
    # (interchangeable group — insurer reference prices are defined per group)
    # but NEVER the 30 mg product.
    from services.core.drug_catalog.coverage_import import build_coverage
    r15a = _rec(irc="E15A", name_fa="افدرین ۱۵ الف", generic_name="ephedrine",
                strength="15 mg")
    r15b = _rec(irc="E15B", name_fa="افدرین ۱۵ ب", generic_name="ephedrine",
                strength="15 mg", brand_name="OtherBrand")
    r30 = _rec(irc="E30", name_fa="افدرین ۳۰", generic_name="ephedrine",
               strength="30 mg")
    link = LinkResult({"drug_name": "EPHEDRINE HCL TABLET 15 mg",
                       "covered": "1", "reference_price": "50000"}, r15a, 0.95, "fuzzy")
    cov = build_coverage([link], insurer="salamat", catalog=[r15a, r15b, r30])
    assert cov.applied["E15A"]["salamat"]["reference_price"] == 50000
    assert cov.applied["E15B"]["salamat"]["reference_price"] == 50000   # brand sibling
    assert "E30" not in cov.applied                                     # other strength
