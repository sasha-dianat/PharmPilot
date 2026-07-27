# The Medication & Pricing Database — End-State Design (هوش‌یار دارو v2)

Status: PROPOSED (owner review)
Author: Fable 5 (architect) — 2026-07-17

## North star

One sentence: **every product a pharmacy can buy, dispense, or bill — uniquely
identified, fully attributed, price-dated, insurer-mapped — rebuildable from
evidence, fast at national scale.**

## Design pillars

### P1 — A product identity spine (product_key)
Identity today rides on NFI's IRC + fuzzy names. Elegant end-state: a
**deterministic product_key** computed from the normalized identity chain we
have converged on empirically:

    generic + components[] + dosage_form + route + strength + concentration
    + pack_size + container/pen + brand

- Same normalization everywhere (enrich_key discipline, mg-normalized units).
- IRC stays authoritative when present; product_key is the neutral join spine
  when it isn't (formulary rows, supplies, compounding items).
- Collisions surface in review, never auto-merge.

### P2 — Criteria forecasting: dimensions as DATA, not columns
Every correction so far (pack → route → concentration → container → pen →
components) followed one pattern: *a new identity dimension appeared*.
Forecast the pattern, not the specific criteria:

- **Dimension dictionaries** (tables, owner-editable): dosage-form taxonomy
  with hierarchy (gel → topical|vaginal; injection → solution|concentrate|
  powder), route, container/pen registry, unit registry.
- Variants stay **JSONB attrs** validated against the dictionaries — a future
  criterion (flavor, ampoule count, cold-chain flag, biosimilar reference,
  pediatric/adult) is a dictionary row + prompt rule + matcher token map.
  **Zero migrations for new criteria** — that is the stability guarantee.
- Governance rule: new dimension ⇒ (dictionary entry, prompt rule, matcher
  tokens, one pinned test). Four artifacts, one afternoon, no schema risk.

### P3 — Layered truth (rebuildable from evidence)
    L0 evidence   raw harvests, HTML/xls, diagnostics (immutable)
    L1 catalog    NFI-authoritative products (IRC)
    L2 reference  owner-approved enrichments + variants (git-versioned artifact)
    L3 overlays   per-insurer coverage, tariffs, IR-pricing config
    L4 derived    matching indexes, materialized views (disposable)
Only L4 is disposable; L1–L3 are append/versioned; everything below L4
reconstructs deterministically from L0+decisions. No LLM output ever writes
below L4 without an owner decision (standing invariant).

### P4 — Prices as time-series, never overwrites
`price_history(irc/product_key, price_type, value, valid_from, valid_to,
source, evidence_ref)` — announced, invoice, insurer-reference tariffs.
- Quotes pin to a date → auditability + receipt-matching.
- Stale-price detection becomes a QUERY (age > threshold), which is exactly
  the known ~2,410-gap problem, solved structurally instead of by re-crawl
  heroics.

### P5 — Matching as a scored, learning pipeline
Current: blocking + ratio + variant narrowing (good). End-state:
1. candidate generation (pg_trgm + ingredient-prefix blocks),
2. feature scores: name similarity, mg-normalized strength, form-taxonomy
   distance (topical-gel vs vaginal-gel = near, tablet = far), pack match,
   pen/container tokens, components overlap,
3. calibrated confidence → auto / review / reject bands,
4. **match_audit** table records every decision + features → thresholds tuned
   from the owner's actual approvals (the review queue becomes training data
   without any model — just calibration curves).

### P6 — Fast at national scale
- GIN on variants/attrs JSONB, pg_trgm on normalized names, ingredient_key
  b-tree (exists), materialized matching view refreshed post-approval.
- Target: full 40k-catalog × 10k-row formulary re-link < 30 s; quote < 50 ms.

### P7 — Governance loop (already strong — close it)
- Artifact export gains counts + checksum in the commit body.
- Nightly drift check: live coverage vs artifact vs price_history → workbench.
- Provenance invariant unchanged: suggestion → owner approval → self-apply.

## Phased roadmap

| Phase | Deliverable | Size |
|---|---|---|
| A | Dimension dictionaries + form taxonomy; validator wired to them | S |
| B | product_key + collision review; backfill catalog & enrichments | M |
| C | price_history + dated quotes; stale-price query in workbench | M |
| D | scored matcher + match_audit; calibrate from existing approvals | M |
| E | GIN/trgm indexes + materialized view; perf gates in release-check | S |

Order matters: A before B (keys need vocabularies), C independent, D after B,
E last. Each phase lands behind tests + the existing review gates.
