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

Later the same day, three more root causes were found and fixed:

6. **JSONB `None` wrote a JSON null, not SQL NULL** — the model lacked
   `none_as_null=True`, so every "clear the coverage" path left a `'null'::jsonb`
   behind and 129 fresh ones appeared within hours of the first cleanup.
   *Fixed at the column, not by re-normalizing.*
7. **The reconciliation engine disagreed with the issue board** — my
   `price_gap_extreme_strong_identity` check used `ref > 10×ap` while the
   registry counts `abs(ref−ap) > 10×ap` (i.e. `> 11×`), inventing a permanent
   116-row phantom in the 10–11× band. *Fixed: one shared predicate.*
8. **A ruled item kept firing** — the spliced check ignored `issue_dispositions`,
   so an accepted, correctly-quarantined page still counted. *Fixed: rulings
   suppress the check while staying auditable.*

All judgement items are now ruled (see §6); reconciliation reports
`healthy: true`.

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
               → price-picks-form; ingredient floor; FS verification demotes;
               VOLUME guard + collision pass demote wrong-size presentations)
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

## 6c. Volume became part of product identity (2026-08-01)

«IOHEXOL 300 mg/1mL 10 mL» and «… 100 mL» share a molecule, a form, a route and
a dose set, so nothing downstream could tell them apart. 13 tamin rows collapsed
onto one volume-less stub priced 1,440 rial against references up to 25,000,000
(ratios to ×17,361).

`structural_match` now parses **fill volume** as its own dimension:
`volume_set()` strips the concentration denominator first (so «300 mg/1mL 50 mL»
is a 50 mL vial, not a 1 mL one), `record_volumes()` reads it from
`monograph.generic_full` — where NFI keeps it, never in `strength` — and
`volumes_agree()` treats **silence as agreement**, because 44% of liquid catalog
rows state no volume and refusing those would discard thousands of correct
matches.

Two mechanisms, because the data supports two kinds of evidence:

| | when it fires | measured on the two live formularies |
|---|---|---|
| `_volume_guard` | both sides state a volume and they differ | **273** links demoted (cisplatin 100 mL on a 10 mL record, bimatoprost 3 mL on 0.5 mL, adenosine 1 mL *and* 4 mL both on a 2 mL record) |
| `_volume_collision_pass` | the catalog row is silent, but rows naming different volumes all landed on it | **41** links demoted — including all 10 tamin iohexol rows |

The guard sits in `link_rows`, not only in the structural lane, because of a
measurement: of the 23 bad links left in the review queue, **zero** arrived
structurally — all came via the code join, the ingredient lane or a
crosswalk-derived guess. An exact IRC and an owner ruling are never demoted.

The collision pass deliberately skips records that DO state a volume: the guard
has already demoted exactly the contradicting rows there, and sweeping the group
would punish the correct claimant (piperazine 100 mL is right, 60 mL is wrong).

Effect: auto-applied links 4,677 → 4,375; the 302 difference moved to human
review with the reason in the method name. Nothing was discarded.

## 6b. The 2026-08-01 relinks

