# Runbook — drug-data operations

*Companion to `docs/data-architecture.md` (the model) and
`docs/ai-context/AI_COLLABORATION.md` (the ledger). This file is the
**procedure**: what to run, in what order, and what "done" looks like. Written
so a fresh session — human or model — can continue without re-deriving anything.*

## 0. Ground rules that are not negotiable

1. **Read from a fresh session before believing a count.** An uncommitted
   session shows you your own writes. Two ruling scripts printed `created` on
   2026-07-29 and silently rolled back because they never committed.
2. **Record rulings through the API, never a bare script.**
   `POST /pricing/issues/ruling` commits; `set_disposition()` deliberately does
   not (it is designed to run inside `apply_run`'s transaction).
3. **Never `UPDATE announced_price` directly.** Go through
   `price_history.record_price` so the SCD-2 trail exists. The
   `price_refreshed_without_history` check will catch you.
4. **Observed may be overwritten; Decided may not.** If a change would erase an
   owner ruling, an override, or insurer coverage, it is wrong by construction.
5. **Restart the backend after touching models or services**, and verify the
   listener — `lsof -nP -iTCP:8001 -sTCP:LISTEN`. Forgetting this has produced
   "fixed but not really" three times in this program.

## 1. Daily / after any change: the reconciliation gate

```bash
curl -s -H "Authorization: Bearer $TOK" \
  http://127.0.0.1:8001/api/v1/pricing/data-quality/report
```

* `healthy: true` and `firing: 0` → proceed.
* Any `critical` → **stop.** Read the `action` field; it names the remedy.
* `warn` → fix or rule it. A ruled item stops firing (dispositions are honoured).
* `info` lines are backlogs, not faults: insurer-priced/NFI-unpriced products,
  pending harvest failures, stale parsed runs.

`POST /pricing/data-quality/fix` applies only lossless normalizations. It will
never make a judgement call.

## 2. A new formulary publication arrives

1. Upload in **منابع** with the correct insurer selected. The mismatch guard
   compares the file's own name column against every insurer's known names and
   **blocks** a wrong selection (verified: a salamat file reports 100% salamat /
   0% tamin). Override only if you are certain.
2. Open the staged run in **اجراها**. Reuse is expected: rows whose code already
   carries an owner ruling resolve at tier 1 without review.
3. Review the queue, then apply. Applying writes:
   * live coverage for accepted links,
   * owner decisions for every reviewed row,
   * `origin='auto'` beliefs for the engine's own accepted links, and
   * an `auto_moved` list if the engine now sends a code somewhere new — **read
     that list**; it is the drift report.
4. Run §1.

**If a run was staged before a matcher fix**, don't re-upload — restage it:
`POST /pricing/coverage/runs/{run_id}/restage` rebuilds the staging from that
run's own snapshots with today's engine. No original file needed.

## 3. An NFI crawl

* **Ingest mode** (کاتالوگ دارو → برداشت به کاتالوگ) writes the catalog. Sticky
  fields protect coverage, prices, country and monographs from a blank crawl.
* **Audit mode** (پویش ممیزی) writes *nothing* to the catalog: it saves the raw
  source of flagged pages and the irc→page map. Tick **پویش دوباره** to
  re-observe pages already audited — that is the only way a page's IRC can be
  seen to change, which is the sole evidence a re-registration happened.
* A stopped run leaves a resume point on disk (survives a backend restart):
  «▶ ادامه از شناسهٔ N».
* Lost pages land in `harvest_failures`; «تلاش دوباره» retries only those.
* After ingest: run §1, then `POST /pricing/catalog/nfi/backfill-page-ids`.

## 4. The matcher changed (or you want to audit past decisions)

1. «بازبینی همهٔ تصمیم‌ها» — re-derives every stored decision with today's
   engine (~25 s for 4,700). Read-only.
2. Rule the disagreements:
   * **جابه‌جا شده** — engine picks something else. Compare the FS scores; a
     negative FS on the engine's side means keep yours.
   * **رد قابل بازنگری** — a past refusal the engine now matches confidently.
     Verify the actual product (form, strength, salt) before accepting.
   * **مقصد حذف شده** — the stored product is gone → succession candidate.
3. Every ruling is a training label. Tick «بازآموزی مدل پس از هر رأی», or refit
   once at the end via `POST /pricing/match-intel/retrain {"mode":"decisions"}`.
4. Re-export the canonical bundle (`POST /pricing/canonical/export`) — that is
   the rollback point for the session.

## 5. Price-gap triage (the rules used on 2026-07-29)

Classify before writing. Never refresh blind.

| observation | meaning | action |
|---|---|---|
| ratio ≈ `package_count` | insurer quotes the PACK, we quote the unit | **accept**; refreshing multiplies the unit price |
| `match_confidence < 0.90` and method not in {code, crosswalk, irc} | the MATCH is suspect, not the price | **defer** to «بازبینی تصمیم‌ها» |
| identity certain, reference > announced, licence lapsed or price ancient | our announced price is stale | **refresh**, increase-only, with provenance + history |
| reference < announced by >10× | our price is too HIGH — a unit/currency error | investigate before touching |

Refresh only via `record_price`; stamp `monograph.price_provenance` with source,
insurer, reason and previous value.

## 6. Spliced monographs (مغایرت داخلی NFI)

Run the auditor, apply repairs, then **run it again** — repairs change the
catalog vocabulary and expose a second wave. Iterate to a fixed point (2026-07-29:
164 → 28 → 0). A page that is genuinely spliced with no donor is ruled
`accepted`; the quarantine is the correct outcome, not a defect.

## 7. Where things live

| what | where |
|---|---|
| reconciliation checks | `services/core/drug_catalog/data_quality.py` |
| decision durability + revision | `decision_review.py`, `crosswalk.py` |
| IRC succession | `succession.py` |
| integrity gate + repairs | `nfi_integrity.py`, `nfi.py` |
| issue lanes and rulings | `issue_registry.py` |
| audit evidence | `logs/nfi_audit/` (index.jsonl, irc_page_map.csv, pages/) |
| canonical bundle | `data/canonical/` (gitignored, checksummed manifest) |
| fitted match model | `data/reference/match_model.json` |

## 8. Verification commands

```bash
/Users/sashad85/miniforge3/bin/python3 -m pytest tests/unit -q
cd frontend/workstation && npx tsc --noEmit
launchctl kickstart -k gui/501/com.pharmpilot.api && sleep 8 && \
  lsof -nP -iTCP:8001 -sTCP:LISTEN
```

Known baseline: `test_integrations_sandbox::test_notifications_sandbox_success_shape…`
fails in a full run and passes in isolation (caplog ordering). Everything else
must pass — 900 as of 2026-07-29.
