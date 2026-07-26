#!/usr/bin/env python3
"""Scan NFI monograph pages and keep the SOURCE of anything that looks wrong.

Why this exists: the catalog records what the parser understood, not what the
page actually said, and it never stored the page id — so a suspicious product
could not be taken back to its own /NFI/Detail/<id> page. Every question about a
bad row ("is the country missing on the page, or did we drop it?") was
unanswerable without a hand re-fetch.

This pass answers it in bulk. For each page it records the irc → page-id mapping
(which permanently fixes source links), runs the same completeness and coherence
checks the importer uses, and — for any page that fails one — saves the RAW HTML
so it can be read line by line afterwards.

Usage
  python3 scripts/nfi_page_audit.py --start 1 --end 5000 --proxy http://host:port
  python3 scripts/nfi_page_audit.py --failed --proxy http://host:port     # retry queue
  python3 scripts/nfi_page_audit.py --ids 212,4188 --proxy http://host:port

Output (logs/nfi_audit/)
  index.jsonl      one line per page: id, url, irc, name, flags, section presence
  irc_page_map.csv irc,page_id for EVERY page that parsed — the link table
  pages/<id>.html  raw source, saved only for flagged pages
Safe to interrupt and re-run: finished ids are skipped unless --force.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.core.drug_catalog.nfi import (  # noqa: E402
    fetch_detail_raw, make_opener, parse_detail)

OUT = Path("logs/nfi_audit")
PAGES = OUT / "pages"
INDEX = OUT / "index.jsonl"
IRCMAP = OUT / "irc_page_map.csv"


def page_flags(rec: dict, html: str) -> list[str]:
    """Everything about this page worth a human look. Section presence is
    recorded separately from field emptiness so we can tell "the page never
    said it" from "we failed to read it" — the distinction that decides whether
    re-crawling can help at all."""
    f = []
    if not rec:
        return ["unparsable"]
    if not rec.get("irc"):
        f.append("no_irc")
    if not rec.get("country"):
        f.append("no_country")
    if not (rec.get("announced_price") or rec.get("package_price")):
        f.append("no_price")
    if not rec.get("strength"):
        f.append("no_strength")
    if not rec.get("atc"):
        f.append("no_atc")
    if not rec.get("generic_name"):
        f.append("no_generic")
    if not rec.get("brands"):
        f.append("no_brands_table")          # country lives here — key signal
    if "محصولات مشابه" not in html:
        f.append("section_similar_absent")
    if "قیمت" not in html:
        f.append("section_price_absent")
    if rec.get("integrity", {}).get("spliced_page"):
        f.append("spliced")
    return f


def load_done() -> set[int]:
    done = set()
    if INDEX.exists():
        for line in INDEX.open(encoding="utf-8"):
            try:
                done.add(int(json.loads(line)["page_id"]))
            except Exception:
                continue
    return done


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--end", type=int, default=0)
    ap.add_argument("--ids", type=str, default="")
    ap.add_argument("--failed", action="store_true",
                    help="use the harvest_failures retry queue")
    ap.add_argument("--proxy", type=str, default=os.getenv("HTTPS_PROXY"))
    ap.add_argument("--delay", type=float, default=0.0)
    ap.add_argument("--save-all", action="store_true",
                    help="keep HTML for every page, not just flagged ones")
    ap.add_argument("--force", action="store_true", help="re-scan finished ids")
    a = ap.parse_args()

    if a.failed:
        import asyncio
        from services.core.drug_catalog.nfi_harvest_service import pending_failures
        ids = asyncio.get_event_loop().run_until_complete(pending_failures("nfi"))
    elif a.ids:
        ids = [int(x) for x in a.ids.replace(",", " ").split()]
    else:
        if not a.end:
            ap.error("give --end, --ids or --failed")
        ids = list(range(a.start, a.end + 1))

    OUT.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(exist_ok=True)
    done = set() if a.force else load_done()
    todo = [i for i in ids if i not in done]
    print(f"pages to scan: {len(todo):,} (skipping {len(ids) - len(todo):,} already done)")
    if not todo:
        return 0

    opener = make_opener(a.proxy)
    idx = INDEX.open("a", encoding="utf-8")
    mp = IRCMAP.open("a", newline="", encoding="utf-8")
    wr = csv.writer(mp)
    if IRCMAP.stat().st_size == 0:
        wr.writerow(["irc", "page_id", "name_fa"])

    ok = flagged = failed = 0
    t0 = time.time()
    for n, pid in enumerate(todo, 1):
        try:
            status, body, _hdrs = fetch_detail_raw(pid, opener)
        except Exception as e:
            idx.write(json.dumps({"page_id": pid, "error": type(e).__name__}) + "\n")
            failed += 1
            continue
        html = body.decode("utf-8", "replace") if body else ""
        if status != 200 or not html:
            idx.write(json.dumps({"page_id": pid, "http": status}) + "\n")
            failed += 1
            continue
        rec = parse_detail(html, pid) or {}
        flags = page_flags(rec, html)
        row = {
            "page_id": pid,
            "url": f"https://irc.fda.gov.ir/NFI/Detail/{pid}",
            "irc": rec.get("irc"), "name_fa": rec.get("name_fa"),
            "generic": rec.get("generic_name"), "manufacturer": rec.get("manufacturer"),
            "country": rec.get("country"), "price": rec.get("announced_price"),
            "strength": rec.get("strength"), "atc": rec.get("atc"),
            "licence_until": rec.get("license_valid_until"),
            "flags": flags, "html_bytes": len(html),
        }
        idx.write(json.dumps(row, ensure_ascii=False) + "\n")
        if rec.get("irc"):
            wr.writerow([rec["irc"], pid, rec.get("name_fa") or ""])
        if flags or a.save_all:
            (PAGES / f"{pid}.html").write_text(html, encoding="utf-8")
            flagged += 1
        ok += 1
        if n % 200 == 0:
            idx.flush(); mp.flush()
            el = time.time() - t0
            print(f"  {n:,}/{len(todo):,}  ok={ok:,} flagged={flagged:,} failed={failed:,} "
                  f"· {el/max(n,1):.2f}s/page · eta {(len(todo)-n)*el/max(n,1)/60:.0f}m")
        if a.delay:
            time.sleep(a.delay)

    idx.close(); mp.close()
    print(f"\ndone: {ok:,} parsed · {flagged:,} flagged (HTML saved) · {failed:,} unreachable")
    print(f"index      {INDEX}")
    print(f"irc→page   {IRCMAP}")
    print(f"saved HTML {PAGES}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
