# Inventory simulation and adversarial validation

**Date:** 2026-08-14 · **Branch:** `feat/inventory-integrity`
**Operating principle:** assume hidden bugs exist; try to falsify inventory
correctness rather than demonstrate the happy path.

---

## 1. Why a simulator

There is no real pharmacy data yet. Every number in the system so far came from
a seed script, and the previous audit found that seeded numbers had been
mistaken for measurements more than once. A simulator gives three things a
fixture cannot: volume, adversarial sequences nobody would think to write by
hand, and a **second, independent opinion** about what the stock should be.

## 2. Architecture

```
tests/simulation/
  world.py       synthetic catalogue from a seed — SKUs, lots, demand archetypes
  oracle.py      ground truth. Imports NOTHING from services.core.inventory
  driver.py      applies each event to the real application AND to the oracle
  invariants.py  the comparisons, plus structural checks on the database alone
  runner.py      escalating phases 1-5
tests/unit/test_inventory_simulation.py   phases 1-5 at small scale, in CI
scripts/inv_simulate.py                   sweeps, concurrency, long horizon
```

### The oracle is independent in two ways

**Different source.** It is written from the invariant equations —
`total = on_hand + damaged + returned + in_transit`, `available = on_hand -
reserved`, `total = receipts - issues ± authorised adjustments` — not from the
implementation. It imports no application inventory code.

**Different algorithm.** The application keeps running balances and updates them
in place. The oracle keeps an append-only event log and **recomputes every
balance from zero** whenever asked. A drift bug in an incremental updater cannot
exist in a full replay, so the two reach the same number by different routes and
a shared bug is unlikely.

### The apparatus was itself tested

A simulator that finds nothing may be working or may be blind. Three checks:

1. **Non-triviality assertions** — the CI test fails unless the run produced
   ≥100 events, ≥10 check rounds, ≥10 lots, ≥20 dispenses.
2. **Can-it-fail** — raising a threshold to an impossible value produced
   `assert 125 >= 100000`, confirming the body executes.
3. **Mutation test** — `pick_fefo` was patched to under-allocate by `0.001`, a
   quantity no eyeball would catch. The simulator reported it within 85 events
   with exact lot ids and expected-vs-actual. Reverted immediately.

Coverage of event kinds is asserted rather than assumed: a probe confirmed that
`RECEIPT`, `DISPENSE`, `COUNT_GAIN`, `COUNT_LOSS`, `TO_BUCKET` and `FROM_BUCKET`
were all genuinely exercised. Two of them silently were not on the first
attempt — see D3.

## 3. Scenarios and seeds

| Phase | What it does |
|---|---|
| 1 known answers | receive 100 → dispense 30 → damage 10 → approved write-off. Hand-computable |
| 2 normal | 12–30 simulated trading days across six demand archetypes |
| 3 edges | zero, negative, expired, fractional, 10^7, single-unit; over-dispense; over-write-off; whole-lot bucket transfer |
| 4 adversarial | randomised mix of dispense/receive/bucket/write-off/reserve/release/replay |
| 5 replay & counts | duplicate dispense, count short, count over, bucket round trip, valuation |
| concurrency | 6–10 sessions racing for the same 1–5 units |
| long horizon | 12 simulated months, checked monthly |

Seeds run: `7`, `11`, `23` (CI); `200–213`, `300–315` (sweeps); `555` (long
horizon); `900`, `901` (concurrency). Every finding below is reproducible from
its seed.

## 4. Defects

### D1 — Receipt accepts a quantity the schema cannot store · **High**

*Found:* phase 3, seed 7, "huge receipt".

**Reproduction**
1. Receive `9999999.999` units of any SKU — accepted.
2. Receive `1` more unit of the same SKU.
3. `asyncpg.NumericValueOutOfRangeError` on `stock_levels.quantity_on_hand`,
   surfaced as a 500 with nothing actionable in it.

**Root cause.** Quantity was bounded nowhere — not in `ReceiveLot` (only
`gt=0`), not in `receipt_units`, not in `plan_receipt`. `NUMERIC(10,3)` caps at
`9999999.999`.

**Impact.** A mistyped receipt (`99999999` for `999`) is accepted and then
**wedges that SKU**: every later receipt fails on the aggregate update until
somebody writes the phantom stock off. The phantom units also poison valuation,
ABC classification and days-of-supply.

**Fix.** `ledger.MAX_QUANTITY`, enforced in `plan_receipt` for the lot and in
`receive_stock` for the aggregate — the per-lot cap does not stop enough
legitimate lots summing past the column. Both now return a 422 naming the
figure. Transaction atomicity was already correct: the failed receipt left no
lot, no movement and an unchanged aggregate.

**Regression:** 4 tests in `test_inventory_ledger.py`.

### D2 — Reserved stock can be taken out from under the promise · **High**

*Found:* phase 4, seeds 200/203/208/209/213 — 15 occurrences across 12 seeds.

**Reproduction**
1. Receive 10 units.
2. Reserve all 10 for a prescription.
3. `record_damage` 10 units — accepted.
4. Lot now reads `on_hand=0, reserved=10, damaged=10`; `available` is **−10**.

