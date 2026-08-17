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
- ⬜ Verify against a real pharmacy receipt — still the open item from before,
  and the one that proves the arithmetic rather than the wiring.
- ⬜ Confirm the 30/70 franchise, حق فنی and VAT exemptions (`pricing_ir/config.py`).
