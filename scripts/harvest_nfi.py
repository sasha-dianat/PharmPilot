#!/usr/bin/env python3
"""Harvest the NFI drug list (irc.fda.gov.ir/nfi) into a catalog JSON feed.

The NFI has no bulk download — it's a search SPA over a backend API, reachable
only from Iranian IPs. Run this THROUGH an Iran proxy/VPN:

    HTTPS_PROXY=http://user:pass@host:port python scripts/harvest_nfi.py discover
    HTTPS_PROXY=... python scripts/harvest_nfi.py harvest --out nfi_catalog.json

Modes
-----
discover   Fetch the SPA + its JS bundles and print every backend API path the
           app itself calls (regex over the bundle source). Use this once to
           confirm/adjust the search endpoint, then run harvest.
harvest    Page through the search API (Persian + Latin alphabet probes, then
           two-letter refinements for full coverage), dedupe by IRC, and write
           a JSON list our importer understands:
             [{irc, name_fa, generic_name, strength, dosage_form,
               announced_price, brand_name, manufacturer, gtin, category}, ...]
           Ingest with:  POST /api/v1/pricing/catalog/import (as .json→convert)
           or:  python scripts/harvest_nfi.py ingest --feed nfi_catalog.json

Only stdlib is required (urllib honors HTTP(S)_PROXY). Be polite: --delay 0.4s
default between requests; NFI is a public national service.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://irc.fda.gov.ir"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0 Safari/537.36")

# Candidate search endpoints (checked in order until one answers with JSON).
# `discover` mode prints the real ones straight from the app bundle; adjust
# --endpoint if NFI ships a different path.
CANDIDATE_ENDPOINTS = [
    "/nfi/api/searchDrug?term={q}&page={page}",
    "/nfi/api/Search?term={q}&pageNumber={page}",
    "/api/nfi/search?query={q}&page={page}",
    "/nfi/Search/SearchDrug?term={q}&page={page}",
]

FA_LETTERS = list("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی")
EN_LETTERS = list("abcdefghijklmnopqrstuvwxyz")


def _get(url: str, timeout: int = 30) -> tuple[int, str, str]:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/json, text/html;q=0.8",
        "Accept-Language": "fa,en;q=0.8", "Referer": f"{BASE}/nfi",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp.headers.get("Content-Type", ""), body
    except urllib.error.HTTPError as e:
        return e.code, "", ""
    except Exception as e:
        print(f"  ! {type(e).__name__}: {e} — {url}", file=sys.stderr)
        return 0, "", ""


# ── discover ──────────────────────────────────────────────────────────────────
def discover() -> None:
    print(f"fetching {BASE}/nfi …")
    status, ctype, html = _get(f"{BASE}/nfi")
    print(f"  status={status} type={ctype} bytes={len(html)}")
    if status != 200:
        print("Cannot reach NFI — check your Iran proxy (HTTPS_PROXY).", file=sys.stderr)
        sys.exit(2)

    bundles = re.findall(r'src=["\']([^"\']+\.js[^"\']*)["\']', html)
    print(f"  {len(bundles)} script bundles")
    api_paths: set[str] = set()
    for b in bundles:
        url = b if b.startswith("http") else urllib.parse.urljoin(BASE, b)
        s, _, js = _get(url)
        if s != 200:
            continue
        # anything that looks like an app API route
        for m in re.findall(r'["\'](/?[A-Za-z0-9_./-]*(?:api|Api|API|Search|search|Drug|drug)[A-Za-z0-9_./?=&{}-]*)["\']', js):
            if len(m) > 4 and not m.endswith((".js", ".css", ".png", ".svg")):
                api_paths.add(m)
        print(f"  scanned {url.rsplit('/',1)[-1][:60]} ({len(js)//1024} KB)")
    print("\nAPI-looking paths found in the app bundle:")
    for p in sorted(api_paths):
        print("   ", p)
    print("\nPick the search endpoint and run:\n"
          "  python scripts/harvest_nfi.py harvest --endpoint '<path with {q} and {page}>'")


# ── harvest ───────────────────────────────────────────────────────────────────
def _extract_items(payload) -> list[dict]:
    """NFI responses vary; accept a bare list or common wrapper keys."""
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ("data", "items", "results", "result", "drugs", "list"):
            v = payload.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
            if isinstance(v, dict):
                inner = _extract_items(v)
                if inner:
                    return inner
    return []


# Map NFI item fields (English/Persian API variants) → our importer's aliases.
_FIELD_MAP = {
    "irc": ("irc", "IRC", "ircCode", "irc_code", "productCode", "code"),
    "name_fa": ("persianName", "faName", "nameFa", "persian_name", "title", "name"),
    "generic_name": ("genericName", "genericNameEn", "englishName", "enName", "generic"),
    "strength": ("strength", "dose", "dosage"),
    "dosage_form": ("dosageForm", "form", "shape"),
    "announced_price": ("price", "consumerPrice", "consumer_price", "publicPrice"),
    "brand_name": ("brandName", "brand"),
    "manufacturer": ("companyName", "producer", "manufacturer", "licenseOwner"),
    "gtin": ("gtin", "GTIN", "barcode"),
    "category": ("productType", "category", "type"),
}


def _map_item(item: dict) -> dict | None:
    out = {}
    for ours, theirs in _FIELD_MAP.items():
        for k in theirs:
            if k in item and item[k] not in (None, ""):
                out[ours] = item[k]
                break
    if not out.get("irc"):
        return None
    out.setdefault("name_fa", out.get("generic_name", ""))
    return out


def harvest(endpoint: str | None, out_path: str, delay: float, max_pages: int) -> None:
    endpoints = [endpoint] if endpoint else CANDIDATE_ENDPOINTS
    live = None
    for ep in endpoints:
        url = urllib.parse.urljoin(BASE, ep.format(q=urllib.parse.quote("ا"), page=1))
        s, ctype, body = _get(url)
        if s == 200 and ("json" in ctype or body.lstrip().startswith(("{", "["))):
            live = ep
            print(f"using endpoint: {ep}")
            break
        print(f"  candidate dead ({s}): {ep}")
    if not live:
        print("No candidate endpoint answered with JSON. Run `discover` and pass "
              "--endpoint with the real path.", file=sys.stderr)
        sys.exit(2)

    seen: dict[str, dict] = {}
    queries = FA_LETTERS + EN_LETTERS
    qi = 0
    while qi < len(queries):
        q = queries[qi]
        qi += 1
        page, q_total = 1, 0
        while page <= max_pages:
            url = urllib.parse.urljoin(BASE, live.format(q=urllib.parse.quote(q), page=page))
            s, _, body = _get(url)
            if s != 200:
                break
            try:
                payload = json.loads(body)
            except json.JSONDecodeError:
                break
            items = _extract_items(payload)
            if not items:
                break
            fresh = 0
            for raw in items:
                m = _map_item(raw)
                if m and m["irc"] not in seen:
                    seen[m["irc"]] = m
                    fresh += 1
            q_total += len(items)
            print(f"  q='{q}' page={page}: {len(items)} items ({fresh} new, total {len(seen)})")
            page += 1
            time.sleep(delay)
        # If a single-letter query hit the page cap, refine with two-letter queries
        if page > max_pages and len(q) == 1:
            queries.extend(q + c for c in (FA_LETTERS if q in FA_LETTERS else EN_LETTERS))

    rows = list(seen.values())
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)
    print(f"\nwrote {len(rows)} unique items → {out_path}")
    print("Ingest with: python scripts/harvest_nfi.py ingest --feed", out_path)


# ── ingest (local, no proxy needed) ───────────────────────────────────────────
def ingest(feed_path: str) -> None:
    import asyncio
    import os
    sys.path.insert(0, ".")
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from services.core.drug_catalog.importer import build_records, upsert_catalog

    rows = json.load(open(feed_path, encoding="utf-8"))
    records = build_records(rows)
    print(f"{len(rows)} rows → {len(records)} valid records")

    async def run():
        eng = create_async_engine(os.environ["DATABASE_URL"])
        Session = async_sessionmaker(eng, expire_on_commit=False)
        async with Session() as s:
            n = await upsert_catalog(s, records, source="nfi-harvest")
        await eng.dispose()
        print(f"upserted {n} catalog items")

    asyncio.run(run())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)
    sub.add_parser("discover", help="find the NFI backend API paths")
    h = sub.add_parser("harvest", help="paginate the search API into a JSON feed")
    h.add_argument("--endpoint", help="search path with {q} and {page} placeholders")
    h.add_argument("--out", default="nfi_catalog.json")
    h.add_argument("--delay", type=float, default=0.4)
    h.add_argument("--max-pages", type=int, default=50)
    i = sub.add_parser("ingest", help="load a harvested feed into the drug catalog")
    i.add_argument("--feed", required=True)
    args = ap.parse_args()

    if args.mode == "discover":
        discover()
    elif args.mode == "harvest":
        harvest(args.endpoint, args.out, args.delay, args.max_pages)
    else:
        ingest(args.feed)


if __name__ == "__main__":
    main()
