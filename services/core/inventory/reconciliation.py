"""Inventory reconciliation — the checks that decide whether the books can be
trusted, and a severity model that says which failures may not be shipped.

Pure functions over already-fetched rows. The SQL that feeds them lives in
`services.platform.routers.inventory_integrity`; keeping the predicates here
means every rule is unit-testable against a hand-built counterexample instead of
a live database.

Design rule: a check never repairs. It states what is wrong, how much, and which
rows to look at. Repair is an approved, recorded movement — never a side effect
of running a report. That separation is what makes the report safe to run in
production on a schedule.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from .ledger import q

# Severity drives behaviour, not just colour:
#   critical — the ledger is provably wrong or stock is unaccounted for.
#              Blocks a clean bill of health; requires a human ruling.
#   high     — a real defect with financial or safety consequence.
#   medium   — a data-quality problem that will become one of the above.
#   info     — context; never blocks.
SEVERITIES = ("critical", "high", "medium", "info")


@dataclass
class Finding:
    check: str
    severity: str
    count: int
    title_fa: str
    detail: str
    samples: list[dict] = field(default_factory=list)
    remediation: str = ""

    def as_dict(self) -> dict:
        return {"check": self.check, "severity": self.severity, "count": self.count,
                "title_fa": self.title_fa, "detail": self.detail,
                "samples": self.samples[:10], "remediation": self.remediation}


def _f(row: dict, *names, default=0) -> Decimal:
    for n in names:
        if row.get(n) is not None:
            return q(row[n])
    return q(default)


# ── C1. Aggregate vs lots ─────────────────────────────────────────────────
def check_aggregate_drift(rows: list[dict], tolerance: Decimal = Decimal("0.001")) -> Finding:
    """`stock_levels.quantity_on_hand` must equal the sum of its lots.

    The aggregate is a denormalisation for fast lookup. When it drifts, every
    reorder decision and stockout probability is computed from a number that no
    lot supports — and the drift is invisible in the UI, which reads the
    aggregate.
    """
    bad = []
    for r in rows:
        agg, lots = _f(r, "aggregate"), _f(r, "lot_sum")
        if abs(agg - lots) > tolerance:
            bad.append({"irc": r.get("irc"), "ndc11": r.get("ndc11"),
                        "aggregate": float(agg), "lot_sum": float(lots),
                        "drift": float(q(agg - lots))})
    return Finding(
        "aggregate_drift", "critical", len(bad),
        "ناهماهنگی موجودی کل با مجموع بچ‌ها",
        "stock_levels.quantity_on_hand does not equal the sum of that item's lots.",
        sorted(bad, key=lambda x: -abs(x["drift"])),
        "Run a cycle count for the affected items, then post the variance as an "
        "approved COUNT_GAIN/COUNT_LOSS movement. Never edit the aggregate directly.",
    )


# ── C2. Negative stock ────────────────────────────────────────────────────
def check_negative_stock(rows: list[dict]) -> Finding:
    """Physical quantities cannot be negative. One appearing means a decrement
    was applied without a matching receipt, or a clamp was removed and exposed
    an older error."""
    bad = [{"irc": r.get("irc"), "ndc11": r.get("ndc11"), "lot_id": r.get("lot_id"),
            "lot_number": r.get("lot_number"), "quantity": float(_f(r, "quantity_on_hand"))}
           for r in rows if _f(r, "quantity_on_hand") < 0]
    return Finding(
        "negative_stock", "critical", len(bad),
        "موجودی منفی",
        "A lot or aggregate holds a negative quantity — physically impossible.",
        bad,
        "Freeze the item, count it, and post the variance. Investigate the "
        "movement history for the missing receipt.",
    )


# ── C3. Dispensed but never decremented ───────────────────────────────────
def check_fills_without_movements(fills: list[dict], movements_by_fill: set) -> Finding:
    """Every dispensed fill must have consumed stock.

    This is the defect ROADMAP line 34 records: `prescription_fills` grew while
    `inventory_movements` did not. Each orphan fill is stock that left the
    building without leaving the ledger.
    """
    bad = [{"fill_id": f.get("id"), "ndc11": f.get("ndc_dispensed"), "irc": f.get("irc"),
            "quantity": float(_f(f, "quantity_dispensed")),
            "filled_at": f.get("filled_at"), "lot_number": f.get("lot_number")}
           for f in fills if f.get("id") not in movements_by_fill]
    return Finding(
        "fill_without_movement", "critical", len(bad),
        "تحویل بدون کسر از موجودی",
        "A prescription fill has no corresponding inventory issue movement, so the "
        "units left the pharmacy without being deducted.",
        bad,
        "Backfill one DISPENSE movement per orphan fill against the lot recorded "
        "on the fill (or the FEFO lot when none was recorded), as an approved "
        "reconciliation batch, then enable the dispense hook.",
    )


# ── C4. Fills with no lot traceability ────────────────────────────────────
def check_untraceable_fills(fills: list[dict]) -> Finding:
    """A fill that names no lot cannot be recalled.

    When a manufacturer recalls lot X, the question is "which patients received
    it?". `PrescriptionFill.lot_number` is free text with no foreign key, so the
    answer today is a string comparison at best and nothing at all when blank.
    """
    bad = [{"fill_id": f.get("id"), "ndc11": f.get("ndc_dispensed"),
            "filled_at": f.get("filled_at")}
           for f in fills if not (f.get("inventory_lot_id") or f.get("lot_number"))]
    return Finding(
        "untraceable_fill", "high", len(bad),
        "تحویل بدون ردیابی بچ",
        "Fill records no lot, so a recall cannot identify the patients who "
        "received the affected batch.",
        bad,
        "Make lot selection mandatory at dispense; the ledger's FEFO pick "
        "supplies it automatically.",
    )


# ── C5. Formulary binding ─────────────────────────────────────────────────
def check_formulary_binding(rows: list[dict]) -> Finding:
    """Stock must point at a row of the real formulary.

    Inventory keys on `ndc11` (a US National Drug Code); the authoritative
    Iranian catalogue is `drug_catalog.irc`. Unbound stock cannot be priced,
    adjudicated against insurer coverage, or matched to a prescription written
    from the formulary.
    """
    bad = [{"ndc11": r.get("ndc11"), "lot_id": r.get("lot_id"),
            "quantity": float(_f(r, "quantity_on_hand")),
            "drug_name": r.get("drug_name")}
           for r in rows if not r.get("irc")]
    return Finding(
        "unbound_from_formulary", "high", len(bad),
        "عدم اتصال به فهرست دارویی رسمی",
        "Stock row carries no IRC, so it is not connected to the national "
        "formulary (drug_catalog) used for pricing and coverage.",
        bad,
        "Resolve IRC by GTIN, then by generic+strength+form; leave genuinely "
        "unresolvable rows for owner review rather than guessing.",
    )


# ── C6. Expired stock still sellable ──────────────────────────────────────
def check_expired_on_hand(lots: list[dict], as_of: date | None = None) -> Finding:
    today = as_of or date.today()
    bad = []
    for l in lots:
        exp = l.get("expiry_date")
        if isinstance(exp, str):
            exp = date.fromisoformat(exp)
        if exp and exp < today and _f(l, "quantity_on_hand") > 0 and not l.get("is_quarantined"):
            bad.append({"irc": l.get("irc"), "lot_number": l.get("lot_number"),
                        "expiry_date": exp.isoformat(),
                        "days_expired": (today - exp).days,
                        "quantity": float(_f(l, "quantity_on_hand")),
                        "value": float(_f(l, "value"))})
    return Finding(
        "expired_on_hand", "critical", len(bad),
        "داروی منقضی در دسترس فروش",
        "Expired lots still hold sellable quantity and are not quarantined — they "
        "can be picked by a dispense.",
        sorted(bad, key=lambda x: -x["days_expired"]),
        "Quarantine immediately, then post EXPIRY_REMOVAL with approval.",
    )


# ── C7. Suspicious adjustments ────────────────────────────────────────────
def check_suspicious_adjustments(movements: list[dict], *,
                                 window_days: int = 30,
                                 repeat_threshold: int = 3,
                                 large_pct: float = 25.0) -> Finding:
    """Patterns that distinguish an error from a habit.

    A single large correction is usually a miscount. The same staff member
    repeatedly writing down the same controlled item is the shape of diversion.
    We report the pattern and the evidence; a human decides what it means —
    never the model, and never a disciplinary output.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    by_actor: dict[tuple, list] = {}
    findings = []
    for m in movements:
        ts = m.get("created_at")
        if isinstance(ts, str):
            ts = datetime.fromisoformat(ts)
        if ts and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts and ts < cutoff:
            continue
        delta, before = _f(m, "quantity_delta"), _f(m, "quantity_before")
        pct = float(abs(delta) / before * 100) if before > 0 else 100.0
        is_loss = delta < 0 and m.get("movement_type") in (
            "ADJUSTMENT", "CORRECTION", "COUNT_LOSS", "WASTE")
        if not is_loss:
            continue
        key = (m.get("created_by"), m.get("irc") or m.get("ndc11"))
        by_actor.setdefault(key, []).append({"id": m.get("id"), "pct": round(pct, 1),
                                             "delta": float(delta),
                                             "controlled": bool(m.get("is_controlled"))})
        if pct >= large_pct or m.get("is_controlled"):
            findings.append({
                "movement_id": m.get("id"), "irc": m.get("irc"), "ndc11": m.get("ndc11"),
                "actor": m.get("created_by"), "delta": float(delta),
                "pct_of_stock": round(pct, 1), "reason": m.get("reason"),
                "controlled": bool(m.get("is_controlled")),
                "pattern": "large_write_down" if pct >= large_pct else "controlled_write_down",
            })
    for (actor, item), rows in by_actor.items():
        if len(rows) >= repeat_threshold:
            findings.append({
                "actor": actor, "irc_or_ndc": item, "occurrences": len(rows),
                "total_delta": round(sum(r["delta"] for r in rows), 3),
                "controlled": any(r["controlled"] for r in rows),
                "pattern": "repeat_write_down",
            })
    return Finding(
        "suspicious_adjustment", "high", len(findings),
        "اصلاحات مشکوک موجودی",
        "Write-downs that are large, repeated by one person on one item, or touch "
        "a controlled substance.",
        findings,
        "Route to a named reviewer with the movement history attached. Treat as a "
        "question to answer, not a conclusion about a person.",
    )


