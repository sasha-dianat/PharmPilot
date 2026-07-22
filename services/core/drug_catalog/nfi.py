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
# the ATC hierarchy renders as label/code anchor pairs per level:
#   <a href="/NFI/SearchByATC?Term=A02BA">H2-RECEPTOR ANTAGONISTS</a> …
#   <a href="/NFI/SearchByATC?Term=A02BA">A02BA</a>
_ATC_TREE_RE = re.compile(
    r'href="/NFI/SearchByATC\?Term=([A-Za-z0-9]+)"\s*>\s*([^<]+?)\s*</a>', re.I)
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


def parse_atc_path(html: str) -> list[dict]:
    """The labeled ATC hierarchy, root→leaf:
    [{"code": "A", "label": "ALIMENTARY TRACT AND METABOLISM"}, …,
     {"code": "A02BA02", "label": "RANITIDINE"}].
    The LEAF label is the site's own canonical generic for this monograph —
    the anchor that lets a page's coherence be checked against itself."""
    labels: dict[str, str] = {}
    for code, text in _ATC_TREE_RE.findall(html):
        code, text = code.upper(), _clean(text)
        if not text or text.upper() == code:      # the bare code anchor
            continue
        labels.setdefault(code, text)
    return [{"code": c, "label": labels[c]} for c in sorted(labels, key=len)]


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
    path = parse_atc_path(html)
    if path:
        out["atc_path"] = path                     # pharmacological category chain
        out.setdefault("atc", path[-1]["code"])
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


# ── page coherence: spliced-monograph quarantine ─────────────────────────────
# Legacy NFI product pages can reference a generic-entity id the site has since
# REUSED: the page then renders drug A's product block (نام/IRC/قیمت/تولیدکننده)
# with drug B's monograph (نام عمومی/شکل دارویی/ATC). Verified 2026-07-22:
# RANITIDINE product pages carrying the follitropin monograph, G03GA05.
# The ATC leaf label + the brand string let the page indict itself.

_FORM_MARKERS = (               # marker stated in the brand string → form family
    ("MOUTH WASH", "MOUTHWASH"), ("SYRINGE", "INJECTION"), ("SACHET", "POWDER"),
    ("POWDER", "POWDER"), ("SYRUP", "SYRUP"), ("CREAM", "CREAM"),
    ("SPRAY", "SPRAY"), ("DROP", "DROP"), ("SUPP", "SUPPOSITORY"),
    ("ENEMA", "ENEMA"), ("OINT", "OINTMENT"), ("VIAL", "INJECTION"),
    ("AMP", "INJECTION"), ("INJ", "INJECTION"), ("SUSP", "SUSPENSION"),
    ("CAP", "CAPSULE"), ("TAB", "TABLET"), ("GEL", "GEL"), ("SOL", "SOLUTION"),
)
_FORM_COMPAT = {                # stored forms that satisfy a stated marker
    "TABLET": ("TABLET",), "CAPSULE": ("CAPSULE",),
    "INJECTION": ("INJECTION", "SOLUTION"), "SUSPENSION": ("SUSPENSION",),
    "SYRUP": ("SYRUP", "SOLUTION"), "OINTMENT": ("OINTMENT",),
    "CREAM": ("CREAM",), "GEL": ("GEL", "JELLY"),
    "DROP": ("DROP", "SOLUTION", "SUSPENSION"),
    "SPRAY": ("SPRAY", "AEROSOL"), "SUPPOSITORY": ("SUPPOSITORY",),
    "POWDER": ("POWDER", "GRANULE"), "MOUTHWASH": ("MOUTHWASH", "SOLUTION"),
    "SOLUTION": ("SOLUTION", "INJECTION"), "ENEMA": ("ENEMA",),
}
_STRENGTH_IN_BRAND_RE = re.compile(
    r"(\d+(?:\.\d+)?(?:\s*/\s*\d+(?:\.\d+)?)?)\s*(MG|MCG|G|IU|U|%)?(?![\d.])", re.I)


def brand_stated_form(brand: str | None) -> str | None:
    b = str(brand or "").upper()
    for marker, target in _FORM_MARKERS:
        if re.search(rf"(?<![A-Z]){marker}(?![A-Z])", b):
            return target
    return None


def brand_generic_tokens(brand: str | None) -> list[str]:
    """Leading latin words of a brand string, before any digits/parenthesis —
    'MEDROXYPROGESTERONE ACETATE 250MG TAB' → ['medroxyprogesterone','acetate'];
    a pure trade name ('VICTOZA') yields itself and simply won't be in vocab."""
    head = re.split(r"[\d(®]", str(brand or ""))[0]
    return [t.lower() for t in re.findall(r"[A-Za-z]{2,}", head)]


