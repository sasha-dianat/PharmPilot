#!/usr/bin/env python3
"""
FDA NDC Drug Catalog Seeder
============================
Downloads FDA NDC Directory (free/public) and seeds the drug_products table.
Uses openFDA API for core data + DEA scheduling crosswalk.

Sources (all public, no API key required):
  - openFDA Drug NDC API: https://api.fda.gov/drug/ndc.json
  - openFDA Drug Label API: https://api.fda.gov/drug/label.json (for rxcui/GPI)

Run: python scripts/seed_fda_ndc.py [--limit N] [--pharmacy-id UUID]
     --limit: max records to seed (default: 80000)
     --pharmacy-id: if provided, also creates StockLevel rows

Idempotent: safe to re-run (uses INSERT ... ON CONFLICT DO NOTHING)
"""
import argparse
import asyncio
import hashlib
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional
from uuid import uuid4

import asyncpg
import httpx

# ── Config ──────────────────────────────────────────────────────────────────
DATABASE_URL = "postgresql://pharmpilot:pharmpilot_dev@127.0.0.1:5433/pharmpilot"
FDA_NDC_API  = "https://api.fda.gov/drug/ndc.json"
OPENFDA_LIMIT_PER_PAGE = 1000  # max openFDA allows
REQUEST_DELAY = 0.35           # seconds between API calls (rate-limit safe)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ndc-seeder")

# DEA schedule keyword map (derived from FDA marketing category + drug class)
DEA_SCHEDULE_KEYWORDS = {
    "CII":  ["schedule ii", "schedule 2", "cii", "morphine", "oxycodone", "hydrocodone",
             "fentanyl", "amphetamine", "methylphenidate", "methadone (pain)", "cocaine"],
    "CIII": ["schedule iii", "schedule 3", "ciii", "buprenorphine", "ketamine",
             "anabolic steroid", "testosterone"],
    "CIV":  ["schedule iv", "schedule 4", "civ", "benzodiazepine", "alprazolam",
             "diazepam", "clonazepam", "lorazepam", "zolpidem", "tramadol", "carisoprodol"],
    "CV":   ["schedule v", "schedule 5", "cv", "pregabalin", "lacosamide",
             "cough preparation", "codeine combination"],
}

# Known controlled drug names for schedule inference
CONTROLLED_BY_NAME: dict[str, str] = {
    # CII
    "oxycodone": "CII", "oxymorphone": "CII", "hydromorphone": "CII",
    "morphine": "CII", "fentanyl": "CII", "hydrocodone": "CII",
    "methadone": "CII", "meperidine": "CII", "codeine": "CII",
    "amphetamine": "CII", "dextroamphetamine": "CII", "lisdexamfetamine": "CII",
    "methylphenidate": "CII", "dexmethylphenidate": "CII",
    "adderall": "CII", "ritalin": "CII", "concerta": "CII",
    "oxycontin": "CII", "percocet": "CII", "vicodin": "CII",
    "dilaudid": "CII", "duragesic": "CII", "vyvanse": "CII",
    "cocaine": "CII", "phencyclidine": "CII",
    # CIII
    "buprenorphine": "CIII", "suboxone": "CIII", "subutex": "CIII",
    "ketamine": "CIII", "testosterone": "CIII", "anavar": "CIII",
    "nandrolone": "CIII", "oxandrolone": "CIII",
    # CIV
    "alprazolam": "CIV", "diazepam": "CIV", "clonazepam": "CIV",
    "lorazepam": "CIV", "oxazepam": "CIV", "temazepam": "CIV",
    "triazolam": "CIV", "chlordiazepoxide": "CIV",
    "zolpidem": "CIV", "zaleplon": "CIV", "eszopiclone": "CIV",
    "tramadol": "CIV", "carisoprodol": "CIV", "butorphanol": "CIV",
    "phenobarbital": "CIV", "pregabalin": "CIV",
    "xanax": "CIV", "valium": "CIV", "klonopin": "CIV",
    "ativan": "CIV", "ambien": "CIV", "lunesta": "CIV", "lyrica": "CIV",
    # CV
    "lacosamide": "CV", "clobazam": "CV", "ezogabine": "CV",
    "codeine/acetaminophen": "CV",
}