# ── C8. Duplicate / invalid lots ──────────────────────────────────────────
def check_duplicate_lots(lots: list[dict]) -> Finding:
    seen: dict[tuple, list] = {}
    for l in lots:
        key = (l.get("irc") or l.get("ndc11"), (l.get("lot_number") or "").strip().upper())
        if not key[1]:
            continue
        seen.setdefault(key, []).append(l)
    bad = [{"irc_or_ndc": k[0], "lot_number": k[1], "rows": len(v),
            "lot_ids": [x.get("lot_id") for x in v],
            "expiries": sorted({str(x.get("expiry_date")) for x in v})}
           for k, v in seen.items() if len(v) > 1]
    return Finding(
        "duplicate_lot", "medium", len(bad),
        "بچ تکراری",
        "The same lot number exists more than once for an item — receiving the "
        "same delivery twice double-counts stock.",
        bad,
        "Merge into the earliest row with an approved CORRECTION; keep both "
        "movement histories.",
    )


# ── C9. Unit-conversion errors ────────────────────────────────────────────
def check_unit_conversion(rows: list[dict], *, factor_threshold: float = 20.0) -> Finding:
    """On-hand wildly out of scale with the pack size is usually packs entered
    as units (or the reverse) — a 30× error that looks like a stockout or a
    year of surplus."""
    bad = []
    for r in rows:
        pack = _f(r, "package_count")
        onhand = _f(r, "quantity_on_hand")
        demand = _f(r, "avg_daily_demand")
        if pack <= 1 or onhand <= 0:
            continue
        if demand > 0:
            days = float(onhand / demand)
            if days > 365 * 3 and float(onhand) >= float(pack) * factor_threshold:
                bad.append({"irc": r.get("irc"), "ndc11": r.get("ndc11"),
                            "quantity_on_hand": float(onhand),
                            "package_count": float(pack),
                            "days_supply": round(days),
                            "hypothesis": "packs recorded as units"})
    return Finding(
        "unit_conversion_suspect", "medium", len(bad),
        "خطای احتمالی واحد شمارش",
        "On-hand implies an implausible days-supply given the pack size — likely a "
        "pack/unit confusion.",
        bad,
        "Confirm with a physical count before correcting; the fix is a movement, "
        "not an edit.",
    )


