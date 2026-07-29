# PharmPilot drug-data architecture — canonical model, pipelines, and runbook

*Audited and reconciled 2026-07-29 (Jalali 1405/05/07). Alembic head `0029`.
This is the source-of-truth description of how formulary, NFI, and
reimbursement data is stored, updated, and kept honest.*

## 1. Diagnosis — what the full audit found (and what was already sound)

Verified sound (counts at audit time):

| invariant | state |
|---|---|
| IRC uniqueness | 39,184 rows, **0 duplicates, 0 empty** |
| decided-layer referential integrity | 0 dangling crosswalk IRCs, 0 dangling overrides, 0 confirmed-without-IRC |
| observed-layer attribution | 0 orphaned snapshots across 50,232 rows |
| schema vs models | no drift; every model table exists, extra DB tables belong to other bounded contexts |
| coverage arithmetic | 0 rows with share > 100% |

Defects found and fixed the same day:

1. **Insurer-mismatch guard was a no-op on real files** — it read `drug_name`
   off raw rows, but real دارونامه columns are Persian («عنوان»); 0 of 3,679
   names visible → a salamat export staged as tamin. *Fixed: roles resolved
   before the check; the run rejected.*
2. **Name-key spreading of coded decisions** — one ruling swept every product
   sharing salamat's truncated name (222 names → 613 codes, «IOHEXOL» 14→1).
   *Fixed: a coded decision publishes only its code key.*
3. **Nondeterministic conflict resolution** — 6 codes carried contradicting
   owner rulings; the winner was dict iteration order. *Fixed: latest
   `decided_at` wins, confirmed outranks rejected on ties.*
4. **6,771 JSON-null coverage values** (vs SQL NULL) made `IS NOT NULL`
   queries lie. *Fixed: normalized; `fix()` keeps it that way.*
5. **WebSocket auth left every client behind** — all live streams refused
   (403) since the routes gained ticket auth. *Fixed: `wsUrl()` mints a
   ticket per attempt; verified 101 on all three streams.*

Remaining judgement items (in their panels, not silently pending): 204
spliced monographs, 6 owner-conflict codes, 1,159 insurer-priced/NFI-unpriced
products, 631 retryable harvest failures.

## 2. Canonical model and source-of-truth strategy

One rule generates the whole schema: **Observed is replaceable, Decided is
durable, and nothing may be both.**

```
OBSERVED (imports overwrite freely)          DECIDED (owner-owned, survives everything)
──────────────────────────────────           ──────────────────────────────────────────
drug_catalog        ← NFI crawls             crosswalk_entries   insurer row ⇔ IRC
formulary_snapshots ← formulary uploads        (origin: owner | auto)
price_history       ← price movements        field_overrides     per-IRC field corrections
logs/nfi_audit/*    ← page evidence          drug_enrichments    researched identities (approved)
harvest_failures    ← transport losses       issue_dispositions  triage rulings
                                             catalog_succession  old IRC → new IRC
```

Identity anchors, in trust order:
1. **IRC** — product identity; every decided fact hangs on it.
2. **National code** (`generic_code` ≡ tamin `drug_code` ≡ salamat
   `generic_code`) — the cross-insurer Rosetta stone; one confirmation per
   code resolves it for every insurer.
3. **NFI page id** (`monograph.nfi_id`) — registration identity; the only
   anchor that survives a re-registration (measured: 0 of 10,487 pages carry
   two IRCs). GTIN was evaluated and rejected (placeholder-ridden; one value
   spans 77 IRCs / 33 molecules).

Truncated names are **never** an identity — only a last-resort key when an
insurer publishes no code.

## 3. Ingestion, normalization, validation

Every source enters through one pipeline; there is no side door:

```
fetch/upload → rows_from_upload/fetch_rows → merge_row_sets (multi-file, by code)
  → insurer-mismatch guard (roles resolved FIRST; force to override)
  → resolve_roles / normalize_rows (Persian/English aliases, BOM, multi-tier cells)
  → link_rows (crosswalk short-circuit → code join → structural → ingredient
               → price-picks-form; ingredient floor; FS verification demotes)
  → stage_run_payload: CoverageRun {stats, staged, review, unmatched, diff}
  → save_snapshots (full observed rows + per-row engine verdict)
  → HUMAN GATE: apply_run — nothing touches live coverage before it
```

