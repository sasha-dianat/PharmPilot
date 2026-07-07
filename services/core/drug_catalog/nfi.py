"""Shared NFI (irc.fda.gov.ir) fetch + detail-page parser.

Used by both the CLI harvester (scripts/harvest_nfi.py) and the admin-dashboard
harvest service (nfi_harvest_service.py). NFI product pages live at
/NFI/Detail/{id} with sequential numeric ids; each renders facts as
<label>…</label><span|bdo>…</span|bdo> pairs. Parser verified against real pages
(e.g. /NFI/Detail/17248, ویکتوزا / liraglutide).
"""
from __future__ import annotations

import re
import urllib.request

BASE = "https://irc.fda.gov.ir"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0 Safari/537.36")

_HTML_RE = re.compile(r"<[^>]+>")
_DIGIT_FIX = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_PAIR_RE = re.compile(
    r"<label[^>]*>\s*(.*?)\s*</label>\s*<(span|bdo)[^>]*>(.*?)</\2>", re.S | re.I)
_TITLE_RE = re.compile(r"<title>\s*(.*?)\s*</title>", re.S | re.I)
_ATC_RE = re.compile(r'class="graphLabelSearch"[^>]*>\s*([A-Za-z0-9]+)\s*<', re.I)
_SIMILAR_RE = re.compile(r"محصولات مشابه\s*\(\s*([\d۰-۹]+)")

# page label (colon/whitespace-stripped, lowercased) → intermediate field
_PAIR_LABELS = {
    "نام": "brand_name",                       # trade name (Latin), e.g. VICTOZA
    "نام عمومی": "generic_full",               # generic + form + strength string
    "شکل دارویی": "dosage_form",
    "نحوه مصرف": "route",
    "صاحب پروانه": "license_owner",
    "صاحب برند": "brand_owner",
    "تولید کننده": "manufacturer",
    "قیمت مصرف کننده هر بسته": "package_price",
    "قیمت واحد": "unit_price",                 # per-unit consumer price (what we quote on)
    "gtin": "gtin",
    "irc": "irc",
    "تعداد در بسته": "package_count",
    "ترکیبات": "composition",                  # e.g. "LIRAGLUTIDE 6 mg/1mL"
    "تاریخ اعتبار پروانه": "license_valid_until",
    "فارماکوکینتیک": "pharmacokinetics",
    # clinical free-text (kept in the harvested feed for future enrichment; not
    # stored in the catalog table today)
    "موارد مصرف": "indications",
    "تداخل های دارویی": "interactions_text",
    "هشدارها": "warnings",
    "عوارض جانبی": "side_effects",
    "نکات قابل توصیه": "advice",
    "مکانیسم اثر": "mechanism",
}


def _clean(fragment: str) -> str:
    import html as _h
    txt = _HTML_RE.sub(" ", fragment)
    return re.sub(r"\s+", " ", _h.unescape(txt)).strip()


def _digits(v) -> str:
    return re.sub(r"\D", "", str(v).translate(_DIGIT_FIX))


_TABLE_RE = re.compile(r"<table[^>]*>(.*?)</table>", re.S | re.I)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TITLE_ATTR_RE = re.compile(r'title="([^"]+)"')
_FLAG_ISO_RE = re.compile(r"CountriesFlag/([A-Za-z]{2})\.", re.I)
_DETAIL_LINK_RE = re.compile(r"/NFI/Detail/(\d+)")


def _parse_brands(html: str) -> list[dict]:
    """The محصولات مشابه table (نام دارو/صاحب نام تجاری/کشور/صاحب امتیاز/وضعیت):
    every registered brand of this generic, incl. this product's own row.
    کشور renders as a flag <img>; the name is its title attr, ISO its filename."""
    for tbl in _TABLE_RE.findall(html):
        if "کشور" not in tbl:
            continue
        rows: list[dict] = []
        for tr in _TR_RE.findall(tbl):
            tds = _TD_RE.findall(tr)
            if len(tds) < 6:
                continue                      # header row / malformed
            title = _TITLE_ATTR_RE.search(tds[3])
            iso = _FLAG_ISO_RE.search(tds[3])
            link = _DETAIL_LINK_RE.search(tds[1])
            status_title = _TITLE_ATTR_RE.search(tds[5])
            rows.append({
                "name": _clean(tds[1]) or None,
                "trade_owner": _clean(tds[2]) or None,
                "country": (title.group(1) if title else _clean(tds[3])) or None,
                "country_code": iso.group(1).upper() if iso else None,
                "licensee": _clean(tds[4]) or None,
                "status": _clean(tds[5]) or (status_title.group(1) if status_title else None),
                "nfi_id": int(link.group(1)) if link else None,
            })
        if rows:
            return rows
    return []


