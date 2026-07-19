"""Price-refresh: run_sync divergence filter + high-confidence proposal gating."""
from decimal import Decimal

from services.core.drug_catalog.pricing_sync import compute_proposals
from services.core.drug_catalog.schema import CatalogRecord


def _rec(irc, ann):
    return CatalogRecord(irc=irc, name_fa=f"د{irc}", generic_name="x",
                         dosage_form="TABLET", strength="1 mg",
                         announced_price=Decimal(ann))


def test_compute_proposals_flags_divergence_direction():
    cat = [_rec("A", "10000"), _rec("B", "100000")]
    incoming = [{"irc": "A", "announced_price": "50000"},   # 5x up (stale catalog)
                {"irc": "B", "announced_price": "100000"}]  # unchanged → no proposal
    props = compute_proposals(cat, incoming)
    assert len(props) == 1 and props[0].irc == "A"
    assert props[0].kind == "increase" and props[0].pct_change == 400.0


def test_min_pct_noise_floor():
    from services.core.drug_catalog import pricing_sync
    cat = [_rec("A", "10000"), _rec("B", "100000")]
    incoming = [{"irc": "A", "announced_price": "50000"},    # +400%
                {"irc": "B", "announced_price": "105000"}]   # +5% (noise)
    props = compute_proposals(cat, incoming)
    kept = [p for p in props if abs(float(p.pct_change)) >= 25.0]  # what run_sync(min_pct) does
    assert {p.irc for p in kept} == {"A"}                    # +5% dropped, +400% kept
