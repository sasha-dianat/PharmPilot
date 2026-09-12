# PharmPilot — Road to Market-Ready

Status legend: ✅ done · 🟡 partial · ⬜ not started

## Built so far (foundation)
- ✅ Deterministic interaction engine (PK/PD, curated + DDInter bundle), Medical Intelligence console
- ✅ Physician responsibility letter (PHI-safe, editable, audited)
- ✅ Iranian pricing engine (insurer share, مابه‌التفاوت, حق فنی, VAT; Decimal/Rial)
- ✅ Drug catalog + NFI harvester (CLI + GUI, live progress) + Excel/CSV import
- ✅ Daily price-sync → manager-approved proposals
- ✅ Smart insurance-coverage extractor (دارونامه → coverage JSON, fuzzy linkage)
- ✅ Reception affordability flow (per-patient baskets, quote, swap/remove/reduce levers)
- ✅ e-prescription استعلام integration seam (null provider → local fallback)

---

## Phase 1 — Real national data loaded (DATA)  🟡
The engine is done; it needs the full dataset behind it.
- 🟡 Finish NFI crawl (~65% by count; ~39k ingested) → re-crawl for full extraction (country/producer/monograph now captured) + finish ids 53,642–60,000
- 🟡 Load per-insurer دارونامه (تأمین / سلامت / نیروهای مسلح) — config-driven crawler + staged-review GUI built (probe→harvest→diff→approve, global proxy lock); needs real source URLs behind the Iran proxy
- ⬜ Verify pricing/مابه‌التفاوت on a sample of real prescriptions vs a real pharmacy receipt
- ⬜ Confirm the 30/70 franchise, حق فنی amount, VAT exemptions with the domain expert (tariffs in pricing_ir/config.py)
**Exit:** a pharmacist can quote any real Rx and the numbers match a real receipt.