def parse_detail(html: str, page_id: int | None = None) -> dict | None:
    """Extract product fields from a /NFI/Detail/{id} page. Returns None for
    pages without an IRC (error/search shells)."""
    raw: dict[str, str] = {}
    for m in _PAIR_RE.finditer(html):
        label = _clean(m.group(1)).strip(" :：").lower()
        value = _clean(m.group(3))
        field = _PAIR_LABELS.get(label)
        if field and value and field not in raw:
            raw[field] = value

    out: dict = {}
    if raw.get("irc") and _digits(raw["irc"]):
        out["irc"] = _digits(raw["irc"])
    if raw.get("gtin") and _digits(raw["gtin"]):
        out["gtin"] = _digits(raw["gtin"])

    t = _TITLE_RE.search(html)
    if t and _clean(t.group(1)):
        out["name_fa"] = _clean(t.group(1))
    if raw.get("brand_name"):
        out["brand_name"] = raw["brand_name"]
        out.setdefault("name_fa", raw["brand_name"])

    comp = raw.get("composition") or ""
    m = re.match(r"^([A-Za-z][A-Za-z \-/+.]*?)\s+([\d.].*)$", comp)
    if m:
        out["generic_name"] = m.group(1).strip().lower()
        out["strength"] = m.group(2).strip()
    elif raw.get("generic_full"):
        out["generic_name"] = raw["generic_full"].split()[0].lower()
    if raw.get("generic_full"):
        out["generic_full"] = raw["generic_full"]

    for k in ("dosage_form", "route"):
        if raw.get(k):
            out[k] = raw[k]

    unit = _digits(raw.get("unit_price", ""))
    pack = _digits(raw.get("package_price", ""))
    if unit:
        out["announced_price"] = unit
    elif pack:
        out["announced_price"] = pack
    if pack:
        out["package_price"] = pack

    pc = re.match(r"\s*(\d+)", str(raw.get("package_count", "")).translate(_DIGIT_FIX))
    if pc:
        out["package_count"] = int(pc.group(1))

    manu = raw.get("manufacturer") or raw.get("brand_owner") or raw.get("license_owner")
    if manu:
        out["manufacturer"] = manu
    for k in ("license_owner", "brand_owner", "license_valid_until", "composition"):
        if raw.get(k):
            out[k] = raw[k]

    atc = _ATC_RE.search(html)
    if atc:
        out["atc"] = atc.group(1).upper()
    sim = _SIMILAR_RE.search(html)
    if sim:
        out["similar_count"] = int(_digits(sim.group(1)) or 0)

    # clinical free-text (optional; retained in the JSONL feed for later use)
    for k in ("indications", "interactions_text", "warnings", "side_effects",
              "advice", "mechanism", "pharmacokinetics"):
        if raw.get(k):
            out[k] = raw[k]

    brands = _parse_brands(html)
    if brands:
        out["brands"] = brands
        own = next((b for b in brands if page_id is not None and b["nfi_id"] == page_id),
                   brands[0])
        if own.get("country"):
            out["country"] = own["country"]

    out["category"] = "drug"
    if page_id is not None:
        out["nfi_id"] = page_id
    return out if out.get("irc") else None


def make_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    """Build a urllib opener, optionally routing through an Iran proxy."""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def fetch_detail(page_id: int, opener: urllib.request.OpenerDirector | None = None,
                 timeout: int = 25) -> tuple[int, str]:
    """Fetch one /NFI/Detail/{id} page → (status, html)."""
    opener = opener or make_opener()
    req = urllib.request.Request(f"{BASE}/NFI/Detail/{page_id}", headers={
        "User-Agent": UA, "Accept": "text/html", "Accept-Language": "fa,en;q=0.8",
        "Referer": f"{BASE}/nfi",
    })
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""
