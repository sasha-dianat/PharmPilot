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


def test_verify_is_noop_without_model():
    link = LinkResult({"drug_name": "x"}, _rec(), 0.9, "fuzzy")
    assert verify_links([link], None, "salamat") == 0 and link.confidence == 0.9