# ── C10. Reserved exceeds on-hand ─────────────────────────────────────────
def check_over_reservation(rows: list[dict]) -> Finding:
    bad = [{"irc": r.get("irc"), "ndc11": r.get("ndc11"), "lot_id": r.get("lot_id"),
            "on_hand": float(_f(r, "quantity_on_hand")),
            "reserved": float(_f(r, "quantity_reserved"))}
           for r in rows if _f(r, "quantity_reserved") > _f(r, "quantity_on_hand")]
    return Finding(
        "over_reserved", "high", len(bad),
        "رزرو بیش از موجودی",
        "More units are committed to un-dispensed fills than physically exist; the "
        "next patient will be promised stock that is not there.",
        bad,
        "Release reservations for cancelled fills, then count the item.",
    )


# ── C11. Chain integrity ──────────────────────────────────────────────────
def check_chain(verify_result: dict) -> Finding:
    intact = verify_result.get("intact", False)
    return Finding(
        "ledger_chain", "critical", 0 if intact else 1,
        "زنجیره تغییرناپذیری دفتر موجودی",
        ("Movement hash chain verified over "
         f"{verify_result.get('verified', 0)} rows.") if intact
        else f"Chain broken at index {verify_result.get('break_index')}: "
             f"{verify_result.get('detail')}",
        [] if intact else [verify_result],
        "" if intact else
        "Do not repair the chain. Preserve it, export the affected range, and "
        "escalate — a broken chain is evidence, and rewriting it destroys the "
        "only record of what happened.",
    )


