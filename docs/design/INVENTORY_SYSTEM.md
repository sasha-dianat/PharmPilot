# Smart Inventory Management — recovered requirements, gap analysis, architecture

Status: **Phase 1 implemented and verified; Phases 2–5 designed, not built.**
Owner: Claude Code (workstream CL-003) · Branch `feat/inventory-integrity`
Date: 2026-08-02 · Migration head `0030`

This document is deliberately explicit about what is *built and tested* versus
what is *designed*. Section 9 is the production-readiness assessment and it does
not claim more than the evidence supports.

---

## 1. Discovery — what was actually there

Every number below was measured against the live database on 2026-08-02, not
inferred from documentation.

| Table | Rows | Note |
|---|---:|---|
| `drug_catalog` (IRC, Iranian) | 39,184 | the real formulary |
| `drug_products` (NDC, US) | 16 | demo seed: Lipitor, Glucophage… |
| `inventory_lots` | 17 | |
| `stock_levels` | 16 | |
| `inventory_movements` | 8 | |
| `prescription_fills` | 46 | |

Existing code worth preserving (all retained, none rewritten):

- `services/core/inventory/{movements,procurement,replenishment,stock_intelligence}.py`
  — pure, unit-tested rules. Good separation already.
- `services/platform/routers/depot_transfer.py` — depot→shelf dual verification
  with 21 guard tests, cold-chain and LASA checks, pharmacist attestation.
- `services/platform/routers/inventory_movements.py` — append-only ledger,
  correctly tenant-filtered.
- `services/ai/inventory_intelligence/forecaster.py` — Prophet with a
  deterministic fallback (Prophet is **not installed**; the fallback is what
  runs).
- `services/ai/inventory_intelligence/anomaly_detector.py` — IsolationForest +
  CUSUM, 29 KB, **not referenced by any router or task**: dead code today.

---

## 2. Recovered requirements and traceability matrix

Sources: `docs/ROADMAP.md`, `docs/MASTER_PROMPT_intelligent_services.md`,
`docs/superpowers/specs/2026-06-14-depot-shelf-transfer-verification-design.md`,
the migration history (0012, 0018, 0028), and the user's brief of 2026-08-02.

Status key: **DONE** = implemented and tested this session · **PRE** = pre-existing
and verified present · **GAP** = designed here, not built.