## Phase 2 — Live insurer integration (INTEGRATION)  ⬜
- ⬜ Real نسخه الکترونیک استعلام adapter (per-pharmacy credentials) behind the EligibilityProvider seam
- ⬜ Real claim submission / adjudication to the insurer (today it's a sandbox)
- ⬜ Prescription intake from the insurer e-prescription system by reference code (not just manual/CV)
- ⬜ NFI web-service feed for the daily price sync (replace file/crawl with the official API once credentialed)
**Exit:** dispensing pulls the authoritative patient share live and submits a real claim.

## Phase 3 — Workflow completeness (PRODUCT)  🟡
- 🟡 Full Rx lifecycle: intake → quote → adjudication → fill → dispense (states exist; wire the reception→fill handoff end to end)
- ⬜ Inventory decrement on dispense + reconciliation with the depot/shelf modules
- ⬜ POS / payment capture for the patient-payable amount
- ⬜ Label printing on dispense (label engine exists — wire to the final step)
- ⬜ Patient-category franchise overrides (کمیته امداد 15%, special-disease 0%)
- ⬜ Returns / partial fills / reversals

## Phase 4 — Hardening & compliance (TRUST)  ⬜
- ⬜ Security review: authz on every endpoint, rate limits, secrets out of code (DB password is currently in .mcp.json/config — move to env/secrets)
- ⬜ PHI audit completeness pass (clinical-safety-reviewer subagent over all egress paths)
- ⬜ Backups + migration rollback drill; single-Alembic-head enforcement in CI
- ⬜ Load/perf test the quote + interaction endpoints at pharmacy volume
- ⬜ Error monitoring / structured logging / alerting in prod

## Phase 5 — Quality & release (SHIP)  🟡
- 🟡 Test coverage: strong on pricing/catalog/interaction; ⬜ add e2e (Playwright) for the reception→dispense happy path
- ⬜ Fix the baseline frontend tsc errors (DashboardShell/useAIProvider/MedReconciliationPage) so the build is clean
- ⬜ CI pipeline running /release-check gates on every PR
- ⬜ Deployment: containerize, env-based config, DB provisioning, proxy config for NFI/insurer access
- ⬜ Pilot with one real pharmacy → feedback loop → GA

---

## Immediate next 3 actions
1. Ingest the harvested backlog + resume the crawl to completion (Phase 1).
2. Obtain + load one insurer دارونامه; validate a real receipt end to end (Phase 1).
3. Move the DB password out of committed config into env/secrets (Phase 4 quick win).

## Pricing chain — state after 2026-08-16

The chain the owner specified is now closed end to end:

    distributor invoice ──→ unit_cost  AND  sell_price (per lot)
                                    margin = what falls out of the two
                                                       ↓
    insurer reference ──→ insurer share      shelf price = max(every batch,
                                                  owner's manual price)
                                                       ↓
                                              patient remainder

- ✅ Shelf price exists (`inventory_lots.sell_price`, migration 0040) and the
  owner can set one by hand (`drug_products.manual_shelf_price`, 0042), or
  **mandate** one that the batches do not compete with (0043). Absent a mandate
  the two compete; neither overwrites the other.
- ✅ The quote uses it. `shelf_prices_for_ircs` bridges catalog IRC → inventory,
  and every quote line carries `price_source` — `shelf` or `nfi_fallback`.
- ✅ AWP/WAC removed (0041). NFI's announced price is authoritative for neither
  side of the calculation and is now only a labelled last resort.
- ✅ **The direction is corrected** (0044). The invoice names the consumer price
  beside the purchase price, so the sell price is transcribed at receiving and
  the margin is *derived*. `sell_price_basis` records which happened — `invoice`
  (observed) or `margin` (declared, for documents that omit it) — because both
  produce a number and only one of them was read off a document.
- ❌ **There is no default margin, by decision.** The markup depends on what the
  carton holds — cosmetics carry more than generics, and it varies by brand and
  form. A house constant would be wrong in every row it touched, so a lot with
  neither figure stays unpriced rather than being filled in.
- ⬜ **Price the stock.** 17 lots, 17 with a cost, 0 with a sell price; 0 products
  with a manual price. `price_source` therefore reads `nfi_fallback` catalog-wide.
  This is data entry, not code: receive with the invoice's consumer price, or set
  a price in the InventoryAdmin drawer.
- ✅ **The money adds up now.** `covered_base` and `differential` were each
  rounded on their own, and `round(a) + round(b) ≠ round(a + b)`: a 48,000-line
  sweep broke the engine's own documented identity on ~4% of lines at the default
  whole-Rial unit and ~7% at the 1,000-Rial unit `config.py` invites a pharmacy
  to set. The differential is now DERIVED as `gross − covered_base`, so
  `covered_base + differential == gross` holds by construction at any unit. Both
  reachable in production: `sell_price` and `manual_shelf_price` are Numeric(12,4)
  and fractional quantities are ordinary.
- ✅ Three smaller precision defects with it: the rounding unit was bound at
  import (a coarse-rounding deployment silently kept whole-Rial rounding until
  restart); `Decimal(qty)` on a float carried binary noise into every product
  below it; and a negative quantity produced a negative insurer share instead of
  being refused.
- ✅ `covered_base` is now on the quote line. A line showing مابه‌التفاوت without
  the base it was measured against cannot be checked by the person paying it.
- ✅ Pinned by `tests/unit/test_pricing_ir.py` (a 5,376-combination sweep) and
  `scripts/verify_pricing_conservation.py` (4,608 lines on real formulary rows
  through the real endpoint, four rounding units).
- ✅ **The tariffs are researched and sourced** (2026-08-22). Every `VERIFY` tag in
  `pricing_ir/config.py` is gone, replaced by the statute, circular or resolution
  it rests on. Confirmed unchanged: 30%/10% drug franchise, and all three VAT
  rates. Corrected: **no basic insurer pays any part of حق فنی** — it was billing
  508,900 ﷼ per prescription to an insurer that never pays it and under-charging
  the patient by the same — the 1405 fee (کد ۹۰۵۰۱۰) is 727,000 ﷼ private band,
  and armed forces is 0.15/0.00 rather than 0.20/0.05.
- ⚠️ **حق فنی is contested law.** دیوان عدالت اداری has annulled it six times
  (۳/۲/۸۸ … ۸/۱۱/۹۸) as outside the cabinet's competence; it was re-established
  as a کتاب ارزش نسبی service code under a new name. Collected in practice. Set
  `DEFAULT_TECHNICAL_FEE_RIAL = 0` to stop charging it.
- ⬜ **Three fee rules not enforced**, all of which bound what may lawfully be
  charged: the three-item-per-prescription cap (the engine over-charges a
  four-item Rx today), the +40% night/holiday uplift, and the تی‌تک/TTAC
  connection requirement.
- ⬜ Special populations still not modelled, and the earlier note here was wrong:
  روستایی/عشایر inside the referral path pay **30%** for drugs and **100%**
  outside it; **کمیته امداد/بهزیستی** is the 15% case; special-disease patients
  are free. All are over-charged today. Needs a recorded patient category first —
  a per-category franchise with no provenance for the category is worse than none.
- ⬜ Verify against a real pharmacy receipt — now the last open item. The
  arithmetic is proven self-consistent and the tariffs are sourced; what remains
  unproven is the two together against a real فاکتور.
