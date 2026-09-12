"""Separation-of-duties and formulary-binding guards.

The approval rules are the only thing standing between "inventory staff wrote
off 400 tablets of a controlled substance" and "two named people agreed that
happened". Each rule gets its own test.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from services.core.inventory import formulary_binding as FB
from services.platform.routers.inventory_integrity import enforce_approval_authority

ALICE, BOB, CARL = "staff-a", "staff-b", "staff-c"


def _enforce(**over):
    kw = dict(status="pending", requested_by_id=ALICE, approver_id=BOB,
              is_controlled=False, witness_id=None)
    kw.update(over)
    return enforce_approval_authority(**kw)


# ── separation of duties ──────────────────────────────────────────────────
def test_a_different_person_may_approve():
    assert _enforce() is None


def test_the_requester_can_never_approve_their_own_write_off():
    with pytest.raises(HTTPException) as e:
        _enforce(approver_id=ALICE)
    assert e.value.status_code == 403


def test_an_already_decided_approval_cannot_be_decided_again():
    """Otherwise a rejected write-off could be quietly re-approved later."""
    for status in ("approved", "rejected", "applied"):
        with pytest.raises(HTTPException) as e:
            _enforce(status=status)
        assert e.value.status_code == 409


def test_a_controlled_substance_write_off_requires_a_witness():
    with pytest.raises(HTTPException) as e:
        _enforce(is_controlled=True)
    assert e.value.status_code == 422
    assert "witness" in str(e.value.detail)


def test_controlled_write_off_passes_with_a_third_person_as_witness():
    assert _enforce(is_controlled=True, witness_id=CARL) is None


def test_the_witness_may_not_be_the_requester():
    with pytest.raises(HTTPException) as e:
        _enforce(is_controlled=True, witness_id=ALICE)
    assert e.value.status_code == 422


def test_the_witness_may_not_be_the_approver():
    """Two signatures from two people, not two roles held by one person."""
    with pytest.raises(HTTPException) as e:
        _enforce(is_controlled=True, witness_id=BOB)
    assert e.value.status_code == 422


# ── role separation is real in the permission table ───────────────────────
def test_inventory_staff_cannot_approve_what_they_request():
    from shared.models.auth import ROLE_PERMISSIONS, StaffRole
    inv = ROLE_PERMISSIONS[StaffRole.INVENTORY_STAFF]
    assert "inventory:write" in inv and "inventory:approve" not in inv
    assert "inventory:approve" in ROLE_PERMISSIONS[StaffRole.PHARMACIST]
    assert "inventory:approve" in ROLE_PERMISSIONS[StaffRole.PHARMACY_MANAGER]


# ── formulary binding ─────────────────────────────────────────────────────
CAT = [
    {"irc": "IRC-MET-500-TAB", "gtin": "0622", "generic_name": "metformin",
     "strength": "500 mg", "dosage_form": "TAB"},
    {"irc": "IRC-MET-1000-TAB", "gtin": None, "generic_name": "Metformin",
     "strength": "1 g", "dosage_form": "قرص"},
    {"irc": "IRC-MET-500-SYR", "gtin": None, "generic_name": "metformin",
     "strength": "500mg", "dosage_form": "syrup"},
    {"irc": "IRC-VINC", "gtin": None, "generic_name": "vincristine sulfate",
     "strength": "1 mg/ml", "dosage_form": "injection"},
]


def test_gtin_is_the_strongest_evidence():
    p = FB.propose({"ndc11": "N1", "gtin": "0622", "generic_name": "whatever"}, CAT)
    assert (p.irc, p.method) == ("IRC-MET-500-TAB", "gtin")
    assert p.confidence == FB.CONF_GTIN


def test_generic_strength_and_form_identify_one_product():
    p = FB.propose({"ndc11": "N1", "generic_name": "Metformin HCl",
                    "strength": "500 mg", "dosage_form": "tablet"}, CAT)
    assert p.irc == "IRC-MET-500-TAB" and p.confidence == FB.CONF_GENERIC_STRENGTH_FORM


def test_grams_and_milligrams_are_the_same_scale():
    """1 g must match 1000 mg, or every gram-labelled product looks unbound."""
    p = FB.propose({"ndc11": "N1", "generic_name": "metformin",
                    "strength": "1000 mg", "dosage_form": "TAB"}, CAT)
    assert p.irc == "IRC-MET-1000-TAB"


def test_persian_and_english_dosage_forms_compare_equal():
    p = FB.propose({"ndc11": "N1", "generic_name": "metformin",
                    "strength": "1 g", "dosage_form": "قرص"}, CAT)
    assert p.irc == "IRC-MET-1000-TAB"


def test_the_salt_qualifier_does_not_block_a_match():
    p = FB.propose({"ndc11": "N1", "generic_name": "vincristine sulfate",
                    "strength": "1 mg", "dosage_form": "injection"}, CAT)
    assert p.irc == "IRC-VINC"


def test_two_forms_at_one_strength_are_ambiguous_not_guessed():
    """Tablet vs syrup at 500 mg: picking one would put a wrong price on real
    stock. Report the ambiguity instead."""
    p = FB.propose({"ndc11": "N1", "generic_name": "metformin",
                    "strength": "500 mg"}, CAT)
    assert p.irc is None and p.ambiguous is True and p.candidates == 2


def test_a_generic_absent_from_the_formulary_is_not_forced():
    p = FB.propose({"ndc11": "N1", "generic_name": "unobtainium"}, CAT)
    assert p.irc is None and p.method == "generic_not_in_formulary"


def test_generic_alone_binds_only_when_the_formulary_has_exactly_one():
    p = FB.propose({"ndc11": "N1", "generic_name": "vincristine"}, CAT)
    assert p.irc == "IRC-VINC" and p.confidence == FB.CONF_GENERIC_UNIQUE
    p2 = FB.propose({"ndc11": "N2", "generic_name": "metformin"}, CAT)
    assert p2.irc is None and p2.ambiguous is True


def test_persian_digits_normalise():
    p = FB.propose({"ndc11": "N1", "generic_name": "metformin",
                    "strength": "۵۰۰ mg", "dosage_form": "TAB"}, CAT)
    assert p.irc == "IRC-MET-500-TAB"


def test_summary_never_puts_an_ambiguous_row_in_the_bulk_bucket():
    props = [FB.propose({"ndc11": "N1", "generic_name": "metformin"}, CAT),
             FB.propose({"ndc11": "N2", "generic_name": "vincristine"}, CAT)]
    s = FB.summarize(props)
    assert s["bindable"] == 1 and s["ambiguous"] == 1


def test_iu_and_percent_never_convert_to_mass():
    """A 5% cream is not 5 mg. Cross-namespace matching would bind unrelated
    products together."""
    assert FB.normalize_strength("5 %") == (5.0, "%")
    assert FB.normalize_strength("5 mg") == (5.0, "mass")
    assert FB.normalize_strength("100 IU") == (100.0, "iu")