NFI ingest mirrors it: coherence gate quarantines spliced pages, sticky
fields + `_ZERO_IS_ABSENT` stop blank/zero crawls erasing data,
`apply_overrides` re-asserts owner corrections on every upsert, audit mode
gathers evidence without writing the catalog at all.

## 4. Conflict resolution and reconciliation rules

* Owner ruling > auto belief — an auto row can never overwrite an owner row.
* Auto beliefs are recorded but **never consulted by the linker** (a stored
  0.76 guess must not freeze into a 1.0 certainty).
* Same code, several rulings → latest `decided_at` wins; ties prefer
  confirmed. Conflicts are also a reconciliation check.
* Coded decisions bind only their code; name keys only for codeless insurers.
* A moved auto belief is reported (`auto_moved`), never silently swapped.
* Succession carry-over is **additive only** — a wrong application can add,
  never destroy.

Automated reconciliation: `data_quality.report()` runs 15 invariant checks
(each with severity, invariant text, and remedy); `fix()` applies only
lossless normalizations. `GET /pricing/data-quality/report`.

## 5. Versioning, rollback, provenance

* **Versioning** — snapshots are append-only per run; `price_history` keeps
  every price movement; the canonical bundle (`data/canonical/`) is a
  checksummed on-disk export of the decided layer (gitignored — 25 MB; the
  manifest carries per-file sha256, and re-export after every ruling session
  is the backup discipline).
* **Rollback** — decided layer: re-import the canonical bundle. Observed
  layer: re-run any import; `restage_run` rebuilds a run from its own
  snapshots without the original file. Schema: every Alembic migration has a
  real `downgrade()`.
* **Provenance** — `price_provenance` / `country_provenance` / succession
  trails in `monograph`; `decided_by/decided_at/origin/revised_from_irc` on
  every decision; per-row engine verdicts on every snapshot.
* **Historical comparison** — «بازبینی تصمیم‌ها» re-derives every stored
  decision with today's engine and classifies agree/moved/lost/stale/revived;
  revisions become training labels for the FS model.

## 6. Cleanup performed (2026-07-29)

Normalized 6,771 JSON-null coverages · rejected the mislabeled run
`bdbdbc75` with an explanatory note · restaged both re-uploaded runs from
their snapshots under the fixed linker · backfilled `monograph.nfi_id` on
10,427 rows · recorded 3,846 auto decisions.

## 7. Tests

`tests/unit/test_decision_durability.py` (24) — decision invariants,
succession, reconciliation, precedence, the mismatch guard.
`test_nfi_audit_mode.py` (11) — audit/resume. Plus the existing linker,
importer, integrity, price and harvest suites: **900+ tests**; the full run
gates every commit (`/release-check`).

## 8. Monitoring and operations

* Reconciliation report — run after every import/crawl; `healthy: false`
  with a non-info severity means stop and read the `action` line.
* Harvest diagnostics — per-fetch categories with Persian hints; failures
  land in `harvest_failures` for targeted retry.
* Runbook, per event:
  * **New formulary publication** → upload via منابع (guard verifies the
    insurer) → review the staged run → apply → decisions accumulate; only
    genuinely new/changed rows need review.
  * **New NFI crawl** → ingest mode; sticky fields protect decided data;
    then run the reconciliation report.
  * **Matcher changed** → «بازبینی همهٔ تصمیم‌ها» → rule on disagreements →
    optional refit. Or `restage_run` on any parsed run.
  * **Product re-registered** → audit re-scan (پویش دوباره) → «جانشینی IRC»
    proposals → apply carries everything over.
  * **Something looks wrong** → `data-quality/report` first; every check
    names its remedy.

## 9. Phased plan — remaining work

| phase | work | acceptance |
|---|---|---|
| P1 (owner, now) | rule on 6 conflict codes + 75 revived rejections; repair 162 auto-repairable spliced monographs | reconciliation shows 0 warn |
| P2 (proxy-dependent) | finish crawl 43k–70k; retry 631 failures; second audit pass over covered ground | succession detector armed |
| P3 | schedule reconciliation after every apply/ingest automatically; surface the report as a panel card | report visible without CLI |
| P4 | quote-path regression harness (pricing conservation against golden quotes) | release-check includes it |

Risks: NFI reachability (mitigated: resume + failure registry + evidence-only
audit); formulary format drift (mitigated: role inference + mismatch guard +
staged review); matcher regressions (mitigated: decision durability + revision
panel + FS training loop).