def brand_stated_generic(brand: str | None, known: set[str]) -> str | None:
    """The generic name a brand string itself states, if its leading tokens
    form a KNOWN generic — 'RANITIDINE 150' → 'ranitidine'; 'VICTOZA' → None."""
    toks = brand_generic_tokens(brand)
    return next((" ".join(toks[:n]) for n in (3, 2, 1)
                 if len(" ".join(toks[:n])) >= 5
                 and " ".join(toks[:n]) in known), None)


def page_coherence(rec: dict, known_generics: set[str] | None = None,
                   atc_families: dict[str, set[str]] | None = None) -> list[str]:
    """Deterministic self-contradiction check for one parsed detail page (or a
    catalog row — same shape). Returns human-readable reasons; empty =
    coherent (or unverifiable). `atc_families` maps a known generic to the ATC
    4-prefixes it is seen with, so synonym pairs (vitamin B12/cyanocobalamin,
    glyceryl trinitrate/nitroglycerin) that share a family never fire."""
    from difflib import SequenceMatcher
    reasons: list[str] = []
    brand = rec.get("brand_name") or rec.get("name_fa") or ""

    stated = brand_stated_form(brand)
    form = str(rec.get("dosage_form") or "").upper()
    if stated and form and not any(c in form for c in _FORM_COMPAT[stated]):
        reasons.append(f"form: brand says {stated}, monograph says {form}")

    if known_generics:
        cand = brand_stated_generic(brand, known_generics)
        mono = " ".join(str(rec.get(k) or "") for k in
                        ("generic_name", "generic_full")).lower()
        leaf = ""
        if rec.get("atc_path"):
            leaf = str(rec["atc_path"][-1].get("label") or "").lower()
        target = f"{mono} {leaf}".strip()
        if cand and target:
            overlap = any(t in target for t in cand.split())
            close = SequenceMatcher(None, cand, target[:len(cand) + 8]).ratio() >= 0.75
            fam = (atc_families or {}).get(cand) or set()
            row_fam = str(rec.get("atc") or "")[:4].upper()
            same_family = bool(row_fam) and any(f[:3] == row_fam[:3] for f in fam)
            if not overlap and not close and not same_family:
                reasons.append(f"generic: brand says {cand}, monograph says "
                               f"{leaf or (mono.split()[0] if mono else '?')}")
    return reasons


_MONOGRAPH_KEYS = ("generic_full", "composition", "atc", "atc_path",
                   "dosage_form", "route", "generic_name", "strength",
                   "indications", "interactions_text", "warnings",
                   "side_effects", "advice", "mechanism", "pharmacokinetics",
                   "similar_count", "brands", "country")


def quarantine_monograph(rec: dict, reasons: list[str]) -> dict:
    """Strip every monograph-scoped field from a self-contradicting page and
    re-derive identity from the product block's own brand string (row truth).
    The product fields (IRC/GTIN/قیمت/تولیدکننده) are kept — they belong to
    this product; the monograph belongs to some other drug entirely."""
    out = {k: v for k, v in rec.items() if k not in _MONOGRAPH_KEYS}
    brand = out.get("brand_name") or out.get("name_fa") or ""
    toks = brand_generic_tokens(brand)
    if toks:
        out["generic_name"] = " ".join(toks)
    m = _STRENGTH_IN_BRAND_RE.search(str(brand))
    if m:
        unit = (m.group(2) or "").lower()          # no unit stated → no guessing
        out["strength"] = f"{m.group(1)} {unit}".strip()
    stated = brand_stated_form(brand)
    if stated:
        out["dosage_form"] = stated
    out["integrity"] = {"spliced_page": True, "reasons": reasons}
    return out


def make_opener(proxy: str | None = None) -> urllib.request.OpenerDirector:
    """Build a urllib opener, optionally routing through an Iran proxy. Includes
    cookie support — several Iranian gov portals (e.g. ihio.gov.ir) set a session
    cookie then redirect to the SAME url; without cookies urllib sees an infinite
    redirect loop and surfaces a raw 302."""
    handlers: list[urllib.request.BaseHandler] = [
        urllib.request.HTTPCookieProcessor()]
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


def fetch_detail_raw(page_id: int, opener: urllib.request.OpenerDirector | None = None,
                     timeout: int = 25) -> tuple[int, bytes, dict]:
    """Like fetch_detail but returns raw bytes + headers and KEEPS the HTTP-error
    body; raises on transport failure. For the diagnostics-recording crawl path."""
    opener = opener or make_opener()
    req = urllib.request.Request(f"{BASE}/NFI/Detail/{page_id}", headers={
        "User-Agent": UA, "Accept": "text/html", "Accept-Language": "fa,en;q=0.8",
        "Referer": f"{BASE}/nfi"})
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() if hasattr(e, "read") else b""), dict(e.headers or {})
