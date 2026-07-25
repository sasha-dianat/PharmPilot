"""ناسازگاری‌ها as a managed backlog instead of an endless list.

The problem this solves: the incompatibilities view reported ~45,000 item-issues
(19,303 products with no country, 12,946 price gaps, 6,595 with no price, 2,652
salamat + 986 tamin coverage rows needing attention…), re-derived from scratch on
every run, with no memory. Nothing could ever be marked "this is normal", so the
number never moved and gave the owner no idea what to do next.

Three ideas make it manageable:

1. ROOT CAUSES, NOT ROWS. 19,303 missing countries is ONE cause (the NFI crawl
   never captured country) with ONE action (re-crawl), not 19,303 tasks. Issues
   are grouped into a small set of causes, each carrying its own count.

2. A LANE PER CAUSE. Every cause is routed by how it can actually be closed —
   the platform fixes it, one owner ruling covers the whole group, it needs
   external research, it needs clinical judgement, or it is simply expected.
   The lane tells the owner where the effort belongs.

3. DISPOSITIONS THAT PERSIST. A ruling is written to issue_dispositions, so
   accepted-as-normal groups leave the OPEN list for good and the backlog
   converges. Same decided-layer discipline as crosswalk_entries and
   field_overrides — observed data may never overwrite an owner decision.

Deterministic and read-only: this module counts and classifies, it never edits
the catalog. Fixes are applied by the modules that own them (nfi_integrity,
crosswalk, enrichment).
"""
from __future__ import annotations

# ── lanes: how a cause can be closed ────────────────────────────────────────
LANE_AUTO = "auto"            # the platform can fix it now, deterministically
LANE_BULK = "bulk"            # one owner ruling covers the whole group
LANE_RESEARCH = "research"    # needs external facts (enrichment worklist)
LANE_JUDGEMENT = "judgement"  # genuine clinical / pricing call, row by row
LANE_EXPECTED = "expected"    # structurally normal — acknowledge, don't "fix"
LANE_BLOCKED = "blocked"      # needs something unavailable (proxy, key, file)

LANE_FA = {
    LANE_AUTO: "قابل رفع خودکار",
    LANE_BULK: "تصمیم گروهی",
    LANE_RESEARCH: "نیازمند پژوهش",
    LANE_JUDGEMENT: "نیازمند داوری تخصصی",
    LANE_EXPECTED: "طبیعی — پذیرش",
    LANE_BLOCKED: "مسدود",
}

DISPOSITIONS = ("accepted", "wont_fix", "resolved", "deferred")

