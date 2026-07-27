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
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.core.drug_catalog.nfi import (  # noqa: E402
    fetch_detail_raw, make_opener, parse_detail)
# The flag rules, the index format and the "what counts as done" rule live in
# ONE place — the GUI's audit mode runs the same code, so a page flagged here is
# flagged there too.
from services.core.drug_catalog import nfi_audit  # noqa: E402
from services.core.drug_catalog.nfi_audit import (  # noqa: E402
    INDEX, IRCMAP, PAGES, load_done)


def preflight(opener, proxy: str | None) -> str | None:
    """Fetch one known-good page before scanning thousands. Returns an error
    string to abort on. A placeholder proxy («http://host:port») previously
    produced 5,000 identical InvalidURL failures before anyone found out."""
    if proxy and ("host:port" in proxy or "://" not in proxy):
        return (f"پروکسی نامعتبر است: {proxy!r} — نشانی واقعی را بدهید "
                f"(مثال: http://127.0.0.1:8086)")
    try:
        status, body, _ = fetch_detail_raw(212, opener)   # the ranitidine page
    except Exception as e:
        return (f"اتصال برقرار نشد ({type(e).__name__}: {str(e)[:80]}) — "
                f"پروکسی ایران لازم است؛ دسترسی مستقیم به irc.fda.gov.ir قطع است.")
    if status != 200 or not body:
        return f"صفحهٔ آزمایشی HTTP {status} برگرداند — پروکسی یا مقصد در دسترس نیست."
    return None


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

    nfi_audit.ensure_dirs()
    done = set() if a.force else load_done()
    todo = [i for i in ids if i not in done]
    print(f"pages to scan: {len(todo):,} (skipping {len(ids) - len(todo):,} already done)")
    if not todo:
        return 0

    opener = make_opener(a.proxy)
    err = preflight(opener, a.proxy)
    if err:
        print(f"\n✗ {err}\n  هیچ صفحه‌ای پویش نشد.", file=sys.stderr)
        return 2
    print("✓ اتصال آزمایشی موفق — شروع پویش")

    ok = flagged = failed = streak = 0
    t0 = time.time()
    for n, pid in enumerate(todo, 1):
        if streak >= 100:                     # the link died mid-run; stop early
            print(f"\n✗ {streak} خطای پیاپی — پویش متوقف شد (اتصال قطع شده).",
                  file=sys.stderr)
            break
        try:
            status, body, _hdrs = fetch_detail_raw(pid, opener)
        except Exception as e:
            nfi_audit.write_failure(pid, error=type(e).__name__)
            failed += 1; streak += 1
            continue
        html = body.decode("utf-8", "replace") if body else ""
        if status != 200 or not html:
            nfi_audit.write_failure(pid, http=status)
            failed += 1; streak += 1
            continue
        rec = parse_detail(html, pid) or {}
        if nfi_audit.write_page(pid, rec, html, save_all=a.save_all):
            flagged += 1
        ok += 1; streak = 0
        if n % 200 == 0:
            el = time.time() - t0
            print(f"  {n:,}/{len(todo):,}  ok={ok:,} flagged={flagged:,} failed={failed:,} "
                  f"· {el/max(n,1):.2f}s/page · eta {(len(todo)-n)*el/max(n,1)/60:.0f}m")
        if a.delay:
            time.sleep(a.delay)

    print(f"\ndone: {ok:,} parsed · {flagged:,} flagged (HTML saved) · {failed:,} unreachable")
    print(f"index      {INDEX}")
    print(f"irc→page   {IRCMAP}")
    print(f"saved HTML {PAGES}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