All 16 relink proposals were verified deterministically against the catalog —
same molecule, the strength/form/route/**volume** the formulary row names, and
a target price equal to the insurer reference to the rial. None was refuted.
The pattern was uniform: the matcher had fallen back to a family member when the
exact one was unreachable (lorazepam *injections* on a 1 mg oral *tablet*,
perphenazine 4 mg and 8 mg both on the 2 mg, three valproate syrups on the wrong
bottle volume).

Four were more than a strength correction:

* **clotrimazole** — the row is plain 1% vaginal cream; it was linked to
  clotrimazole **+ betamethasone**, an antifungal combined with a steroid.
* **ampicillin/sulbactam** — the row is the combination; it was linked to plain
  ampicillin 1 g.
* **glycine irrigation** — the old link's `name_fa` is «ایزوپروپیل الکل»
  (isopropyl alcohol) while its `generic_name` says glycine. Same mislabelling
  class as the immunoglobulin-called-albumin row.
* **loratadine** — the old link was a **synthetic NFI test row** («لورا تست»,
  IRC 1000020000300004). NFI publishes junk of its own (also a placeholder IRC
  1234567890123456 on four pages) and the harvester ingests it faithfully; three
  such rows were holding live salamat coverage at 70%.

Two design corrections came out of applying them:

1. **An owner relink is a statement about ONE product.** The first pass spread
   each entry across the target's ingredient group, as `apply_run` does — and
   because `ingredient_key` is generic|strength|form, **volume is not in the
   key**, so a 1,700,000 price for a 10 mL bupivacaine pack landed on 20 mL
   ampoules priced 19,000–38,857. The 179 spread entries were pulled back; only
   the 16 named products keep the entry.
2. **Flagging a bad row is not enough** — `repo.fetch_all` now withholds any row
   carrying `monograph.excluded` from the matcher, or the next import simply
   re-links it. A new critical check, `excluded_row_carrying_coverage`, watches
   for the state that started this.

## 6a. The 2026-07-30 apply

Both restaged runs were applied with `remove_missing`, after a full
`coverage_backup_20260730` snapshot (39,184 rows) was taken as the rollback point:

| | salamat | tamin |
|---|---|---|
| coverage entries written | 27,655 | 24,254 |
| **stale links retired** | **4,119** | **5,955** |
| review rows left undecided | 177 | 260 |
| auto beliefs refreshed | 1,923 | 1,694 |
| links that MOVED | 0 | 0 |

Two defects were fixed first, because applying would otherwise have caused harm:
the removal cap (above), and — found while checking the apply contract —
`record_run_decisions` turned **every** non-accepted review row into a durable
owner *rejection*, including rows nobody had looked at. That would have written
437 false rejections, each a permanent hard-0.0 block in the linker and a
negative training label it never earned. A rejection is now an act: only an
accepted row or one carrying an explicit reject reason becomes a decision.

Result: the weak+extreme class fell to **0** live, `price_gap_extreme` from 499
to **12** (all pack-basis), and the judgement lane from 527 to **15**.

## 6. Cleanup performed (2026-07-29)

Normalized 6,771 (+129 recurrence) JSON-null coverages · rejected the mislabeled
run `bdbdbc75` with an explanatory note · restaged both re-uploaded runs from
their snapshots under the fixed linker · backfilled `monograph.nfi_id` on
10,427 rows · recorded 3,846 auto decisions · ruled all 77 decision
disagreements and refit the model on 780 owner labels.

**Spliced monographs: 204 → 0.** `apply_repairs` was iterated to a fixed point
(164, then 28 exposed by the repaired vocabulary, then none). Two aluminium
hydroxide rows are *genuine* splices with no donor — a chewable tablet served a
suspension monograph — and are ruled `accepted`: the quarantine is the correct
outcome, and the clinical text stays withheld until a future crawl serves the
right page.

**The "weak match" class was an artifact of stale coverage.** Comparing live
coverage against the restaged runs settled all 575 weak+extreme pairs: **301**
were links today's engine no longer makes at all (left over from the superseded
07-25 runs), **251** are now matched with certain identity (so the gap is a price
question, not a match question), **23** were fixed outright, and **0** remain.
Applying the restaged runs would land **zero** weak+extreme entries, because
sub-0.75 links never reach `staged` — they wait in the review queue behind the
human gate.

That comparison exposed a defect of its own: `apply_run` retired stale coverage
from `diff.samples.removed`, which `compute_diff` caps at 50 **for display**. A
run reporting 6,000 retired links could clear only 50, so a link the matcher had
stopped making survived every later import. Measured before the fix: 4,119
salamat + 5,955 tamin such entries were live. `remove_missing` now re-derives the
full set, and `coverage_orphaned_by_newer_staging` watches it permanently.

**Extreme price gaps: 2,880 products → 0 open.** Every gap was classified
deterministically before anything was written:

| class | products | ruling | why |
|---|---|---|---|
| stale announced price, identity certain | 2,339 | **resolved** — refreshed from the insurer reference | licence lapsed or price ancient; increase-only, `price_provenance` stamped, SCD-2 history row per product |
| insurer reference is a PACK price | 26 | **accepted** — never refresh | ratio ≈ `package_count` (×12 with pc=14, ×32 with pc=30); the two sides use different units, so "fixing" it would multiply the unit price |
| weak match (conf < 0.90, method not code/crosswalk/irc) | 499 | **deferred** to match review | the gap is evidence the MATCH is wrong, not the number — refreshing would bake a wrong price into a wrong product |

The refresh rule, stated exactly: *increase-only, strong identity only
(`conf ≥ 0.90` or method ∈ {code, crosswalk, irc}), never a pack-basis row, skip
if the row moved under us, highest-confidence insurer wins and ties take the
LOWER reference.* Every write carries provenance and history — the
`price_refreshed_without_history` check now enforces that permanently.

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
| ~~P1~~ **done** | 77 decision disagreements ruled · 204 spliced monographs repaired · 2,880 price gaps resolved/ruled | ✅ reconciliation `healthy: true`, 0 warn |
| ~~P1b~~ **done** | the 499 weak-match products: **none survives**. 301 were stale links from the superseded 07-25 runs, 251 now carry certain identity, 23 fixed outright, 0 residual | ✅ verified against the restaged runs |
| ~~P1d~~ **done** | both restaged runs applied with remove-missing: 51,909 coverage entries written, **10,074 stale links retired**, 487 further stale prices refreshed | ✅ reconciliation `healthy: true`; price_gap_extreme 499 → 12 |
| ~~P1c~~ **done** | all 16 relink proposals verified against the catalog and applied as owner decisions; 3 synthetic NFI test rows withdrawn from matching | ✅ reconciliation `healthy: true` |
| P1e (owner) | ~~16 proposed relinks for the review queue~~ — **proposals only, not adversarially verified** (`docs/review-2026-07-30-weak-link-adjudication.json`); 36 of the 68 queue rows are still unadjudicated | each proposal confirmed or rejected in «اجراها» before the runs are applied |
| P2 (proxy-dependent) | finish crawl 43k–70k; retry 631 failures; second audit pass over covered ground | succession detector armed |
| P3 | schedule reconciliation after every apply/ingest automatically; surface the report as a panel card | report visible without CLI |
| P4 | quote-path regression harness (pricing conservation against golden quotes) | release-check includes it |

Risks: NFI reachability (mitigated: resume + failure registry + evidence-only
audit); formulary format drift (mitigated: role inference + mismatch guard +
staged review); matcher regressions (mitigated: decision durability + revision
panel + FS training loop).
