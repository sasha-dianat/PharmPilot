"""Recovering a strength we already hold, without touching the network.

1,206 catalog rows have no `strength`; 687 of them state the dose plainly in
`monograph.generic_full`. The parser takes strength from `composition` and gives
up when that field is absent — so re-crawling would fetch the same bytes and
produce the same empty column. The defect is downstream of the fetch.
"""
from __future__ import annotations

from services.core.drug_catalog.backfill import strength_from_generic_full as sfg


def test_reads_the_dose_after_form_and_route():
    assert sfg("GLICLAZIDE TABLET, EXTENDED RELEASE ORAL 60 mg",
               "TABLET, EXTENDED RELEASE") == "60 mg"
    assert sfg("VITAMIN K1 (PHYTOMENADIONE) INJECTION PARENTERAL 10 mg/1mL 1MILLILITER",
               "INJECTION") == "10 mg/1mL"
    assert sfg("INTERFERON BETA-1A INJECTION PARENTERAL 12000000 [iU] 0.5MILLILITER",
               "INJECTION") == "12000000 [iU]"


def test_a_per_gram_denominator_is_not_a_pack_size():
    """«100000 [iU]/1g 15GRAM»: the 1g is the concentration's denominator and the
    15GRAM is the tube. Stripping both left a dangling '100000 [iU]/' and made
    412 rows disagree with ground truth."""
    assert sfg("NYSTATIN OINTMENT TOPICAL 100000 [iU]/1g 15GRAM",
               "OINTMENT") == "100000 [iU]/1g"


def test_a_trailing_pack_size_is_dropped():
    assert sfg("MYCOPHENOLATE MOFETIL POWDER, FOR SUSPENSION ORAL 1 g/5mL 110G",
               "POWDER, FOR SUSPENSION") == "1 g/5mL"


def test_returns_none_rather_than_a_guess():
    assert sfg("", "TABLET") is None
    assert sfg("SOME PRODUCT TABLET ORAL", "TABLET") is None      # no dose stated
    assert sfg(None, "TABLET") is None


def test_combination_doses_are_kept_whole():
    got = sfg("ADULT COLD PREPARATIONS (7-23) TABLET ORAL 15 mg/2 mg/5 mg/500 mg",
              "TABLET")
    assert got == "15 mg/2 mg/5 mg/500 mg"


def test_works_without_a_form_hint():
    """dosage_form may be blank on exactly the rows that need repair."""
    assert sfg("GLICLAZIDE TABLET, EXTENDED RELEASE ORAL 60 mg", "") == "60 mg"