# ── the causes ──────────────────────────────────────────────────────────────
# Each entry: lane, Persian title, why it happens, and the action that closes it.
CAUSES: dict[str, dict] = {
    "nfi_spliced": {
        "lane": LANE_AUTO, "title": "مونوگراف دوپاره در NFI",
        "why": "صفحهٔ مبدأ NFI بلوک محصول یک دارو را با مونوگراف داروی دیگر نشان می‌دهد.",
        "action": "ترمیم خودکار از رکورد سالم هم‌خانواده؛ نتیجه به‌صورت field_override ثبت می‌شود.",
        "route": "nfi_integrity",
    },
    "coverage_review_agrees": {
        "lane": LANE_BULK, "title": "تطبیق هم‌مولکول در انتظار تأیید",
        "why": "مادهٔ مؤثرهٔ ردیف با محصول پیشنهادی می‌خواند؛ فقط چون شاهد بین‌بیمه‌ای است در بازبینی نگه داشته شده.",
        "action": "«تأیید همه» — هر تأیید یک مدخل دائمی crosswalk می‌سازد.",
        "route": "run_review",
    },
    "coverage_review_conflict": {
        "lane": LANE_JUDGEMENT, "title": "اختلاف مادهٔ مؤثره یا قدرت",
        "why": "نام ردیف و محصول پیشنهادی بر سر مادهٔ مؤثره یا قدرت توافق ندارند.",
        "action": "بررسی موردی؛ رد با دلیل کدگذاری‌شده تا مدل تطبیق آموزش ببیند.",
        "route": "run_review",
    },
    "coverage_unmatched_absent": {
        "lane": LANE_EXPECTED, "title": "دارو در فهرست NFI موجود نیست",
        "why": "مادهٔ مؤثره در کل کاتالوگ NFI نیست — دارو در ایران عرضه نمی‌شود یا نامش در NFI متفاوت است.",
        "action": "پذیرش گروهی؛ در صورت نیاز افزودن هم‌ارز به جدول مترادف‌ها.",
        "route": "acknowledge",
    },
    "coverage_unmatched_bulk": {
        "lane": LANE_EXPECTED, "title": "مادهٔ اولیهٔ ترکیبی (فله)",
        "why": "مادهٔ اولیهٔ ساخت ترکیبی است، نه فرآوردهٔ نهایی — طبیعتاً IRC ندارد.",
        "action": "پذیرش گروهی؛ قیمت به‌عنوان مبنای ترکیب‌سازی نگه داشته می‌شود.",
        "route": "acknowledge",
    },
    "coverage_unmatched_device": {
        "lane": LANE_EXPECTED, "title": "تجهیز یا لوازم مصرفی",
        "why": "کالای پزشکی است نه دارو؛ در کاتالوگ دارویی جایی ندارد.",
        "action": "پذیرش گروهی؛ در آیندهٔ ماژول تجهیزات دسته‌بندی می‌شود.",
        "route": "acknowledge",
    },
    "coverage_unmatched_researchable": {
        "lane": LANE_RESEARCH, "title": "نام مبهم — نیازمند پژوهش",
        "why": "نام تجاری یا مخفف است و مادهٔ مؤثره از خودِ نام درنمی‌آید.",
        "action": "ارسال به «غنی‌سازی هوشمند» برای استخراج ماده/شکل/قدرت.",
        "route": "enrichment",
    },
    "nfi_missing_country": {
        "lane": LANE_BLOCKED, "title": "کشور سازنده نامشخص",
        "why": "خزش NFI ستون کشور را برنداشته بود؛ فقط با خزش کامل پر می‌شود.",
        "action": "خزش دوبارهٔ NFI با پروکسی ایران (پارسر اکنون کشور را ذخیره می‌کند).",
        "route": "nfi_harvest",
    },
    "nfi_missing_price": {
        "lane": LANE_BLOCKED, "title": "قیمت اعلامی ندارد",
        "why": "فرآورده بدون قیمت مصرف‌کننده در NFI (پروانهٔ منقضی یا عرضه‌نشده).",
        "action": "خزش دوباره؛ باقی‌مانده‌ها را می‌توان از مرجع بیمه قیمت‌گذاری کرد.",
        "route": "nfi_harvest",
    },
    "nfi_missing_atc": {
        "lane": LANE_RESEARCH, "title": "کد ATC ندارد",
        "why": "درخت ATC در صفحهٔ مبدأ نبود یا خوانده نشد.",
        "action": "خزش دوباره (اکنون atc_path کامل ذخیره می‌شود) یا تکمیل از غنی‌سازی.",
        "route": "nfi_harvest",
    },
    "nfi_missing_strength": {
        "lane": LANE_RESEARCH, "title": "قدرت نامشخص",
        "why": "ترکیبات در صفحهٔ مبدأ قدرت را اعلام نکرده بود.",
        "action": "استخراج از نام فرآورده یا غنی‌سازی؛ بر کلید هم‌مولکولی اثر دارد.",
        "route": "enrichment",
    },
    "price_gap_extreme": {
        "lane": LANE_JUDGEMENT, "title": "اختلاف شدید قیمت اعلامی و مرجع بیمه",
        "why": "مرجع بیمه بیش از ۱۰ برابر قیمت اعلامی است — قیمت کهنه، ردیف مبدأ خراب، یا تطبیق نادرست.",
        "action": "بررسی موردی؛ در صورت صحت، به‌روزرسانی قیمت از همان اجرا.",
        "route": "price_review",
    },
    "price_gap_moderate": {
        "lane": LANE_BULK, "title": "اختلاف متعارف قیمت",
        "why": "فاصلهٔ قیمت اعلامی و مرجع بیمه در دامنهٔ معمول تعدیل تعرفه است.",
        "action": "پذیرش گروهی یا به‌روزرسانی دسته‌ای قیمت‌ها از آخرین اجرا.",
        "route": "price_review",
    },
}


