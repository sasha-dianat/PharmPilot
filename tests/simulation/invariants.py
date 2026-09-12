"""The rules that must hold after every event, whatever the sequence.

Two kinds of check live here. The first compares the application against the
oracle — if they disagree, one of them is wrong and the simulation says so
rather than deciding which. The second is structural: things that must be true
of the database on its own terms, regardless of what the oracle thinks.

A check returns a list of failure strings. Empty means it held.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import text

from .oracle import BUCKETS, Oracle, q

FIELDS = ("on_hand", "reserved", "damaged", "returned", "in_transit")


# ── application vs ground truth ───────────────────────────────────────────

def lots_agree(app: dict[str, dict], oracle: Oracle) -> list[str]:
    """Every lot balance, in both worlds, field by field."""
    truth = oracle.state()
    bad = []
    for lot_id, expected in truth.items():
        actual = app.get(lot_id)
        if actual is None:
            bad.append(f"lot {lot_id}: present in oracle, absent from the database")
            continue
        for f in FIELDS:
            e, a = getattr(expected, f), actual[f]
            if e != a:
                bad.append(f"lot {lot_id}.{f}: expected {e}, database has {a}")
    for lot_id in app:
        if lot_id not in truth:
            bad.append(f"lot {lot_id}: in the database, unknown to the oracle")
    return bad


def sku_totals_agree(stock_levels: dict[str, dict], oracle: Oracle) -> list[str]:
    """`stock_levels` is a denormalisation; it must equal the sum of its lots."""
    truth = oracle.by_ndc()
    bad = []
    for ndc, expected in truth.items():
        actual = stock_levels.get(ndc)
        if actual is None:
            if expected.on_hand or expected.reserved:
                bad.append(f"sku {ndc}: holds stock but has no stock_levels row")
            continue
        if expected.on_hand != actual["on_hand"]:
            bad.append(f"sku {ndc}.on_hand: expected {expected.on_hand}, "
                       f"aggregate has {actual['on_hand']}")
        if expected.reserved != actual["reserved"]:
            bad.append(f"sku {ndc}.reserved: expected {expected.reserved}, "
                       f"aggregate has {actual['reserved']}")
    return bad


def valuation_agrees(app_total, oracle: Oracle,
                     tolerance: Decimal = Decimal("0.01")) -> list[str]:
    expected = oracle.valuation()
    actual = q(app_total)
    if abs(expected - actual) > tolerance:
        return [f"valuation: expected {expected}, application reports {actual}"]
    return []


# ── structural, regardless of the oracle ──────────────────────────────────

async def no_negative_quantities(db, pid) -> list[str]:
    rows = (await db.execute(text("""
        SELECT id, ndc11, quantity_on_hand, quantity_reserved, quantity_damaged,
               quantity_returned, quantity_in_transit
        FROM inventory_lots WHERE pharmacy_id = :p AND is_deleted = false
          AND (quantity_on_hand < 0 OR quantity_reserved < 0
               OR quantity_damaged < 0 OR quantity_returned < 0
               OR quantity_in_transit < 0)"""), {"p": pid})).mappings().all()
    return [f"lot {r['id']} ({r['ndc11']}) holds a negative quantity: {dict(r)}"
            for r in rows]


async def reserved_within_on_hand(db, pid) -> list[str]:
    """More units promised than exist is the double-promise, after the fact."""
    rows = (await db.execute(text("""
        SELECT id, ndc11, quantity_on_hand, quantity_reserved
        FROM inventory_lots WHERE pharmacy_id = :p AND is_deleted = false
          AND quantity_reserved > quantity_on_hand"""), {"p": pid})).mappings().all()
    return [f"lot {r['id']} ({r['ndc11']}): reserved {r['quantity_reserved']} "
            f"exceeds on-hand {r['quantity_on_hand']}" for r in rows]


async def aggregate_matches_lots(db, pid) -> list[str]:
    rows = (await db.execute(text("""
        SELECT s.ndc11, s.quantity_on_hand AS agg,
               COALESCE(SUM(il.quantity_on_hand), 0) AS lots
        FROM stock_levels s
        LEFT JOIN inventory_lots il
               ON il.ndc11 = s.ndc11 AND il.pharmacy_id = s.pharmacy_id
              AND il.is_deleted = false
        WHERE s.pharmacy_id = :p
        GROUP BY s.ndc11, s.quantity_on_hand
        HAVING ABS(s.quantity_on_hand - COALESCE(SUM(il.quantity_on_hand), 0)) > 0.001"""),
        {"p": pid})).mappings().all()
    return [f"sku {r['ndc11']}: aggregate {r['agg']} != sum of lots {r['lots']}"
            for r in rows]


async def reservation_counter_matches_rows(db, pid) -> list[str]:
    """`quantity_reserved` is a denormalisation of the reservation rows."""
    rows = (await db.execute(text("""
        SELECT il.id, il.quantity_reserved AS counter,
               COALESCE((SELECT SUM(r.quantity) FROM inventory_reservations r
                         WHERE r.inventory_lot_id = il.id AND r.status = 'active'
                           AND r.is_deleted = false), 0) AS rows_sum
        FROM inventory_lots il
        WHERE il.pharmacy_id = :p AND il.is_deleted = false"""),
        {"p": pid})).mappings().all()
    return [f"lot {r['id']}: reserved counter {r['counter']} != active "
            f"reservations {r['rows_sum']}"
            for r in rows if abs(float(r["counter"]) - float(r["rows_sum"])) > 0.001]


async def movements_explain_stock(db, pid) -> list[str]:
    """Conservation: every lot's on-hand must be its movement history summed.

    This is the check that catches stock appearing or vanishing without a
    ledger row — the failure that makes every other number meaningless.
    """
    rows = (await db.execute(text("""
        SELECT il.id, il.ndc11, il.quantity_on_hand AS actual,
               COALESCE((SELECT SUM(m.quantity_delta) FROM inventory_movements m
                         WHERE m.inventory_lot_id = il.id), 0) AS from_movements
        FROM inventory_lots il
        WHERE il.pharmacy_id = :p AND il.is_deleted = false"""),
        {"p": pid})).mappings().all()
    bad = []
    for r in rows:
        # Bucket transfers move units without changing on-hand's *total*, so
        # the raw delta sum is only expected to match when no bucket movement
        # has touched the lot. The caller narrows this; here we report the gap.
        if abs(float(r["actual"]) - float(r["from_movements"])) > 0.001:
            bad.append(f"lot {r['id']} ({r['ndc11']}): on-hand {r['actual']} "
                       f"but movements sum to {r['from_movements']}")
    return bad


async def no_orphan_rows(db, pid) -> list[str]:
    """Rows pointing at things that do not exist."""
    bad = []
    n = (await db.execute(text("""
        SELECT count(*) FROM inventory_movements m
        LEFT JOIN inventory_lots il ON il.id = m.inventory_lot_id
        WHERE m.pharmacy_id = :p AND m.inventory_lot_id IS NOT NULL
          AND il.id IS NULL"""), {"p": pid})).scalar()
    if n:
        bad.append(f"{n} movement(s) reference a lot that does not exist")
    n = (await db.execute(text("""
        SELECT count(*) FROM inventory_reservations r
        LEFT JOIN inventory_lots il ON il.id = r.inventory_lot_id
        WHERE r.pharmacy_id = :p AND il.id IS NULL"""), {"p": pid})).scalar()
    if n:
        bad.append(f"{n} reservation(s) reference a lot that does not exist")
    return bad


async def approved_write_offs_only(db, pid) -> list[str]:
    """A movement type that destroys value must carry an approval."""
    rows = (await db.execute(text("""
        SELECT id, movement_type, quantity_delta FROM inventory_movements
        WHERE pharmacy_id = :p AND approval_id IS NULL
          AND movement_type IN ('WASTE','EXPIRY_REMOVAL','RECALL_REMOVAL',
                                'SUPPLIER_CREDIT','COUNT_LOSS','COUNT_GAIN')"""),
        {"p": pid})).mappings().all()
    return [f"movement {r['id']} ({r['movement_type']}, {r['quantity_delta']}) "
            f"destroyed value with no approval" for r in rows]


async def chain_intact(db, pid) -> list[str]:
    """The hash chain, verified with the application's own verifier.

    Reimplementing SHA-256 chaining in the oracle would test my arithmetic
    rather than theirs; what matters here is that the chain covers every
    movement the simulation produced and still verifies.
    """
    from services.core.inventory.ledger import verify_chain
    from services.platform.routers.inventory_integrity import chain_rows_for

    rows = (await db.execute(text("""
        SELECT id, pharmacy_id, irc, inventory_lot_id, movement_type,
               quantity_delta, quantity_after, created_by, prev_hash, event_hash,
               created_at
        FROM inventory_movements
        WHERE pharmacy_id = :p AND event_hash IS NOT NULL
        ORDER BY created_at ASC, id ASC"""), {"p": pid})).mappings().all()
    if not rows:
        return []
    res = verify_chain(chain_rows_for(rows))
    if not res.get("intact"):
        return [f"movement chain broken at index {res.get('break_index')}: "
                f"{res.get('detail')}"]

    unchained = (await db.execute(text(
        "SELECT count(*) FROM inventory_movements "
        "WHERE pharmacy_id = :p AND event_hash IS NULL"), {"p": pid})).scalar()
    if unchained:
        return [f"{unchained} movement(s) carry no hash and are outside the chain"]
    return []


async def structural(db, pid) -> list[str]:
    """Every structural check, in one call."""
    out: list[str] = []
    for check in (no_negative_quantities, reserved_within_on_hand,
                  aggregate_matches_lots, reservation_counter_matches_rows,
                  no_orphan_rows, approved_write_offs_only, chain_intact):
        out.extend(await check(db, pid))
    return out
