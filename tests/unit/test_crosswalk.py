"""Decision crosswalk (X1–X3): keys, linker short-circuit, override re-apply."""
from decimal import Decimal

import pytest

from services.core.drug_catalog.coverage_import import link_rows
from services.core.drug_catalog.crosswalk import (
    apply_overrides, lookup_key, row_source_code)
from services.core.drug_catalog.enrichment import enrich_key
from services.core.drug_catalog.schema import CatalogRecord


def _rec(irc="A1", **kw):
    base = dict(irc=irc, name_fa="آ", generic_name="metformin",
                dosage_form="TABLET", strength="500 mg",
                announced_price=Decimal("1000"))
    base.update(kw)
    return CatalogRecord(**base)


def test_source_code_detection_and_key_order():
    assert row_source_code({"drug_code": "01211"}) == "01211"       # tamin's column
    assert row_source_code({"code": " 77 "}) == "77"
    assert row_source_code({"drug_name": "x"}) is None
    # most-specific first: the insurer's own code beats the name key
    assert lookup_key("tamin", "01211", "k") == ["tamin|code:01211", "tamin|name:k"]
    assert lookup_key("tamin", None, "k") == ["tamin|name:k"]


def test_confirmed_decision_short_circuits_matcher():
    # A row that fuzzy-matching would NEVER link (nonsense name) resolves
    # exactly, because the owner already ruled on it.
    cat = [_rec("A1"), _rec("B2", name_fa="ب", strength="850 mg")]
    row = {"drug_name": "ZZZ UNMATCHABLE GIBBERISH", "drug_code": "01211"}
    assert not link_rows([row], cat)[0].matched                      # baseline
    cw = {"tamin|code:01211": {"irc": "B2", "status": "confirmed"}}
    link = link_rows([row], cat, crosswalk=cw, insurer="tamin")[0]
    assert link.matched and link.record.irc == "B2"
    assert link.confidence == 1.0 and link.method == "crosswalk"


def test_confirmed_by_name_key_when_no_source_code():
    cat = [_rec("A1")]
    name = "متفورمین ۵۰۰ عجیب"
    cw = {f"salamat|name:{enrich_key(name)}": {"irc": "A1", "status": "confirmed"}}
    link = link_rows([{"drug_name": name}], cat, crosswalk=cw, insurer="salamat")[0]
    assert link.matched and link.record.irc == "A1" and link.method == "crosswalk"


def test_rejected_decision_is_not_re_proposed():
    # The owner said "this is not that drug" — the matcher must not offer it again.
    cat = [_rec("A1")]
    row = {"drug_name": "METFORMIN 500 TABLET"}
    assert link_rows([row], cat)[0].matched                          # would match
    cw = {f"tamin|name:{enrich_key(row['drug_name'])}":
          {"irc": None, "status": "rejected", "reason": "wrong_strength"}}
    link = link_rows([row], cat, crosswalk=cw, insurer="tamin")[0]
    assert not link.matched and link.method == "crosswalk-rejected"


def test_crosswalk_for_other_insurer_is_ignored():
    cat = [_rec("A1"), _rec("B2", name_fa="ب")]
    row = {"drug_name": "ZZZ UNMATCHABLE", "drug_code": "01211"}
    cw = {"salamat|code:01211": {"irc": "B2", "status": "confirmed"}}
    assert not link_rows([row], cat, crosswalk=cw, insurer="tamin")[0].matched


def test_stale_confirmed_irc_falls_back_to_matching():
    # Product deleted from the catalog since the decision → don't crash, just match.
    cat = [_rec("A1")]
    row = {"drug_name": "METFORMIN 500 TABLET"}
    cw = {f"tamin|name:{enrich_key(row['drug_name'])}":
          {"irc": "GONE", "status": "confirmed"}}
    link = link_rows([row], cat, crosswalk=cw, insurer="tamin")[0]
    assert link.matched and link.record.irc == "A1" and link.method != "crosswalk"


def test_overrides_reassert_over_source_values():
    rec = _rec("A1", country="China", announced_price=Decimal("1000"))
    out = apply_overrides(rec, {"A1": {"country": "ایران",
                                       "announced_price": "2500"}})
    assert out.country == "ایران"                       # source value overridden
    assert out.announced_price == Decimal("2500")
    assert apply_overrides(rec, {"OTHER": {"country": "x"}}) is rec   # untouched
    assert apply_overrides(rec, {}) is rec
    # unknown field / bad number are skipped, never raise
    assert apply_overrides(rec, {"A1": {"nope": "x"}}).country == "China"
    assert apply_overrides(rec, {"A1": {"announced_price": "abc"}}).announced_price \
        == Decimal("1000")
