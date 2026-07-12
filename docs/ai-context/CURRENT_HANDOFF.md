# Current Handoff

Status: READY_FOR_REVIEW
Workstream: discrepancy-workbench (DW-1 backend detail+analysis)
Owner: Fable 5
Worker: opus-implementation-worker
Last Updated: 2026-07-11

## Objective

Upgrade the read-only inconsistencies panel into a **Discrepancy Workbench** that
lets the admin RELATE an item's NFI-catalog data to its insurer-coverage data.
DW-1 (this task) is the backend: a per-drug joined detail endpoint + a pure
root-cause analysis helper. DW-2 (next, separate) rebuilds the frontend panel.

## Why

Today «پوشش بیمه» and «کاتالوگ NFI» are two disconnected lists — the admin can't
see, for one drug, its catalog record next to its coverage entry, which is
exactly how a price conflict is diagnosed (e.g. octreotide 50mcg's 66M﷼ reference
was inherited from the 30mg sibling — only visible when you see both + siblings).

## Scope (READ-ONLY — selects only, no writes/migrations/deps)

**1. Pure helper** in `services/core/drug_catalog/coverage_harvest.py`:
```python
def diagnose_discrepancy(catalog: dict, entry: dict, siblings: list) -> list[str]:
    """Human Persian root-cause hints for one drug's coverage entry.
    catalog: {announced_price, strength, country, generic_name, atc, ...}
    entry:   the insurer coverage dict {reference_price, match_confidence, match_method, ...} or {}
    siblings:[{name_fa, strength, announced_price, reference_price}] same generic, other products
    Rules (append a hint when the condition holds; empty list if all clean):
      - announced & reference both present, ratio = reference/announced:
          * ratio>2 or ratio<0.5  → it's a conflict; then:
              - if a sibling's reference_price ≈ this reference (within 5%) AND that
                sibling's strength != this strength →
                'مرجع بیمه با هم‌مولکول {sib_strength} یکسان است ({ref}) ولی قدرت این قلم {strength} است — احتمال تطبیق بین‌قدرتی.'
              - elif match_confidence present and < 0.75 →
                'تطبیق کم‌اطمینان ({conf}، {method}) — بازبینی شود.'
              - else →
                'مرجع بیمه {ref} در برابر قیمت اعلامی {announced} ({ratio}× ) با تطبیق دقیق — احتمالاً قیمت اعلامی قدیمی است یا ردیف منبع خراب.'
      - country is None → 'کشور نامشخص — نیازمند خزش کامل NFI.'
      - generic_name None/blank → 'ژنریک نامشخص.'
      - atc None → 'کد ATC موجود نیست.'
    round ratio to 1 decimal; format money with commas."""
```
Add small unit tests in `tests/unit/test_coverage_harvest.py`: cross-strength
case (sibling same ref diff strength → cross-strength hint), stale-price case
(exact match, big ratio, no matching sibling → stale hint), clean case → [].

**2. Endpoint** `GET /pricing/inconsistencies/drug/{irc}?insurer=salamat`
(`inventory:read`) in `services/platform/routers/pricing.py`, returning:
```json
{
  "irc": "...",
  "catalog": { irc, name_fa, name_en, generic_name, ingredient_key, strength,
               dosage_form, brand_name, manufacturer, country, atc,
               announced_price:int|null, package_count, gtin, source } | null,
  "coverage": { "<insurer>": { covered, share_pct, reference_price, ceiling,
               inpatient, match_confidence, match_method }, ... all insurers present },
  "siblings": [ { irc, name_fa, strength, dosage_form, announced_price:int|null,
                  reference_price:int|null (this insurer's ref for that sibling) } 
                for products with the SAME generic_name, irc != this, limit 25,
                order by announced_price desc ],
  "analysis": [ diagnose_discrepancy(...) strings for the SELECTED insurer's entry ]
}
```
- `catalog` is null if the IRC isn't in drug_catalog (unmatched formulary rows
  carry no IRC, so the frontend won't call this for them — but return
  `{catalog: null, coverage: {}, siblings: [], analysis: []}` gracefully if so).
- "siblings" = same `generic_name` (exact), other IRCs — reveals cross-strength.
  reference_price per sibling = `sib.coverage->insurer->>'reference_price'` or null.
- Reuse the existing `/pricing/inconsistencies` endpoint's style (local imports,
  `require_permission("inventory:read")`, `AsyncSession = Depends(get_db)`).

## Out of Scope

