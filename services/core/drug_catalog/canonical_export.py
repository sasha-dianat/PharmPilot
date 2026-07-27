"""X5 — the canonical bundle: PharmPilot's own updatable, versionable lists.

export_bundle writes data/canonical/:
  catalog.json            canonical products (overrides already applied in DB)
  formulary_<insurer>.json  latest snapshot resolved through the crosswalk —
                            each line carries its price/share AND the product it
                            maps to, so a new publication diffs against
                            decisions instead of re-matching from zero
  crosswalk.json          every confirmed/rejected mapping + who/when/why
  overrides.json          durable field corrections
  prices_current.json     current open price points (full history stays in DB)
  manifest.json           counts + sha256 of each file (tamper-evident)

import_decided re-seeds ONLY the decided layer (crosswalk + overrides) from a
bundle — observed data always re-enters through the normal import flows.
Deterministic, no LLM.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

BUNDLE_DIR = Path("data/canonical")


def _write(path: Path, payload) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                      default=str)
    path.write_text(body, encoding="utf-8")
    return {"file": path.name, "bytes": len(body.encode("utf-8")),
            "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]}


async def export_bundle(db, out_dir=BUNDLE_DIR) -> dict:
    from sqlalchemy import select
    from shared.models.crosswalk import CrosswalkEntry, FieldOverride
    from shared.models.drug_catalog import DrugCatalogItem
    from shared.models.formulary_snapshot import FormularySnapshot
    from shared.models.price_history import PriceHistory
    from .enrichment import enrich_key

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict = {"exported_at": datetime.now(timezone.utc).isoformat(),
                      "files": [], "counts": {}}

    # catalog — overrides are already applied in the DB rows
    items = (await db.execute(select(DrugCatalogItem))).scalars().all()
    catalog = [{
        "irc": i.irc, "name_fa": i.name_fa, "generic_name": i.generic_name,
        "ingredient_key": i.ingredient_key, "dosage_form": i.dosage_form,
        "strength": i.strength, "brand_name": i.brand_name,
        "manufacturer": i.manufacturer, "country": i.country, "atc": i.atc,
        "announced_price": int(i.announced_price) if i.announced_price else None,
        "coverage": i.coverage, "source": i.source,
    } for i in items]
    manifest["files"].append(_write(out_dir / "catalog.json", catalog))
    manifest["counts"]["catalog"] = len(catalog)

    # decided layer
    cw = (await db.execute(select(CrosswalkEntry))).scalars().all()
    crosswalk = [{
        "insurer": e.insurer, "source_code": e.source_code, "raw_key": e.raw_key,
        "raw_name": e.raw_name, "irc": e.irc, "status": e.status,
        "reason": e.reason,
        "decided_at": e.decided_at.isoformat() if e.decided_at else None,
    } for e in cw]
    manifest["files"].append(_write(out_dir / "crosswalk.json", crosswalk))
    manifest["counts"]["crosswalk"] = len(crosswalk)

    ov = (await db.execute(select(FieldOverride))).scalars().all()
    overrides = [{"irc": o.irc, "field": o.field, "value": o.value,
                  "reason": o.reason,
                  "decided_at": o.decided_at.isoformat() if o.decided_at else None}
                 for o in ov]
    manifest["files"].append(_write(out_dir / "overrides.json", overrides))
    manifest["counts"]["overrides"] = len(overrides)

    # formularies — latest snapshot per insurer, resolved through the crosswalk
    cw_lookup: dict[str, dict] = {}
    for e in cw:
        if e.source_code:
            cw_lookup[f"{e.insurer}|code:{e.source_code}"] = {"irc": e.irc, "status": e.status}
        cw_lookup[f"{e.insurer}|name:{e.raw_key}"] = {"irc": e.irc, "status": e.status}

    insurers = [r for (r,) in (await db.execute(
        select(FormularySnapshot.insurer).distinct())).all()]
    for ins in insurers:
        latest_run = (await db.execute(
            select(FormularySnapshot.run_id)
            .where(FormularySnapshot.insurer == ins)
            .order_by(FormularySnapshot.created_at.desc()).limit(1))).scalar()
        snaps = (await db.execute(
            select(FormularySnapshot)
            .where(FormularySnapshot.insurer == ins,
                   FormularySnapshot.run_id == latest_run))).scalars().all()
        rows = []
        resolved = 0
        for s in snaps:
            d = (cw_lookup.get(f"{ins}|code:{s.source_code}") if s.source_code else None) \
                or cw_lookup.get(f"{ins}|name:{enrich_key(s.raw_name or '')}")
            if d and d.get("status") == "confirmed":
                resolved += 1
            rows.append({
                "source_code": s.source_code, "raw_name": s.raw_name,
                "reference_price": s.reference_price,
                "share_pct": float(s.share_pct) if s.share_pct is not None else None,
                "covered": s.covered,
                "product_irc": d.get("irc") if d and d.get("status") == "confirmed" else None,
                "decision": d.get("status") if d else None,
            })
        manifest["files"].append(_write(out_dir / f"formulary_{ins}.json", {
            "insurer": ins, "run_id": str(latest_run), "rows": rows}))
        manifest["counts"][f"formulary_{ins}"] = len(rows)
        manifest["counts"][f"formulary_{ins}_resolved"] = resolved

    # current prices (open rows only — history stays queryable in the DB)
    open_prices = (await db.execute(
        select(PriceHistory).where(PriceHistory.valid_to.is_(None)))).scalars().all()
    prices = [{"irc": p.irc, "type": p.price_type, "insurer": p.insurer,
               "value": p.value, "since": p.valid_from.isoformat() if p.valid_from else None,
               "source": p.source} for p in open_prices]
    manifest["files"].append(_write(out_dir / "prices_current.json", prices))
    manifest["counts"]["prices_current"] = len(prices)

    _write(out_dir / "manifest.json", manifest)
    return manifest


async def import_decided(db, in_dir=BUNDLE_DIR) -> dict:
    """Re-seed the DECIDED layer from a bundle (idempotent upserts). Observed
    data is not touched — it re-enters through the normal import flows."""
    from .crosswalk import record_decision, set_override

    in_dir = Path(in_dir)
    out = {"crosswalk": 0, "overrides": 0}
    cw_path, ov_path = in_dir / "crosswalk.json", in_dir / "overrides.json"
    if cw_path.exists():
        for e in json.loads(cw_path.read_text(encoding="utf-8")):
            if not isinstance(e, dict):
                continue
            r = await record_decision(
                db, insurer=e.get("insurer", ""), raw_name=e.get("raw_name") or e.get("raw_key", ""),
                irc=e.get("irc"), status=e.get("status", ""),
                source_code=e.get("source_code"), reason=e.get("reason"))
            if r in ("created", "updated"):
                out["crosswalk"] += 1
    if ov_path.exists():
        for o in json.loads(ov_path.read_text(encoding="utf-8")):
            if not isinstance(o, dict) or not o.get("irc") or not o.get("field"):
                continue
            await set_override(db, o["irc"], o["field"], o.get("value"),
                               reason=o.get("reason"))
            out["overrides"] += 1
    await db.commit()
    return out