# NIOSH hazardous drug keywords (Table 1 — antineoplastics etc.)
HAZARDOUS_KEYWORDS = [
    "methotrexate", "cyclophosphamide", "doxorubicin", "vincristine",
    "paclitaxel", "docetaxel", "carboplatin", "cisplatin", "fluorouracil",
    "capecitabine", "imatinib", "erlotinib", "gefitinib", "irinotecan",
    "temozolomide", "bleomycin", "busulfan", "chlorambucil", "melphalan",
    "thalidomide", "lenalidomide", "pomalidomide", "azathioprine",
    "mycophenolate", "tacrolimus", "sirolimus", "finasteride", "dutasteride",
    "misoprostol", "mifepristone", "ribavirin", "ganciclovir", "valganciclovir",
    "interferon", "aldesleukin", "bortezomib", "carfilzomib",
]

REFRIGERATED_KEYWORDS = [
    "insulin", "adalimumab", "etanercept", "infliximab", "rituximab",
    "trastuzumab", "bevacizumab", "cetuximab", "ranibizumab", "aflibercept",
    "epoetin", "filgrastim", "pegfilgrastim", "sargramostim",
    "vaccine", "antitoxin", "immunoglobulin", "albumin",
    "latanoprost", "bimatoprost",
]


def infer_dea_schedule(generic_name: str, brand_name: str) -> Optional[str]:
    """Infer DEA schedule from drug name. Returns None if not controlled."""
    name_lower = (generic_name + " " + (brand_name or "")).lower()
    for drug_name, schedule in CONTROLLED_BY_NAME.items():
        if drug_name in name_lower:
            return schedule
    return None


def is_hazardous(generic_name: str) -> bool:
    name_lower = generic_name.lower()
    return any(kw in name_lower for kw in HAZARDOUS_KEYWORDS)


def is_refrigerated(generic_name: str) -> bool:
    name_lower = generic_name.lower()
    return any(kw in name_lower for kw in REFRIGERATED_KEYWORDS)


def normalize_ndc11(ndc: str) -> Optional[str]:
    """Convert any NDC format to 11-digit zero-padded (5-4-2 format, no dashes)."""
    if not ndc:
        return None
    ndc_clean = ndc.replace("-", "").replace(" ", "")
    if len(ndc_clean) == 11:
        return ndc_clean
    if len(ndc_clean) == 10:
        # Try all three pad positions: 5-3-2 → 5-4-2, 4-4-2 → 5-4-2, 5-4-1 → 5-4-2
        parts = ndc.split("-")
        if len(parts) == 3:
            p1, p2, p3 = parts
            if len(p1) == 4:
                return f"0{p1}{p2}{p3}"
            elif len(p2) == 3:
                return f"{p1}0{p2}{p3}"
            elif len(p3) == 1:
                return f"{p1}{p2}0{p3}"
        # Fallback: pad front
        return "0" + ndc_clean
    return None


def ndc10_from_ndc11(ndc11: str) -> Optional[str]:
    """Derive NDC-10 (no dashes) from NDC-11."""
    if len(ndc11) == 11:
        # Remove leading zero from labeler segment
        return ndc11[1:] if ndc11[0] == "0" else None
    return None


async def fetch_fda_ndc_page(
    client: httpx.AsyncClient,
    skip: int,
    limit: int = 1000,
) -> list[dict]:
    """Fetch one page of FDA NDC data."""
    params = {
        "limit": min(limit, 1000),
        "skip": skip,
        "search": 'product_type:"HUMAN PRESCRIPTION DRUG"',
    }
    for attempt in range(3):
        try:
            resp = await client.get(FDA_NDC_API, params=params, timeout=30.0)
            if resp.status_code == 200:
                data = resp.json()
                return data.get("results", [])
            elif resp.status_code == 404:
                return []  # No more data
            elif resp.status_code == 429:
                log.warning("Rate limited, backing off 5s…")
                await asyncio.sleep(5)
        except Exception as e:
            log.warning("Fetch attempt %d failed: %s", attempt + 1, e)
            await asyncio.sleep(2 ** attempt)
    return []