- NO frontend (that's DW-2). NO writes/resolution actions. NO new columns/migration/dep.
- Don't change the existing `/pricing/inconsistencies` list endpoint or the panel.

## Existing Decisions / Invariants

- `drug_catalog` cols: irc, name_fa, name_en, generic_name, ingredient_key,
  strength, dosage_form, brand_name, manufacturer, country, atc,
  announced_price (Numeric), package_count, gtin, coverage (JSONB), source.
- Coverage entry shape: {covered, share_pct, reference_price, ceiling, inpatient,
  match_confidence, match_method}.
- Graphify-before-source-read is mandatory (see .claude/rules). pytest = miniforge
  `python3 -m pytest` (NOT .venv). Backend :8001 = launchd KeepAlive (kill=restart).

## Acceptance Criteria

1. `diagnose_discrepancy` unit tests pass (cross-strength / stale-price / clean).
2. `GET /pricing/inconsistencies/drug/<a real salamat conflict IRC>?insurer=salamat`
   → 200 with catalog+coverage+siblings+analysis; for the octreotide-50mcg IRC the
   siblings include the 30mg product and analysis names the cross-strength cause.
3. Unknown IRC → 200 with catalog null, empty coverage/siblings/analysis (not 500).
4. No pre-existing test regresses; endpoint is selects-only.

## Verification Plan

- `python3 -m pytest tests/unit/test_coverage_harvest.py -q`.
- Restart backend; `curl .../pricing/inconsistencies/drug/<irc>?insurer=salamat`
  for a known octreotide/ranitidine conflict IRC → paste catalog+coverage+siblings+analysis.
- Confirm unknown-IRC path returns 200 with nulls.

## Escalation Conditions

- If coverage/catalog shape doesn't match reality → STOP, report.
- If a resolution/write action seems needed → STOP (Fable scope decision).

Status: READY_FOR_REVIEW

## Implementation Result

Two changes, READ-ONLY (selects only, no writes/migrations/deps/columns):

1. **Pure helper** `diagnose_discrepancy(catalog, entry, siblings) -> list[str]`
   in `services/core/drug_catalog/coverage_harvest.py` (placed right after
   `price_conflict`, plus a private `_as_num` coercion helper). Implements the
   contract rules verbatim: conflict block (ratio>2 or ratio<0.5) branching
   cross-strength-sibling / low-confidence / stale-price, then the country /
   generic / atc completeness hints. Money formatted with commas, ratio rounded
   to 1 dp. Pure — no I/O.

2. **Endpoint** `GET /api/v1/pricing/inconsistencies/drug/{irc}?insurer=salamat`
   (`inventory:read`) in `services/platform/routers/pricing.py`, added just
   before `/sync/run`. Returns `{irc, catalog|null, coverage{all insurers},
   siblings[≤25, same generic, order announced_price desc nullslast],
   analysis[]}`. Local-import style matches the existing `/inconsistencies`
   endpoint. Unknown IRC → 200 with catalog null + empty coverage/siblings/analysis.

Note: real IRCs are reached under the `/api/v1` mount prefix (main.py mounts the
pricing router at `/api/v1/pricing`), and auth login is `/api/v1/auth/login`.

Files changed:
- `services/core/drug_catalog/coverage_harvest.py` (helper + `_as_num`)
- `services/platform/routers/pricing.py` (new endpoint)
- `tests/unit/test_coverage_harvest.py` (5 new diagnose tests)

## Verification Evidence

- `python3 -m pytest tests/unit/test_coverage_harvest.py -q` → **33 passed in 0.76s**
  (the 5 new: cross-strength, stale-price, low-confidence, clean→[], catalog-gaps).
- Backend restarted (launchd respawn, health 200 in 1s).
- Live cross-strength IRC `1696988120783443` (اکتوستاتین, octreotide 50 ug/1mL,
  announced 300,000, salamat ref 66,000,000):
  - `catalog`: {generic octreotide, strength "50 ug/1mL", country null, atc H01CB02,
    announced_price 300000, source nfi-harvest, ...}
  - `coverage`: {"salamat":{covered:true, share_pct:90, reference_price:66000000,
    match_confidence:1.0, match_method:"ingredient", ceiling:null, inpatient:null}}
  - `siblings`: 25, top ones are ساندوستاتین لار **30 mg** with reference_price
    66000000 (announced up to 743,630,000) — the 30mg source of the inherited ref.
  - `analysis`: ["مرجع بیمه با هم‌مولکول 30 mg یکسان است (66,000,000) ولی قدرت
    این قلم 50 ug/1mL است — احتمال تطبیق بین‌قدرتی.", "کشور نامشخص — نیازمند خزش
    کامل NFI."]  → names the cross-strength cause exactly (AC #2 met).
- Unknown IRC `0000000000000000` → **HTTP 200**,
  `{"catalog":null,"coverage":{},"siblings":[],"analysis":[]}` (AC #3 met).

## Remaining Risks / Questions

- Data reality: octreotide announced prices in the catalog are frequently 0 or
  tiny (e.g. 32,600 / 300,000) while salamat ref is a flat 66,000,000 across BOTH
  30mg and 50mcg — the ref appears mislinked at the generic level (match_method
  "ingredient", not strength-aware). DW-1 only *surfaces* this; the underlying
  coverage-import strength mismatch is out of scope (no writes).
- `country` is null on many NFI-harvest rows, so the "کشور نامشخص" completeness
  hint co-fires with the price hint on most conflict drugs. This is per the
  contract rules (each independent condition appends) — not a bug, but the DW-2
  UI may want to visually separate price-cause hints from completeness hints.
- Sibling ordering uses `announced_price desc nullslast` (a refinement of the
  contract's "order by announced_price desc") so null-priced siblings don't crowd
  out the meaningful high-price cross-strength siblings within the 25-row cap.

## Durable Learnings

- Platform routers are mounted under `/api/v1` in `services/platform/main.py`
  (pricing → `/api/v1/pricing`, auth login → `POST /api/v1/auth/login` with JSON
  `{username,password}`). Curl the endpoints via that prefix, not bare paths.
- salamat octreotide reference is a flat 66,000,000 across all strengths — a
  clean real fixture for cross-strength discrepancy demos.