| ID | Requirement | Source | Status | Evidence |
|---|---|---|---|---|
| R1 | Stock decrements when a prescription is dispensed | ROADMAP:34 | **GAP→P2** | `check_fills_without_movements` now *detects* it: 46 firing |
| R2 | Reconciliation between dispensing, depot/shelf and stock | ROADMAP:34 | **DONE** | `reconciliation.py`, 11 checks, 28 tests |
| R3 | Batch/lot traceability for recall | brief §2 | **DONE (schema)** | `prescription_fills.inventory_lot_id` FK, migration 0030 |
| R4 | Inventory bound to the real formulary | brief §4, formulary integration | **DONE (schema+resolver)** | `irc` on 4 tables; `formulary_binding.py`, 12 tests |
| R5 | Expiry control | MASTER_PROMPT ⑫ | **PRE + DONE** | `expiry_prevention.py`; `check_expired_on_hand` found 2 live |
| R6 | Demand forecasting / reorder | MASTER_PROMPT ⑫⑰ | **PRE** | `forecaster.py` (fallback path active) |
| R7 | Supply-risk / shortage prevention | MASTER_PROMPT ⑰ | **PRE** | `supply_warning.py` |
| R8 | Turnover / dead-stock | prior session | **PRE** | `stock_intelligence.py`, 4 tests |
| R9 | Depot→shelf dual verification | 2026-06-14 spec | **PRE** | `depot_transfer.py`, 21 guard tests |
| R10 | Append-only audit ledger | prior session | **DONE (hardened)** | trigger blocks UPDATE/DELETE; verified live |
| R11 | Tamper evidence on stock history | brief §2 | **DONE** | SHA-256 chain, `verify_chain`, 4 tests |
| R12 | Role-based access | AGENTS invariants | **DONE (extended)** | `inventory:approve` added; requester ≠ approver |
| R13 | Approval workflow for high-risk changes | brief §3 | **DONE** | `inventory_approvals` + 7 guard tests |
| R14 | Controlled-drug witness | brief §2 | **DONE** | witness ≠ requester ≠ approver, enforced + CHECK constraint |
| R15 | Physical / cycle counting | brief §4 | **DONE** | `stock_counts`, blind counting, variance→approval |
| R16 | Anomaly detection (theft, diversion) | brief §2 | **PARTIAL** | rules-based `check_suspicious_adjustments` (7 tests); ML detector still unwired |
| R17 | Negative-stock prevention | brief §4 | **DONE** | CHECK constraint + no-clamp ledger |
| R18 | Unit-conversion error detection | brief §4 | **DONE** | `check_unit_conversion`, 2 tests |
| R19 | Duplicate/invalid record detection | brief §4 | **DONE** | `check_duplicate_lots`, 2 tests |
| R20 | Bulk edit / fast admin search | brief §1 | **DONE** | `inventory_admin.py` — one search box (name/IRC/NDC/GTIN/lot), 12 named filters with live counts, 6 sorts, bounded bulk edit; 42 tests |
| R21 | Formulary sync versioning/rollback | brief §1 | **PRE** | `formulary_snapshots` (50,232), `price_history` SCD-2, `catalog_succession` |
| R22 | Barcode / GTIN receiving | brief §2 | **PARTIAL** | receiving endpoint live and ledger-backed; search box already classifies a scanned GTIN; hardware scan-to-field still P2 |
| R23 | RFID / sensors / CV | brief §2 | **GAP→P4/P5** | see §6 division of responsibility |
| R24 | Surveillance integration | brief §2 | **GAP→P4** | `docs/design/SURVEILLANCE_PLATFORM.md` (unapproved) |
| R25 | Guided picking / layout optimisation | brief §3 | **PARTIAL** | lots shown in FEFO order with blocked-reason per lot; picking route UI still P3 |
| R26 | Fair operational KPIs | brief §3 | **DONE (principle)** | patterns attach to items, escalate to a reviewer, never score a person |
| R27 | Supplier selection / cash flow / margin | brief §1 | **PRE/GAP** | `procurement.py` exists; multi-supplier optimisation not built |
| R28 | Correction audit: who changed what, when, why | brief §1,§4 | **DONE** | every edit carries a mandatory reason; movement history rendered per item with actor and chain marker |
| R29 | Goods receipt as a ledger event | brief §4 | **DONE** | `POST /admin/receive` → RECEIPT movement; past-dated expiry refused; one lot number cannot hold two expiries |
| R30 | Admin panel cannot bypass inventory controls | derived | **DONE** | quantity fields unreachable from the panel (4 parametrised tests); sensitive edits routed to approval |

---

## 3. Gap analysis — defects found, with evidence

| # | Defect | Severity | Evidence | Disposition |
|---|---|---|---|---|
| D1 | Inventory keyed to a 16-row US demo catalog while the real formulary has 39,184 IRC rows; **no `irc` column existed in any inventory table** | Critical | schema dump | **Fixed** (0030) + resolver |
| D2 | Dispensing never decremented stock | Critical | 46 fills / 8 movements; no writer in the Rx path | **Detected**; hook is P2 |
| D3 | `PrescriptionFill.lot_number` free text, no FK → recall unanswerable | High | schema | **Fixed** (FK added) |
| D4 | `POST /orders/{po_id}/submit` had no tenant filter → cross-tenant PO submission | High | `inventory.py:306` | **Fixed** |
| D5 | `max(0.0, …)` clamp on the aggregate hid shrinkage | High | `inventory_movements.py:95` | **Superseded** by no-clamp ledger + CHECK |
| D6 | "Append-only" was a comment; UPDATE/DELETE were permitted | High | no trigger existed | **Fixed** — verified blocked live |
| D7 | No maker-checker; one `inventory:write` holder could zero any lot | High | permission table | **Fixed** |
| D8 | No physical-count entity → nothing to reconcile against | High | schema | **Fixed** |
| D9 | ML anomaly detector never called | Medium | grep across `services/` | Documented; P3 |
| D10 | `/stock?expiring_days=` accepted and silently ignored | Medium | `inventory.py:109` | **Fixed** |
| D11 | 2 expired lots (240 units) still sellable and not quarantined | Critical | live report | **Surfaced** — owner action |