def parse_fda_record(record: dict) -> Optional[dict]:
    """Convert openFDA NDC API record → DrugProduct row dict."""
    openfda = record.get("openfda", {})

    # Build 11-digit NDC
    raw_ndc = record.get("product_ndc", "")
    pkg_ndcs = [pkg.get("package_ndc", "") for pkg in record.get("packaging", [{}])]

    # Use package NDC preferentially (has all 3 segments)
    ndc11 = None
    for pndc in pkg_ndcs:
        n = normalize_ndc11(pndc)
        if n:
            ndc11 = n
            break
    if not ndc11:
        ndc11 = normalize_ndc11(raw_ndc)
    if not ndc11:
        return None

    generic_name = (
        record.get("generic_name")
        or (openfda.get("substance_name", [""])[0])
        or record.get("brand_name", "")
    )
    if not generic_name:
        return None

    brand_name    = record.get("brand_name") or openfda.get("brand_name", [None])[0]
    labeler_name  = record.get("labeler_name") or openfda.get("manufacturer_name", [None])[0]
    dosage_form   = record.get("dosage_form", "")
    route         = ", ".join(record.get("route", []))
    strength      = record.get("active_ingredients", [{}])[0].get("strength", "") if record.get("active_ingredients") else ""

    # Package info
    pkg = (record.get("packaging") or [{}])[0]
    pkg_description = pkg.get("description", "")
    pkg_quantity: Optional[float] = None
    try:
        import re
        qty_match = re.search(r"(\d+(?:\.\d+)?)", pkg_description)
        if qty_match:
            pkg_quantity = float(qty_match.group(1))
    except Exception:
        pass

    rxcui_list  = openfda.get("rxcui", [])
    rxcui       = rxcui_list[0] if rxcui_list else None

    dea_schedule = infer_dea_schedule(generic_name, brand_name or "")
    is_controlled_ = dea_schedule is not None
    is_hazardous_  = is_hazardous(generic_name)
    is_refrigerated_ = is_refrigerated(generic_name)

    marketing_status = record.get("marketing_status", "")
    is_otc_ = "otc" in marketing_status.lower() or record.get("product_type", "") == "OTC"
    is_generic_ = brand_name is None or brand_name.upper() == generic_name.upper()

    return {
        "id": str(uuid4()),
        "ndc11": ndc11,
        "ndc10": ndc10_from_ndc11(ndc11),
        "brand_name": brand_name,
        "generic_name": generic_name[:255],
        "labeler_name": (labeler_name or "")[:255],
        "strength": strength[:100] if strength else None,
        "dosage_form": dosage_form[:100] if dosage_form else None,
        "route": route[:100] if route else None,
        "package_size": pkg_description[:100] if pkg_description else None,
        "package_quantity": pkg_quantity,
        "gpi": None,         # Not available from openFDA — FDB provides this
        "rxcui": rxcui,
        "dea_schedule": dea_schedule,
        "is_controlled": is_controlled_,
        "is_hazardous": is_hazardous_,
        "requires_refrigeration": is_refrigerated_,
        "is_generic": is_generic_,
        "is_otc": is_otc_,
        "fdb_drug_id": None,
        "medspan_drug_id": None,
        "is_active": True,
        "discontinued": False,
        "drug_db_metadata": json.dumps({
            "source": "openfda_ndc",
            "marketing_status": marketing_status,
            "product_type": record.get("product_type", ""),
        }),
    }


INSERT_SQL = """
INSERT INTO drug_products (
    id, ndc11, ndc10, brand_name, generic_name, labeler_name,
    strength, dosage_form, route, package_size, package_quantity,
    gpi, rxcui, dea_schedule, is_controlled, is_hazardous,
    requires_refrigeration, is_generic, is_otc,
    fdb_drug_id, medspan_drug_id,
    is_active, discontinued, drug_db_metadata,
    created_at, updated_at
)
VALUES (
    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
    $12, $13, $14, $15, $16, $17, $18, $19, $20, $21,
    $22, $23, $24, $25, $26, NOW(), NOW()
)
ON CONFLICT (ndc11) DO NOTHING
"""

STOCK_INSERT_SQL = """
INSERT INTO stock_levels (
    id, pharmacy_id, ndc11, drug_product_id,
    quantity_on_hand, quantity_reserved, quantity_on_order,
    par_level_min, par_level_max, reorder_point,
    created_at, updated_at
)
VALUES ($1, $2, $3, $4, 0, 0, 0, 10, 100, 15, NOW(), NOW())
ON CONFLICT DO NOTHING
"""


async def bulk_insert(conn: asyncpg.Connection, rows: list[dict]) -> int:
    """Batch insert rows, return number inserted."""
    if not rows:
        return 0

    records = []
    for r in rows:
        records.append((
            r["id"], r["ndc11"], r["ndc10"], r["brand_name"], r["generic_name"],
            r["labeler_name"], r["strength"], r["dosage_form"], r["route"],
            r["package_size"], r["package_quantity"],
            r["gpi"], r["rxcui"], r["dea_schedule"],
            r["is_controlled"], r["is_hazardous"], r["requires_refrigeration"],
            r["is_generic"], r["is_otc"],
            r["fdb_drug_id"], r["medspan_drug_id"],
            r["is_active"], r["discontinued"], r["drug_db_metadata"],
        ))

    result = await conn.executemany(INSERT_SQL, records)
    return len(records)