**Root cause.** `plan_bucket_transfer`, `plan_bucket_writeoff` and `plan_issue`
all check against `quantity_on_hand`. Only `pick_fefo` respects reservations, so
any path that removed sellable stock could remove units already promised.

**Impact.** The patient's medicine is written off while the reservation still
claims it. FEFO then refuses to fill the very prescription the stock was held
for, and `check_over_reservation` fires on a state no operator can explain.

**Fix — and why not simply to refuse.** A crushed carton is a fact; refusing to
record it would make the books describe a shelf that no longer exists. The
movement stands, and the promises it can no longer back are released:
`reservations.plan_shrink` (pure) plus `reservation_service.shrink_to_capacity`,
called from `record_damage` and from the approved write-off path. Whole
reservations are released, newest first — the earlier promise keeps its place,
and a 30-tablet prescription is not quietly reduced to 11, which would be a
promise nobody can fill discovered with the patient at the counter. The affected
prescription ids are logged and returned in the response.

**Regression:** 7 unit tests + 2 e2e.

### D3 — Stock can enter a holding bucket but only leave by destruction · **Medium**

*Found:* phase 5, while wiring the bucket round trip — `FROM_BUCKET` silently
never executed.

**Reproduction**
1. `record_damage` 40 units into `damaged`.
2. Inspection finds the product sound.
3. There is no endpoint that returns them to sellable stock.

**Root cause.** `plan_bucket_release` existed, and `_apply_movement` handled
`payload["release"]` — but **nothing ever wrote that key**. `grep '"release"'`
returned exactly one hit, a read. The releasing branch was unreachable, and the
only path out of `damaged`/`returned` was an approved write-off. (`in_transit`
had a partial route, but only inside a depot-transfer session.)

**Impact.** A mis-clicked DAMAGE is unrecoverable without destroying stock.
Units sit in the bucket appearing as an asset in valuation indefinitely.

**Fix.** `POST /inventory/admin/release` creates the approval that reaches the
existing branch. Deliberately the opposite way round from `record_damage`:
taking stock **out** of use is a safety action and applies at once; putting
blocked stock **back** on sale is the direction that can hurt a patient, so it
needs a second person. Recalled and expired lots are refused outright.

**Regression:** 4 e2e tests.

### Behaviours confirmed correct (not defects)

- **Concurrency.** 6 sessions racing for 1 unit, and 10 racing for 5: exactly
  one succeeded in full, the rest recorded shortfalls, the ledger totalled
  exactly the stock that existed. `SELECT … FOR UPDATE` in `_lots_for` holds.
- **Separation of duties.** The simulator was refused when the same person
  posted a count and approved its variance — the rule working.
- **Atomicity.** Every refused operation left no partial state.
- **Append-only.** No path produced an unchained or editable movement.

## 5. Stopping

| Measure | Value |
|---|---|
| Events executed | ~15,600 across all runs |
| Check rounds | 512 in the final sweep alone |
| Invariants per round | 11 (5 oracle comparisons, 6 structural) |
| Distinct defect classes | 3 |
| New classes in the last 16 batches | **0** |
| Unresolved Critical/High | **0** |
| Regression tests added | 17 |

Final sweep: 16 seeds, 4,358 events, 512 check rounds, **0 findings**. The
defect-discovery rate has been flat at zero for sixteen consecutive diversified
batches, which is the stated stopping condition.

## 6. Residual risk — what this does *not* establish

This is not a proof of correctness, and the following are untested or only
partly tested:

- **Crash mid-transaction.** Process kill between flush and commit was not
  simulated; only application-level refusals and database constraint failures
  were. Recovery from a torn write is unverified.
- **Multi-tenant interleaving.** Each run uses its own pharmacy. Cross-tenant
  leakage under concurrent load is not exercised, though every query is scoped.
- **Time.** All runs use a fixed `as_of`. Daylight-saving boundaries, timezone
  conversion on `fill_date` vs `created_at`, and month-end effects are untested.
- **Caches and dashboards.** The simulator compares the database and the service
  layer. It does not read the React dashboards, so a stale-cache defect between
  API and UI would not be caught here.
- **The ML engines.** Demand, lead time, cycle counting, valuation and the
  recommendation ledger have unit tests but are not driven by the simulator's
  invariants. A wrong reorder point is not a stock-conservation failure and
  would not appear in these numbers.
- **Scale.** Largest run is ~4,400 events over ~40 SKUs. Query-plan behaviour at
  a 23,859-product catalogue with millions of movements is unmeasured.
- **The oracle could share a blind spot.** It was written independently and uses
  a different algorithm, but by the same author. An invariant neither party
  thought of is invisible to both.

## 7. Confidence

**Stock conservation, bucket arithmetic, reservation lifecycle and the
maker-checker controls: high confidence.** These were attacked directly, with an
independent ground truth, a proven-sensitive harness, and 16 clean diversified
batches after three real defects were found and fixed at the cause.

**Valuation, forecasting inputs and dashboard consistency: moderate.** Covered
by unit tests and one invariant, not by adversarial pressure.

**Crash recovery and scale: low — untested.** These are the next things worth
building, and the honest answer today is that nobody knows.
