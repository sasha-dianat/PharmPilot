"""هوش تطبیق — self-calibrating match verifier (Fellegi–Sunter record linkage).

Learns from the owner's OWN decision history — approved/rejected review items
and live approved coverage — and verifies every formulary↔catalog match:

  * per-feature m/u likelihoods (name similarity, strength agreement, form
    agreement, brand token hit): m = P(level|true match), u = P(level|false
    match), fitted from labeled pairs with Laplace smoothing; a pair's score
    is Σ log2(m/u) — the classic Fellegi–Sunter log-likelihood ratio;
  * a learned per-insurer PRICE-RATIO band (reference_price / announced_price
    quantiles from approved coverage): a price outlier demotes a match even
    when the name looks right — wrong strength/pack links betray themselves
    through price.

Deterministic-first invariant preserved: fitting is an explicit owner action
(retrain endpoint), the fitted model is a versioned JSON artifact, scoring is
pure arithmetic — no LLM, no network, fully offline-testable. Verified matches
are only ever DEMOTED to review (with a reason), never auto-promoted.
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from .coverage_import import _FORM_WORDS, _strength_mg  # deterministic helpers
from .schema import normalize

MODEL_PATH = Path("data/reference/match_model.json")

_FEATURES = ("name", "strength", "form", "brand")
# score below which a matched link is demoted to review (log2 units); a fresh
# unfitted model never demotes (verify is a no-op without an artifact).
_DEFAULT_REVIEW_THRESHOLD = 0.0
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


# ── feature extraction (discrete levels) ─────────────────────────────────────

# Matching-oriented Persian→Latin romanization: not linguistic perfection, just
# enough that «سالبوتامول» lands within edit distance of "salbutamol". Latin
# text passes through unchanged, so the transliterated comparison is safe to
# run on every pair.
_P2L = {
    "ا": "a", "آ": "a", "أ": "a", "إ": "a", "ب": "b", "پ": "p", "ت": "t",
    "ط": "t", "ث": "s", "س": "s", "ص": "s", "ج": "j", "چ": "ch", "ح": "h",
    "ه": "h", "خ": "kh", "د": "d", "ذ": "z", "ز": "z", "ض": "z", "ظ": "z",
    "ر": "r", "ژ": "zh", "ش": "sh", "ع": "", "ء": "", "ئ": "", "غ": "gh",
    "ق": "gh", "ف": "f", "ک": "k", "ك": "k", "گ": "g", "ل": "l", "م": "m",
    "ن": "n", "و": "o", "ی": "i", "ي": "i", "ۀ": "e", "ة": "e",
}


def _translit(s: str) -> str:
    return "".join(_P2L.get(ch, ch) for ch in str(s or "").lower())


def _form_token(text) -> str | None:
    for tok in re.findall(r"[A-Za-z]+|[؀-ۿ]+", str(text or "").lower()):
        if tok in _FORM_WORDS:
            return _FORM_WORDS[tok]
    return None


def extract_features(row_name: str, rec) -> dict:
    """Discrete feature levels for one (formulary row, catalog record) pair."""
    n_row = normalize(str(row_name or "").lower()) or str(row_name or "").lower()
    row_tr = _translit(n_row)
    # Token-level candidates too: «سالبوتامول ۲ میلی گرم قرص» whole-string vs
    # "salbutamol" dilutes below threshold; its NAME token alone matches.
    row_tokens = [tk for tk in re.split(r"[\s\-/]+", row_tr) if len(tk) >= 4]
    best_sim = 0.0
    for target in (rec.generic_name, rec.name_fa, rec.brand_name):
        if target:
            t = normalize(str(target).lower()) or str(target).lower()
            t_tr = _translit(t)
            best_sim = max(best_sim,
                           SequenceMatcher(None, n_row, t).ratio(),
                           # cross-script: «سالبوتامول» ↔ "salbutamol"
                           SequenceMatcher(None, row_tr, t_tr).ratio(),
                           *(SequenceMatcher(None, tk, t_tr).ratio()
                             for tk in row_tokens))
    name_level = "high" if best_sim >= 0.85 else ("mid" if best_sim >= 0.6 else "low")

    row_mg = _strength_mg(str(row_name).translate(_DIGITS))
    rec_mg = _strength_mg(f"{rec.strength or ''}")
    if not row_mg or not rec_mg:
        s_level = "unknown"
    elif row_mg & rec_mg:
        s_level = "exact"
    else:
        s_level = "conflict"

    rf, cf = _form_token(row_name), _form_token(rec.dosage_form)
    f_level = "unknown" if not rf or not cf else ("same" if rf == cf else "different")

    b_level = "no"
    for tok in re.findall(r"[A-Za-z]{4,}", str(row_name or "")):
        hay = f"{rec.brand_name or ''} {rec.name_fa or ''}".lower()
        if tok.lower() in hay:
            b_level = "hit"
            break
    return {"name": name_level, "strength": s_level, "form": f_level, "brand": b_level}


# ── fitting (the autodidact part) ────────────────────────────────────────────

def fit(pairs: list[tuple[dict, bool]],
        price_ratios: dict[str, list[float]]) -> dict:
    """pairs: [(features, is_true_match)]; price_ratios: insurer → observed
    reference/announced ratios from APPROVED coverage. → model dict."""
    mu: dict = {}
    pos = [f for f, y in pairs if y] or []
    neg = [f for f, y in pairs if not y] or []
    for feat in _FEATURES:
        levels = sorted({f[feat] for f, _ in pairs} | {"unknown"})
        mu[feat] = {}
        for lv in levels:
            m = (sum(1 for f in pos if f[feat] == lv) + 1) / (len(pos) + len(levels))
            u = (sum(1 for f in neg if f[feat] == lv) + 1) / (len(neg) + len(levels))
            mu[feat][lv] = [round(m, 5), round(u, 5)]
    bands = {}
    for ins, ratios in price_ratios.items():
        rs = sorted(r for r in ratios if r and r > 0)
        if len(rs) >= 8:
            q = lambda p: rs[min(len(rs) - 1, int(p * len(rs)))]
            bands[ins] = {"lo": round(q(0.05) / 1.5, 4), "hi": round(q(0.95) * 1.5, 4),
                          "median": round(q(0.5), 4), "n": len(rs)}
    return {"version": 1, "fitted_at": datetime.now(timezone.utc).isoformat(),
            "mu": mu, "price_bands": bands,
            "thresholds": {"review": _DEFAULT_REVIEW_THRESHOLD},
            # FS demotion arms only with enough of BOTH labels — a model fitted
            # from (say) zero positives has uniform m-priors and would demote
            # good matches. Price-band demotion arms independently (its corpus
            # is live approved coverage, thousands of entries).
            "fs_armed": len(pos) >= 20 and len(neg) >= 20,
            "counts": {"pos": len(pos), "neg": len(neg)}}


def score(features: dict, model: dict) -> float:
    """Fellegi–Sunter log-likelihood ratio (log2). Positive ⇒ evidence FOR."""
    total = 0.0
    for feat in _FEATURES:
        lv = features.get(feat, "unknown")
        m, u = model["mu"].get(feat, {}).get(lv) or model["mu"].get(feat, {}).get("unknown") or [0.5, 0.5]
        total += math.log2(max(m, 1e-6) / max(u, 1e-6))
    return round(total, 3)


def price_verdict(reference_price, announced_price, model: dict, insurer: str) -> str:
    """'in_band' | 'out_band' | 'unknown' against the learned insurer band."""
    band = (model.get("price_bands") or {}).get(insurer)
    try:
        ref, ann = float(reference_price), float(announced_price)
    except (TypeError, ValueError):
        return "unknown"
    if not band or ann <= 0 or ref <= 0:
        return "unknown"
    ratio = ref / ann
    return "in_band" if band["lo"] <= ratio <= band["hi"] else "out_band"


def score_suggestion(raw_name: str, sugg: dict, model: dict | None) -> float | None:
    """FS score for a RESEARCH suggestion vs the name it claims to identify —
    the same engine judging a second boundary: «is this research really that
    drug?». Low score ⇒ suspect extraction, surfaced first for review."""
    if not model or not model.get("mu"):
        return None
    from types import SimpleNamespace
    strengths = list(sugg.get("strengths") or [])
    for v in (sugg.get("variants") or []):
        for s in (v.get("strength"), v.get("concentration")):
            if s and s not in strengths:
                strengths.append(s)
    forms = [sugg.get("dosage_form")] + [v.get("dosage_form")
                                         for v in (sugg.get("variants") or [])]
    rec = SimpleNamespace(
        generic_name=sugg.get("generic_name"),
        name_fa=sugg.get("raw_name") or None,
        brand_name=sugg.get("brand_name"),
        strength=" ".join(str(s) for s in strengths),
        dosage_form=" ".join(str(f) for f in forms if f),
    )
    return score(extract_features(raw_name, rec), model)


def annotate_review_fs(review: list, by_irc: dict, model: dict | None) -> list:
    """Attach an 'fs' score to each coverage-review item (pair: row name vs its
    candidate record). The GUI orders by |fs| ascending — ACTIVE LEARNING: the
    pairs nearest the decision boundary are exactly the ones whose human verdict
    teaches the model most."""
    if not model or not model.get("mu"):
        return review
    out = []
    for item in (review or []):
        if isinstance(item, dict):
            rec = by_irc.get(item.get("irc"))
            name = (item.get("row") or {}).get("drug_name")
            if rec is not None and name:
                item = {**item, "fs": score(extract_features(name, rec), model)}
        out.append(item)
    return out


# ── verification seam (called from stage_run_payload) ───────────────────────

def verify_links(links, model: dict | None, insurer: str) -> int:
    """Demote matched links the model distrusts: FS score below threshold, or a
    price ratio outside the learned band. Demotion = confidence capped under
    the review cutoff + reason attached to the row (visible in the review GUI).
    Never promotes. Returns the number demoted."""
    if not model or not model.get("mu"):
        return 0
    thr = (model.get("thresholds") or {}).get("review", _DEFAULT_REVIEW_THRESHOLD)
    demoted = 0
    for link in links:
        if not link.matched or link.method == "irc":   # exact code = ground truth
            continue
        feats = extract_features(link.row.get("drug_name", ""), link.record)
        s = score(feats, model)
        pv = price_verdict(link.row.get("reference_price"),
                           getattr(link.record, "announced_price", None),
                           model, insurer)
        reasons = []
        if s < thr and model.get("fs_armed"):
            reasons.append(f"امتیاز تطبیق پایین ({s})")
        if pv == "out_band":
            reasons.append("قیمت خارج از الگوی آموخته‌شده")
        if reasons:
            link.confidence = min(link.confidence, 0.74)   # under default cutoff
            link.matched = link.record is not None and link.confidence >= 0.55
            link.method = f"{link.method}+intel"
            link.row = {**link.row, "هشدار_هوش_تطبیق": "؛ ".join(reasons),
                        "امتیاز_FS": s}
            demoted += 1
    return demoted


# ── artifact + DB fitting ────────────────────────────────────────────────────

_cache: dict = {}


def load_model(path=MODEL_PATH) -> dict | None:
    path = Path(path)
    if not path.exists():
        return None
    mtime = path.stat().st_mtime
    if _cache.get("mtime") != mtime:
        _cache.update(mtime=mtime, model=json.loads(path.read_text(encoding="utf-8")))
    return _cache["model"]


def save_model(model: dict, path=MODEL_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model, ensure_ascii=False, indent=1), encoding="utf-8")
    _cache.clear()


async def fit_from_db(db, mode: str = "decisions") -> dict:
    """Build a labeled corpus and fit. Two training regimes:

    mode='decisions' (strict): review items of APPROVED runs only —
      accepted=True ⇒ positive, accepted=False ⇒ negative (the owner looked at
      exactly that pair and decided).
    mode='bootstrap' (weak supervision over ALL formulary data): adds the
      linker's own very-high-confidence pairings (conf ≥ 0.85 on any
      non-rejected run) as weak positives — classic FS self-training. Arms the
      model before enough human labels exist; a later 'decisions' refit
      supersedes it as approvals accrue.
    Both regimes stabilize the u-side with deterministic decoy pairs (known
    non-matches). Price ratios come from live approved coverage."""
    from sqlalchemy import select
    from shared.models.coverage import CoverageRun
    from shared.models.drug_catalog import DrugCatalogItem

    recs = (await db.execute(select(DrugCatalogItem))).scalars().all()
    by_irc = {r.irc: r for r in recs}

    pairs: list[tuple[dict, bool]] = []
    reason_counts: dict[str, int] = {}
    statuses = ("approved",) if mode == "decisions" else ("approved", "parsed")
    runs = (await db.execute(select(CoverageRun)
                             .where(CoverageRun.status.in_(statuses)))).scalars().all()
    rec_list = list(by_irc.values())
    for run in runs:
        approved = run.status == "approved"
        for i, item in enumerate(run.review or []):
            if not isinstance(item, dict):
                continue
            row = item.get("row") or {}
            rec = by_irc.get(item.get("irc"))
            name = row.get("drug_name")
            if not name or rec is None:
                continue
            if item.get("accepted"):
                pairs.append((extract_features(name, rec), True))
            elif approved and mode == "decisions":
                feats = extract_features(name, rec)
                pairs.append((feats, False))
                code = item.get("reject_reason")
                if code:
                    # A coded refusal is a feature-targeted label — the owner
                    # SAID what was wrong — so it teaches with double weight
                    # and is tallied for the training report.
                    pairs.append((feats, False))
                    reason_counts[code] = reason_counts.get(code, 0) + 1
            elif mode == "bootstrap":
                conf = item.get("confidence") or 0
                if conf >= 0.85:               # linker's own strong pairings
                    pairs.append((extract_features(name, rec), True))
                elif approved:
                    pairs.append((extract_features(name, rec), False))
            if rec_list:   # deterministic decoy → robust u estimates
                decoy = rec_list[(i * 7919) % len(rec_list)]
                if decoy.irc != rec.irc:
                    pairs.append((extract_features(name, decoy), False))

    if mode == "bootstrap":
        # Review items are sub-threshold BY CONSTRUCTION (that is why they are
        # in review), so they cannot supply strong positives. Template weak
        # supervision instead: each catalog record vs its own composed name is
        # a perfect match by construction — teaching the m-side what
        # high/exact/same levels look like; decoys keep teaching the u-side.
        step = max(1, len(rec_list) // 1500)
        for i, r in enumerate(rec_list[::step]):
            row_text = f"{r.generic_name or r.name_fa} {r.strength or ''} {r.dosage_form or ''}"
            if i % 3 == 0 and r.brand_name:
                row_text = f"{r.brand_name} {row_text}"
            pairs.append((extract_features(row_text, r), True))
            decoy = rec_list[(i * 6329 + 13) % len(rec_list)]
            if decoy.irc != r.irc:
                pairs.append((extract_features(row_text, decoy), False))

    price_ratios: dict[str, list[float]] = {}
    for r in recs:
        cov = r.coverage if isinstance(r.coverage, dict) else {}
        for ins, entry in cov.items():
            rp, ap = (entry or {}).get("reference_price"), r.announced_price
            if rp and ap:
                try:
                    price_ratios.setdefault(ins, []).append(float(rp) / float(ap))
                except (TypeError, ValueError, ZeroDivisionError):
                    pass

    model = fit(pairs, price_ratios)
    if reason_counts:
        model["counts"]["reasons"] = reason_counts
    save_model(model)
    return model