---

## 4. The decisive architectural finding: IRC is not a molecule code

Name-based binding was measured against the real formulary:

```
GTIN coverage                                36,873 / 39,184 = 94.1%
(generic, strength, dosage_form) groups               3,462
  … resolving to exactly ONE irc                 665 = 19.2%
  … mean candidates per group                          11.3
  … worst case                                          222
```

An IRC is a **per-brand, per-manufacturer registration**, so amlodipine 5 mg
tablet legitimately has 209 IRCs. Text matching therefore cannot bind stock to
the formulary — it is ambiguous 80.8% of the time, and a wrong bind attaches a
wrong price and a wrong insurer coverage to real stock.

**Consequence for the design:** the GTIN barcode on the carton is the primary
binding key, captured at goods-receipt. Name matching is a fallback for the
5.9% of formulary rows with no GTIN, and it must stay proposal-only. The
resolver already implements this ladder and refuses to guess (verified: 15 of
16 stocked products correctly reported ambiguous rather than mis-bound).

---

## 5. Architecture

```
                    ┌─────────────────────────────────────────┐
   scan / count →   │  services/core/inventory/  (pure)       │
                    │   ledger.py         FEFO, conservation, │
                    │                     hash chain          │
                    │   reconciliation.py 11 integrity checks  │
                    │   formulary_binding.py  GTIN→IRC ladder │
                    │   movements/procurement/replenishment/  │
                    │   stock_intelligence  (pre-existing)    │
                    └──────────────┬──────────────────────────┘
                                   │ plans / findings (no I/O)
                    ┌──────────────▼──────────────────────────┐
                    │  routers/inventory_integrity.py         │
                    │   fetch → apply rule → persist → chain  │
                    └──────────────┬──────────────────────────┘
                                   │
   inventory_lots ─ stock_levels ─ inventory_movements (append-only, chained)
        │                                │
        └── stock_counts/lines           └── inventory_approvals (maker-checker)
                                              └── prescription_fills (lot FK)
```

**Boundaries.** Rules never touch the database; the router never decides. A
check never repairs. A count proposes; an approval disposes. Stock changes only
via `ledger.plan_*` → `append_movement`.

**Data model additions (migration 0030).** `irc` on `inventory_lots`,
`stock_levels`, `purchase_order_lines`, `inventory_movements`;
`prescription_fill_id`, `approval_id`, `prev_hash`, `event_hash` on movements;
`inventory_lot_id` on `prescription_fills`; tables `inventory_approvals`,
`stock_counts`, `stock_count_lines`; append-only trigger; non-negative CHECK.
All additive and nullable — round-trip verified up/down/up.

---

## 6. Division of responsibility

The rule: **the cheapest reliable control wins, and nothing automated decides a
disciplinary or clinical outcome.**

| Concern | Mechanism | Why this one | Human role |
|---|---|---|---|
| Which product is this? | **GTIN barcode** | 94.1% coverage; unambiguous where name matching is 19.2% | Resolve unbound/ambiguous items |
| How many are there? | **Blind physical count** | The only ground truth | Counts; recounts variances |
| Did the books change legitimately? | **Hash-chained ledger + DB trigger** | Deterministic, cheap, court-legible | Investigates a break |
| Is this write-off honest? | **Maker-checker + witness** | Two named people beats any model | Approves/rejects |
| Which lot leaves first? | **FEFO rule** | Deterministic; expiry is the binding constraint | Overrides with a reason |
| Is a pattern forming? | **Rules first** (`check_suspicious_adjustments`) | Explainable, no training data needed | Reviews the pattern |
| Subtle demand shifts | **ML forecast** (advisory) | Non-linear seasonality | Approves every purchase order |
| Cold chain | **Sensors** | Continuous, cheap | Acts on a breach |
| High-value / controlled area | **RFID + video** (P4/P5) | Only worthwhile above a value threshold | Reviews an incident |

