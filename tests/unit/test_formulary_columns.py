"""What the insurers publish beyond name, code and price.

A full column inventory of both formularies (2026-08-09) found four fields
carrying real reimbursement meaning, and one of them was being thrown away:

  tamin    ceiling    quantity ceiling — captured
           inpatient  inpatient-only flag — captured
           covered    NOT a yes/no. Four states, and two of them name the
                      FUNDING CHANNEL. This is the one that was lost.
  salamat  conditions age bands, prescriber level, domestic-production — captured

«صرفا مشمول يارانه دولت» (335 rows) and «صرفا مشمول صندوق صعب العلاج» (32) both
carry share_pct 0. Coerced through _to_bool they became covered=true at 0 %,
which a pharmacist cannot tell apart from "not insured". It is neither: the
patient IS entitled, through a channel they have to claim from.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from services.core.drug_catalog import coverage_import as ci
from services.core.drug_catalog.schema import CatalogRecord


def rec(irc, generic="modafinil", strength="100 mg", form="TABLET", price=1000):
    return CatalogRecord(irc=irc, name_fa=irc, generic_name=generic, dosage_form=form,
                         strength=strength, announced_price=Decimal(price))


def build(covered_text, *, insurer="tamin"):
    row = {"drug_name": "MODAFINIL 100 mg TABLET ORAL", "covered": covered_text,
           "share_pct": "0", "reference_price": "75000"}
    out = ci.build_coverage([ci.LinkResult(row, rec("X"), 1.0, "code")],
                            insurer=insurer, min_confidence=0.75, catalog=[rec("X")])
    return out.applied.get("X", {}).get(insurer, {})


# ── the funding channel ─────────────────────────────────────────────────────
def test_a_funding_channel_is_kept_not_flattened():
    """«covered» alone cannot say "the money comes from somewhere else"."""
    e = build("صرفا مشمول صندوق صعب العلاج")
    assert e.get("funding_channel") == "hard_to_treat_fund"
    assert "hard_to_treat_fund" in (e.get("restrictions") or [])

    e = build("صرفا مشمول يارانه دولت")
    assert e.get("funding_channel") == "govt_subsidy"
    assert "govt_subsidy" in (e.get("restrictions") or [])


def test_an_ordinary_yes_or_no_gains_no_channel():
    for text in ("است", "نيست"):
        e = build(text)
        assert "funding_channel" not in e


def test_the_arabic_and_persian_spellings_both_match():
    """Real files mix ARABIC yeh/kaf with the Persian forms; «يارانه» here is
    written with Arabic yeh, and the fold must catch it."""
    assert build("صرفا مشمول يارانه دولت").get("funding_channel") == "govt_subsidy"
    assert build("صرفا مشمول یارانه دولت").get("funding_channel") == "govt_subsidy"


# ── differing price means differing row (owner's rule, 2026-08-09) ──────────
class _Row:
    def __init__(self, price=None, pack=None):
        self.announced_price, self.package_count = price, pack


def test_a_differing_price_refuses_the_succession():
    from services.core.drug_catalog.succession import same_product_refusal
    why = same_product_refusal(_Row(price=20000), _Row(price=75000))
    assert why and "قیمت" in why


def test_a_differing_pack_count_refuses_it_too():
    """The real modafinil pair: same brand, manufacturer, strength, ATC and
    licence date, same 75,000 price — separated only by 30 against 100."""
    from services.core.drug_catalog.succession import same_product_refusal
    why = same_product_refusal(_Row(price=75000, pack=30), _Row(price=75000, pack=100))
    assert why and "بسته" in why


def test_one_product_re_registered_is_allowed():
    from services.core.drug_catalog.succession import same_product_refusal
    assert same_product_refusal(_Row(price=40000, pack=30), _Row(price=40000, pack=30)) is None


def test_a_missing_field_cannot_refuse():
    """Silence is not evidence — a row with no price recorded must not block a
    succession the owner knows to be real."""
    from services.core.drug_catalog.succession import same_product_refusal
    assert same_product_refusal(_Row(price=None, pack=30), _Row(price=75000, pack=30)) is None
    assert same_product_refusal(None, _Row(price=1)) is None
