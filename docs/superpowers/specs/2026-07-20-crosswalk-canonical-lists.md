# Why incompatibilities recur — and the crosswalk that ends them

Status: PROPOSED (owner review)
Author: Fable 5 — 2026-07-20

## 1. What resolving an incompatibility actually does today

Nothing is written back to any insurer. Concretely, per resolution path:

| You do | What changes | What does NOT change |
|---|---|---|
| Approve a review pair | `drug_catalog.coverage[insurer]` gains covered/share/reference_price | tamin/salamat/NFI source files untouched |
| Approve an enrichment | `drug_enrichments` row → `approved`, exported to the git artifact | no source list touched |
| Confirm a bulk item | `item_kind='bulk'` → excluded from drug matching | no source list touched |
| Edit an NFI row | `drug_catalog` row corrected, `source='manual-edit'`, price dated | **the real NFI/IRC registry is unchanged** |
| Approve a price proposal | catalog price + `price_history` point | insurer tariff unchanged |

**So: the formularies are never modified.** We only ever change *our* copy and
*our* mapping. That is correct — they are other organizations' published
records and we have no write access — but it has a consequence.

## 2. Why the same incompatibilities come back

Every fix lands in a place the next import overwrites or bypasses:

- A **manual NFI edit** is overwritten by the next full NFI crawl (upsert by
  IRC rewrites name/form/strength from the source).
- A **coverage approval** is re-derived from scratch on the next harvest — the
  matcher re-runs on the same ambiguous names and can re-fail.
- The **only durable memory** we have today is `drug_enrichments` (git-versioned)
  and the fitted match model. Everything else is re-computed.

Root cause: **the three lists have no shared identity.** NFI has IRC (16-digit),
tamin publishes a 5-digit internal code, salamat publishes none. So every cycle
re-solves name-matching from zero. Fixing data downstream cannot fix an identity
problem upstream.

**Answer to "can we update the lists without incompatibilities recurring?"** —
Not by editing our copies. Only by making our *decisions* survive re-import, and
by anchoring on codes wherever a code exists.

## 3. The fix: a decision crosswalk (source-of-record separation)

Split what is *observed* from what is *decided*, and never let an import touch
the decided layer.

```
observed (re-importable, never hand-edited)
  nfi_snapshot        irc → fields, per crawl date
  formulary_snapshot  (insurer, list_date, source_code, raw_name, price, share)

decided (owner-owned, survives every import — the crosswalk)
  product            product_key (+ irc when known) = our canonical product
  crosswalk_entry    (insurer, source_code|raw_key) → product_key
                     + status(confirmed|rejected), decided_by/at, reason
  field_override     (irc, field, value, reason) — corrections that RE-APPLY
                     after every crawl instead of being overwritten
```

Rules:
1. Imports write **only** to snapshots. They may never mutate `decided`.
2. Matching consults `crosswalk_entry` **first**; a confirmed mapping is applied
   with confidence 1.0 and never re-litigated. The matcher runs only on rows with
   no crosswalk entry — so the review queue shrinks monotonically with your work.
3. `field_override` re-applies after each NFI crawl (a manual fix becomes
   permanent policy, not a value the next import erases).
4. Crosswalk keys prefer, in order: **IRC → insurer source_code → spelling-proof
   raw key**. tamin's 5-digit `drug_code` is stable across their publications, so
   keying on it makes tamin re-imports effectively zero-mismatch after one pass.
5. The whole `decided` layer exports to git-versioned JSON (same discipline as
   `drug_enrichments.json`) — rebuildable, reviewable, portable.

## 4. What the platform then outputs

With the crosswalk in place, PharmPilot can emit **our own canonical, updatable
lists** — the "impeccable storage" goal:

- `catalog.json` — canonical products (product_key, identity chain, overrides applied)
- `formulary_<insurer>.json` — that insurer's list *resolved to our product keys*,
  with price/share/validity dates
- `crosswalk.json` — every confirmed mapping + who decided it and why
- `price_history.json` — dated series (already built, Phase C)

These are versioned, diffable, and re-importable. A new tamin publication becomes
a **diff against the crosswalk**, not a re-match from zero: only genuinely new
rows reach review.

## 5. Effort and sequencing

| Step | Deliverable | Size |
|---|---|---|
| X1 | `crosswalk_entry` table + write on every approval (harvest + enrichment) | M |
| X2 | Linker consults crosswalk first (confirmed = ground truth) | S |
| X3 | `field_override` + re-apply hook in `upsert_catalog` | S |
| X4 | Snapshot tables for formulary imports (keep raw + list_date) | M |
| X5 | Canonical export bundle + re-import | M |

X1+X2 alone stop the recurrence for coverage decisions; X3 stops manual NFI
edits being erased. Those three are the high-value core.

## 6. Honest limits

- We can never prevent the *sources* from publishing inconsistent data; we can
  only ensure each inconsistency is resolved **once**.
- Rows with no code on either side still need one human decision the first time.
- If tamin renumbers its internal codes, those entries fall back to raw-key
  matching — the crosswalk degrades gracefully rather than breaking.