ML is advisory everywhere. No model approves a write-off, orders stock, or
produces an employee score. Patterns attach to **items and events**, and
escalate to a named reviewer — §26 of the requirements matrix.

---

## 7. Forecasting and anomaly methodology

**Currently active (deterministic).** `_fallback_forecast` — trailing mean
demand with a trend test; reorder point = `demand × lead_time + safety_stock`.
Prophet is imported optionally and is **not installed**, so it never runs. That
is stated here because a forecast whose engine is silently absent is worse than
no forecast.

**Anomaly, active.** Rules with explicit thresholds, each explainable in one
sentence: write-down ≥ 25% of stock; any controlled-substance write-down; ≥ 3
write-downs by one person on one item in 30 days. Output names the pattern, the
evidence and the actor — as a question for a reviewer, never a conclusion.

**Confidence.** Binding proposals carry a calibrated ladder (0.99 GTIN → 0.78
unique-generic) and refuse to emit below 0.78. Reconciliation findings carry
severity, not probability, because they are deductions from the data, not
predictions — an aggregate that disagrees with its lots is wrong with certainty.

**Not yet trustworthy.** The IsolationForest detector is unwired and untested;
until demand history is real (it cannot be, while R1 is open) any statistical
model is fitted to a ledger that never decrements. **Forecasting quality is
blocked on R1, not on model choice.**

---

## 8. Phased rollout

| Phase | Scope | Cost tier | Exit criteria |
|---|---|---|---|
| **P1 — done** | Ledger, reconciliation, counts, approvals, chain, formulary binding, schema | None (software) | 70 tests green; report runs on live data |
| **P1b — done** | Admin panel: search/filter/sort/paginate, item drill-down with lots + full movement history, single and bulk correction, goods receipt, write-off request | None (software) | 42 further tests green (31 policy + 11 end-to-end on a real database); all 8 endpoints served |
| **P2** | Dispense hook (R1); GTIN scan at receiving (R22); backfill 46 orphan fills as an approved batch | Barcode scanners ~$40 ea | `fill_without_movement` = 0 and stays 0 for 14 days |
| **P3** | Wire ML detector; guided picking UI; bulk editor; supplier optimisation | None | Detector precision ≥ 0.6 on labelled history |
| **P4** | Cold-chain sensors; controlled-area video tie-in | Sensors ~$60/fridge | Breach → quarantine within 5 min |
| **P5** | RFID for high-value/controlled stock | ~$0.15/tag + readers | Only if P2–P4 shrinkage justifies it |

**KPIs.** Ledger accuracy (count variance rate), % stock bound to formulary,
service level (lines filled without stockout), expired write-off value / COGS,
days-of-supply distribution, approval turnaround, chain integrity (binary).

**Risks and limitations.**
- Demand history is unusable until R1 lands; every forecast KPI is provisional.
- The 16-row `drug_products` catalog remains the operational product master for
  legacy paths; the `irc` columns bind alongside it rather than replacing it.
  A full cutover is a separate, larger migration.
- Approvals are enforced in the service layer plus two CHECK constraints; a
  direct SQL writer with database credentials can still bypass them.
- The append-only trigger can be dropped by a superuser. It raises the cost of
  tampering; it does not make it impossible.

---

## 9. Production-readiness assessment

**Ready for production use now:** the reconciliation report, the ledger chain
verification, the approval workflow, the physical-count workflow, the schema,
and the tenant/permission fixes. These are additive, tested (70 new tests), and
verified against real data.

**Not production-ready, and must not be described as such:**

- **Inventory quantities themselves.** The live report returns
  `blocking = true`: 46 fills never decremented stock and 2 expired lots are
  still sellable. Until P2 lands and a full count is posted, the numbers in
  `stock_levels` describe history, not the shelf.
- **Forecasting and replenishment.** Fitted on a ledger that only increases.
- **Anomaly ML.** Unwired, untrained, untested.
- **Everything in P4/P5** — RFID, sensors, video analytics — is design only.

**Acceptance criteria for calling inventory production-ready:** `blocking =
false` on the reconciliation report for 14 consecutive days, with a posted full
physical count, `fill_without_movement = 0`, and ≥ 95% of stocked items bound to
the formulary by GTIN.
