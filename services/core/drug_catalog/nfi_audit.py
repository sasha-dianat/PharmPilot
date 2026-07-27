"""Page-audit output: flags, raw-source capture, and the irc → page-id map.

Shared by the CLI (scripts/nfi_page_audit.py) and the harvest service's audit
mode, so the two can never disagree about what counts as a suspicious page.

An audit pass answers a question the catalog cannot: whether a field is missing
because the SOURCE never stated it, or because we failed to read it. That is why
section presence is recorded separately from field emptiness — it is what proved
country unrecoverable by re-crawling (country exists iff the محصولات مشابه table
does) and what showed missing prices were mostly lapsed registrations instead.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

OUT = Path("logs/nfi_audit")
PAGES = OUT / "pages"
INDEX = OUT / "index.jsonl"
IRCMAP = OUT / "irc_page_map.csv"


def page_flags(rec: dict, html: str) -> list[str]:
    """Everything about this page worth a human look."""
    f: list[str] = []
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
        f.append("no_brands_table")           # country lives here
    if "محصولات مشابه" not in html:
        f.append("section_similar_absent")    # source truly lacks it
    if "قیمت" not in html:
        f.append("section_price_absent")
    if (rec.get("integrity") or {}).get("spliced_page"):
        f.append("spliced")
    return f


def ensure_dirs() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(exist_ok=True)


def load_done() -> set[int]:
    """Ids that actually produced a page. A FAILED fetch is not 'done' — it is
    exactly what a re-run exists to retry."""
    done: set[int] = set()
    if not INDEX.exists():
        return done
    for line in INDEX.open(encoding="utf-8"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("error") or d.get("http"):
            continue
        try:
            done.add(int(d["page_id"]))
        except Exception:
            continue
    return done


def row_for(page_id: int, rec: dict, html: str) -> dict:
    return {
        "page_id": page_id,
        "url": f"https://irc.fda.gov.ir/NFI/Detail/{page_id}",
        "irc": rec.get("irc"), "name_fa": rec.get("name_fa"),
        "generic": rec.get("generic_name"), "manufacturer": rec.get("manufacturer"),
        "country": rec.get("country"), "price": rec.get("announced_price"),
        "strength": rec.get("strength"), "atc": rec.get("atc"),
        "licence_until": rec.get("license_valid_until"),
        "flags": page_flags(rec, html), "html_bytes": len(html),
    }


def write_page(page_id: int, rec: dict, html: str, *, save_all: bool = False) -> bool:
    """Append the index row, extend the irc→page map, and keep the raw source
    when the page is flagged. Returns True when HTML was saved."""
    ensure_dirs()
    row = row_for(page_id, rec, html)
    with INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if rec.get("irc"):
        new = not IRCMAP.exists() or IRCMAP.stat().st_size == 0
        with IRCMAP.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["irc", "page_id", "name_fa"])
            w.writerow([rec["irc"], page_id, rec.get("name_fa") or ""])
    if row["flags"] or save_all:
        (PAGES / f"{page_id}.html").write_text(html, encoding="utf-8")
        return True
    return False


def write_failure(page_id: int, *, error: str | None = None,
                  http: int | None = None) -> None:
    ensure_dirs()
    with INDEX.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"page_id": page_id, "error": error, "http": http}) + "\n")


def summary() -> dict:
    """Flag histogram over everything audited so far — the analysis surface."""
    counts: dict[str, int] = {}
    pages = flagged = failed = 0
    if INDEX.exists():
        for line in INDEX.open(encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("error") or d.get("http"):
                failed += 1
                continue
            pages += 1
            fl = d.get("flags") or []
            if fl:
                flagged += 1
            for f in fl:
                counts[f] = counts.get(f, 0) + 1
    return {"pages": pages, "flagged": flagged, "failed": failed,
            "by_flag": dict(sorted(counts.items(), key=lambda x: -x[1])),
            "index": str(INDEX), "irc_map": str(IRCMAP)}