def cause_key(cause: str, scope: str = "*") -> str:
    """Subject key for a GROUP-level ruling ('*' = the whole cause)."""
    return f"{cause}|{scope}"


async def load_dispositions(db) -> dict[str, dict]:
    """{subject_key: {disposition, reason, decided_at}} for open/closed filtering."""
    from sqlalchemy import select
    from shared.models.issue_disposition import IssueDisposition
    out: dict[str, dict] = {}
    for d in (await db.execute(select(IssueDisposition))).scalars().all():
        out[f"{d.issue_type}|{d.subject_key}"] = {
            "disposition": d.disposition, "reason": d.reason,
            "decided_at": d.decided_at.isoformat() if d.decided_at else None}
    return out


async def set_disposition(db, *, issue_type: str, subject_key: str,
                          disposition: str, reason: str | None = None,
                          details: dict | None = None, staff_id=None) -> str:
    """Record (or re-record) the owner's ruling on a cause or a single subject."""
    from datetime import datetime, timezone
    from sqlalchemy import select
    from shared.models.issue_disposition import IssueDisposition
    if disposition not in DISPOSITIONS:
        raise ValueError(f"unknown disposition: {disposition}")
    row = (await db.execute(select(IssueDisposition).where(
        IssueDisposition.issue_type == issue_type,
        IssueDisposition.subject_key == subject_key))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        db.add(IssueDisposition(issue_type=issue_type, subject_key=subject_key,
                                disposition=disposition, reason=reason,
                                details=details, decided_by=staff_id,
                                decided_at=now))
        return "created"
    row.disposition, row.reason, row.details = disposition, reason, details
    row.decided_by, row.decided_at = staff_id, now
    return "updated"


# ── counting ────────────────────────────────────────────────────────────────
async def _nfi_counts(db) -> dict[str, int]:
    from sqlalchemy import text
    async def n(cond: str) -> int:
        return (await db.execute(text(
            f"SELECT count(*) FROM drug_catalog WHERE {cond}"))).scalar() or 0
    return {
        "nfi_spliced": await n("monograph->'integrity'->>'spliced_page'='true' "
                               "AND coalesce(monograph->'integrity'->>'repaired','')<>'true'"),
        "nfi_missing_country": await n("country IS NULL OR country=''"),
        "nfi_missing_price": await n("announced_price IS NULL OR announced_price=0"),
        "nfi_missing_atc": await n("atc IS NULL OR atc=''"),
        "nfi_missing_strength": await n("strength IS NULL OR strength=''"),
    }


async def _price_counts(db, extreme_pct: int = 1000) -> dict[str, int]:
    from sqlalchemy import text
    sql = """
      SELECT count(*) FROM drug_catalog dc
      WHERE dc.announced_price > 0 AND jsonb_typeof(dc.coverage)='object'
        AND EXISTS (SELECT 1 FROM jsonb_each(dc.coverage) e
              WHERE jsonb_typeof(e.value)='object'
                AND coalesce((e.value->>'reference_price')::numeric, 0) > 0
                AND abs((e.value->>'reference_price')::numeric - dc.announced_price)
                    {op} :pct/100.0 * dc.announced_price)"""
    extreme = (await db.execute(text(sql.format(op=">")),
                                {"pct": extreme_pct})).scalar() or 0
    any_gap = (await db.execute(text(sql.format(op=">")), {"pct": 50})).scalar() or 0
    return {"price_gap_extreme": extreme,
            "price_gap_moderate": max(0, any_gap - extreme)}


async def _coverage_counts(db) -> dict[str, int]:
    """Coverage-side causes from the latest parsed run of each insurer, split by
    WHY the row needs attention (agreement vs conflict vs which absence)."""
    import re
    from sqlalchemy import text
    from . import repo, structural_match as sm
    from .coverage_import import link_rows
    from .crosswalk import build_code_registry, load_crosswalk
    from .enrichment import load_approved

    BULK = re.compile(r"\bBULK\b|فله|ترکيبي|ترکیبی", re.I)
    DEV = re.compile(r"\bROLL|GAUZE|SYRINGE|CATHETER|BANDAGE|SET\b|CONTAINER|"
                     r"BAG\b|STRIP|GLOVE|MASK", re.I)
    catalog = await repo.fetch_all(db)
    vocab = sm.build_form_vocab(catalog)
    known = {c for r in catalog for c in sm.components(r.generic_name or "")}
    reg = await build_code_registry(db)

    out: dict[str, int] = {k: 0 for k in (
        "coverage_review_agrees", "coverage_review_conflict",
        "coverage_unmatched_absent", "coverage_unmatched_bulk",
        "coverage_unmatched_device", "coverage_unmatched_researchable")}
    for ins in ("salamat", "tamin"):
        rid = (await db.execute(text(
            "SELECT run_id FROM formulary_snapshots WHERE insurer=:i "
            "GROUP BY run_id ORDER BY max(created_at) DESC LIMIT 1"), {"i": ins})).scalar()
        if not rid:
            continue
        rows = [r for r in (await db.execute(text(
            "SELECT row FROM formulary_snapshots WHERE run_id=:r"),
            {"r": rid})).scalars().all() if isinstance(r, dict)]
        # The board must see the DECIDED layer, exactly as a real staging run
        # does — otherwise it keeps reporting rows the owner already confirmed
        # (786 "awaiting confirmation" against a real review queue of 52).
        for l in link_rows(rows, catalog, insurer=ins, code_registry=reg,
                           crosswalk=await load_crosswalk(db, ins),
                           enrichments=await load_approved(db)):
            nm = str(l.row.get("drug_name") or "")
            if l.matched:
                if l.confidence >= 0.75:
                    continue                              # applied, not an issue
                p = sm.parse_name(nm, vocab)
                agrees = any(sm.ingredient_agrees(g, c)
                             for g in (p["generics"] or [])
                             for c in sm.components(l.record.generic_name or ""))
                out["coverage_review_agrees" if agrees
                    else "coverage_review_conflict"] += 1
            elif BULK.search(nm):
                out["coverage_unmatched_bulk"] += 1
            elif DEV.search(nm):
                out["coverage_unmatched_device"] += 1
            else:
                p = sm.parse_name(nm, vocab)
                if p["generics"] and p["generics"][0] not in known:
                    out["coverage_unmatched_absent"] += 1
                else:
                    out["coverage_unmatched_researchable"] += 1
    return out


async def board(db, *, include_closed: bool = False) -> dict:
    """The triage board: every cause with its count, lane, action and ruling.

    → {causes: [...], totals: {open, acknowledged, by_lane}, lanes: {...}}"""
    counts: dict[str, int] = {}
    counts.update(await _nfi_counts(db))
    counts.update(await _price_counts(db))
    counts.update(await _coverage_counts(db))
    disp = await load_dispositions(db)

    causes = []
    for key, meta in CAUSES.items():
        n = counts.get(key, 0)
        ruling = disp.get(f"{key}|{cause_key(key)[len(key) + 1:]}") or \
            disp.get(f"{key}|*")
        closed = ruling is not None
        if closed and not include_closed:
            pass                      # still listed, but flagged — see 'closed'
        causes.append({
            "cause": key, "count": n, "lane": meta["lane"],
            "lane_fa": LANE_FA[meta["lane"]], "title": meta["title"],
            "why": meta["why"], "action": meta["action"], "route": meta["route"],
            "closed": closed,
            "disposition": (ruling or {}).get("disposition"),
            "reason": (ruling or {}).get("reason"),
            "decided_at": (ruling or {}).get("decided_at"),
        })
    causes.sort(key=lambda c: (c["closed"], -c["count"]))

    open_n = sum(c["count"] for c in causes if not c["closed"])
    ack_n = sum(c["count"] for c in causes if c["closed"])
    by_lane: dict[str, int] = {}
    for c in causes:
        if not c["closed"]:
            by_lane[c["lane"]] = by_lane.get(c["lane"], 0) + c["count"]
    return {"causes": causes,
            "totals": {"open": open_n, "acknowledged": ack_n,
                       "all": open_n + ack_n, "by_lane": by_lane},
            "lanes": LANE_FA}