ALL_CHECKS = ("aggregate_drift", "negative_stock", "fill_without_movement",
              "untraceable_fill", "unbound_from_formulary", "expired_on_hand",
              "suspicious_adjustment", "duplicate_lot", "unit_conversion_suspect",
              "over_reserved", "ledger_chain")


def summarize(findings: list[Finding]) -> dict:
    """Roll findings into a verdict.

    Three states rather than one overloaded flag, because "healthy" alone
    cannot distinguish "nothing is wrong" from "nothing serious is wrong", and
    a report that says healthy while a check is firing teaches people to stop
    reading it:

      healthy      — no check fired at all.
      trustworthy  — nothing critical or high; the quantities can be relied on
                     for ordering and dispensing decisions.
      blocking     — at least one critical; the ledger is provably wrong and a
                     human ruling is required before it is trusted.
    """
    by_sev = {s: 0 for s in SEVERITIES}
    for f in findings:
        if f.count:
            by_sev[f.severity] += 1
    firing = [f for f in findings if f.count]
    return {
        "healthy": len(firing) == 0,
        "trustworthy": by_sev["critical"] == 0 and by_sev["high"] == 0,
        "blocking": by_sev["critical"] > 0,
        "checks_run": len(findings),
        "checks_firing": len(firing),
        "by_severity": by_sev,
        "total_rows_affected": sum(f.count for f in findings),
        "findings": [f.as_dict() for f in sorted(
            findings, key=lambda f: (SEVERITIES.index(f.severity), -f.count))],
    }