async def seed_stock_levels(
    conn: asyncpg.Connection,
    pharmacy_id: str,
) -> int:
    """Create StockLevel rows for each drug at the given pharmacy."""
    # Get all drug_product ids that don't yet have a stock level at this pharmacy
    rows = await conn.fetch("""
        SELECT dp.id, dp.ndc11
        FROM drug_products dp
        WHERE NOT EXISTS (
            SELECT 1 FROM stock_levels sl
            WHERE sl.drug_product_id = dp.id AND sl.pharmacy_id = $1
        )
        LIMIT 10000
    """, pharmacy_id)

    if not rows:
        return 0

    stock_records = [(str(uuid4()), pharmacy_id, r["ndc11"], str(r["id"])) for r in rows]
    await conn.executemany(STOCK_INSERT_SQL, stock_records)
    return len(stock_records)


async def main(limit: int, pharmacy_id: Optional[str]):
    log.info("Connecting to database…")
    conn = await asyncpg.connect(DATABASE_URL)
    log.info("Connected. Starting FDA NDC download…")

    inserted_total = 0
    skip = 0
    batch_size = 200  # rows to insert per DB transaction
    pending: list[dict] = []

    async with httpx.AsyncClient(
        headers={"User-Agent": "PharmPilot/1.0 (research@pharmpilot.ai)"},
        follow_redirects=True,
    ) as client:
        while inserted_total < limit:
            page_size = min(OPENFDA_LIMIT_PER_PAGE, limit - inserted_total)
            log.info("Fetching FDA NDC page skip=%d limit=%d…", skip, page_size)

            records = await fetch_fda_ndc_page(client, skip=skip, limit=page_size)
            if not records:
                log.info("No more records from FDA API (skip=%d)", skip)
                break

            for rec in records:
                parsed = parse_fda_record(rec)
                if parsed:
                    pending.append(parsed)

                if len(pending) >= batch_size:
                    n = await bulk_insert(conn, pending)
                    inserted_total += n
                    log.info("Inserted batch → total: %d / %d", inserted_total, limit)
                    pending = []
                    await asyncio.sleep(0.05)

            skip += len(records)
            await asyncio.sleep(REQUEST_DELAY)

            if len(records) < page_size:
                log.info("FDA API returned fewer records than requested — reached end.")
                break

    # Flush remaining
    if pending:
        n = await bulk_insert(conn, pending)
        inserted_total += n

    log.info("✅ Drug catalog seeded: %d records inserted (new only).", inserted_total)

    # Verify counts
    total_drugs = await conn.fetchval("SELECT COUNT(*) FROM drug_products")
    controlled  = await conn.fetchval("SELECT COUNT(*) FROM drug_products WHERE is_controlled = TRUE")
    cii         = await conn.fetchval("SELECT COUNT(*) FROM drug_products WHERE dea_schedule = 'CII'")
    log.info("Database totals — drugs: %d | controlled: %d | CII: %d", total_drugs, controlled, cii)

    # Seed stock levels if pharmacy_id provided
    if pharmacy_id:
        log.info("Seeding stock levels for pharmacy %s…", pharmacy_id)
        stock_count = await seed_stock_levels(conn, pharmacy_id)
        log.info("✅ Stock levels created: %d", stock_count)

    await conn.close()

    # DONE CRITERION check
    print("\n" + "="*60)
    print("DONE CRITERION CHECK:")
    print(f"  drug_products count: {total_drugs}  (need > 10,000: {'✅' if total_drugs > 10000 else '❌'})")
    print(f"  controlled drugs:    {controlled}")
    print(f"  CII drugs:           {cii}          (need > 200: {'✅' if cii > 200 else '❌ (need more seeding)'})")
    print("="*60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed FDA NDC drug catalog")
    parser.add_argument("--limit",      type=int,  default=80000, help="Max records to fetch")
    parser.add_argument("--pharmacy-id", type=str, default=None,  help="Pharmacy UUID for stock levels")
    args = parser.parse_args()
    asyncio.run(main(args.limit, args.pharmacy_id))
