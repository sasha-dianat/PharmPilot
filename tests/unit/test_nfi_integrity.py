"""Spliced-monograph defense: labeled ATC path, page coherence, quarantine.

Background (2026-07-22): legacy NFI product pages can reference a generic-entity
id the site has since reused — the page renders drug A's product block with
drug B's monograph (RANITIDINE product pages carrying follitropin/G03GA05).
"""
from pathlib import Path

import pytest

from services.core.drug_catalog.nfi import (
    brand_generic_tokens, brand_stated_form, page_coherence, parse_atc_path,
    parse_detail, quarantine_monograph)

FIXTURE = Path(__file__).parent.parent / "fixtures" / "nfi_detail_212.html"


@pytest.fixture(scope="module")
def page():
    return FIXTURE.read_text(encoding="utf-8", errors="replace")


def test_atc_path_extracts_labeled_hierarchy(page):
    path = parse_atc_path(page)
    assert [p["code"] for p in path] == ["A", "A02", "A02B", "A02BA", "A02BA02"]
    assert path[0]["label"] == "ALIMENTARY TRACT AND METABOLISM"
    assert path[-2]["label"] == "H2-RECEPTOR ANTAGONISTS"    # pharm category
    assert path[-1]["label"] == "RANITIDINE"                 # leaf names the generic


def test_parse_detail_carries_atc_path_and_stays_correct(page):
    rec = parse_detail(page, 212)
    assert rec["irc"] == "8426195994121678"
    assert rec["atc"] == "A02BA02"
    assert rec["atc_path"][-1]["label"] == "RANITIDINE"
    assert rec["generic_name"] == "ranitidine" and rec["dosage_form"] == "TABLET"


def test_coherent_page_passes_the_gate(page):
    rec = parse_detail(page, 212)
    assert page_coherence(rec, {"ranitidine", "follitropin"}) == []


def _spliced():
    # what the legacy امین page actually produced on 2026-07-06
    return {
        "irc": "4263712643109596", "name_fa": "رانیتیدین 150",
        "brand_name": "RANITIDINE 150", "manufacturer": "داروسازی امین",
        "announced_price": "650",
        "generic_name": "follitropin", "generic_full": "FOLLITROPIN INJECTION",
        "dosage_form": "INJECTION", "atc": "G03GA05",
        "atc_path": [{"code": "G03GA05", "label": "FOLLITROPIN"}],
        "warnings": "…follitropin text…",
    }


def test_spliced_page_is_detected_via_vocabulary():
    reasons = page_coherence(_spliced(), {"ranitidine", "follitropin"})
    assert reasons and "ranitidine" in reasons[0]
    # without vocabulary the generic check cannot fire (brand may be a trade name)
    assert page_coherence(_spliced(), None) == []


def test_form_contradiction_needs_no_vocabulary():
    rec = {"brand_name": "EPINEPHRINE 1MG/ML SYRINGE",
           "dosage_form": "TABLET, EXTENDED RELEASE"}
    assert any(r.startswith("form:") for r in page_coherence(rec))
    # compatible marker/form is quiet
    assert page_coherence({"brand_name": "X 10MG TAB", "dosage_form": "TABLET"}) == []


def test_trade_names_and_synonyms_do_not_false_positive():
    vocab = {"liraglutide", "aluminum hydroxide", "ranitidine"}
    # pure trade name: VICTOZA states no generic → unverifiable → pass
    assert page_coherence({"brand_name": "VICTOZA", "generic_name": "liraglutide",
                           "dosage_form": "INJECTION"}, vocab) == []
    # spelling variant: aluminium vs aluminum → similarity guard keeps it quiet
    assert page_coherence({"brand_name": "ALUMINIUM HYDROXIDE 300 TAB",
                           "generic_name": "aluminum hydroxide",
                           "dosage_form": "TABLET"},
                          vocab | {"aluminium hydroxide"}) == []


def test_quarantine_keeps_product_block_and_rederives_identity():
    out = quarantine_monograph(_spliced(), ["generic: brand says ranitidine…"])
    # product block survives
    assert out["irc"] == "4263712643109596"
    assert out["manufacturer"] == "داروسازی امین"
    assert out["announced_price"] == "650"
    # foreign monograph is gone; identity re-derived from the brand string
    assert out.get("atc") is None and out.get("atc_path") is None
    assert out.get("warnings") is None
    assert out["generic_name"] == "ranitidine"
    assert out["strength"] == "150"        # no unit stated on brand → none guessed
    assert out["integrity"]["spliced_page"] is True


def test_brand_helpers():
    assert brand_generic_tokens("MEDROXYPROGESTERONE ACETATE 250MG TAB") == \
        ["medroxyprogesterone", "acetate"]
    assert brand_generic_tokens("LIDOCAINE 5%+DEXTROSE 7.5% (ORION) 2ML AMP") == ["lidocaine"]
    assert brand_stated_form("ACETAMINOPHEN 125MG PED SUPP") == "SUPPOSITORY"
    assert brand_stated_form("GLUCAGEN 1 MG/1ML VIAL") == "INJECTION"
    assert brand_stated_form("PLAIN NAME") is None
