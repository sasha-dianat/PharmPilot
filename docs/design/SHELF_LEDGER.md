# The shelf ledger — how the sales floor counts, and the one copy too many

Status: **Arithmetic repaired and verified (migration 0054). One design question
open and referred to the inventory owner — see §5.**
Owner: Claude Code (workstream CL-005) · Branch `fix/shelf-fractional-ledger`
Date: 2026-09-12 · Migration head `0054`

Read this before changing anything that writes `shelf_placements.units`,
`pharmacy_shelves.current_units`, or `shelf_transfer_events.quantity_delta`.
Those three columns are the same quantity recorded three times, and the last
defect in them was invisible for the whole of Phase 1.

---

## 1. What the shelf ledger is for

The shelf layer answers three questions the lot ledger cannot:

1. **What is standing on the sales floor right now**, so the morning
   replenishment round knows what to top up.
2. **What the floor is worth**, at retail.
3. **What a physical count should find** — the *expected* side of
   `services/core/inventory/shelf.py:reconcile`, which is the pharmacy's theft
   detector.

The third is why the arithmetic has to be exact, and it is worth being precise
about the consequence. `reconcile` compares expected against counted and calls
the difference `agrees`, `inconclusive`, `surplus` or `shrinkage`. Only
`shrinkage` reaches a person. So:

- an **under-reported** expected makes an honest count look like SURPLUS, and the
  real loss underneath it is never investigated;
- an **over-reported** expected makes an honest count look like MISSING, and the
  system accuses staff on the strength of its own arithmetic.

One false accusation ends the credibility of every true one. That standard is
already written into `reconcile`'s thresholds; it is worth nothing if the number
fed into it was rounded on the way in.

---

## 2. The provenance distinction, which predates this document

Half the numbers on a shelf are not measurements, and the code is careful about
which half:

| basis | what happened |
|---|---|
| `observed` | somebody scanned the shelf and attested to it — a placement |
| `inferred` | a dispense reduced the lot; *which* shelf the hand reached for was allocated, not seen |

Both move the number. Only one is evidence. `reconcile` refuses to call a
variance theft when it is no larger than the movement that was merely inferred.
Do not collapse these two into a single "quantity changed" event; the detector
stops working the moment it cannot tell them apart.

---

## 3. The defect migration 0054 closed

Proven against the real dispense path by `scripts/verify_shelf_domain.py`, not
inferred from reading code. Dispensing **2.9** units off a placement of **30**
should leave **27.1**. It left:

| column | read | error |
|---|---:|---:|
| `shelf_placements.units` | 25 | −2.1 |
| `pharmacy_shelves.current_units` | 29 | +1.9 |
| `shelf_transfer_events.quantity_delta` | −2 | +0.9 |

Two copies of one number **four apart**, and an audit row that could not
reconstruct either. Three causes, all in `_take_off_shelf`
(`services/core/inventory/dispense.py`), all the same mistake in different
clothes — every one of the three columns was `INTEGER`, and each write converted
the allocator's exact `Decimal` a different way:

```
shelf_placements.units          ← float(take.after)   # rounds a whole unit off per take
pharmacy_shelves.current_units  ← int(take.units)     # 0 for ANY take below one unit
shelf_transfer_events.delta     ← -int(take.units)    # the audit trail, rounded too
```

`shelf.allocate` was never at fault: it computes in `Decimal` and quantises to
three places. The loss was entirely in the write.

**The fix has two halves and neither works alone.** Migration 0054 widens all
three columns to `Numeric(10,3)` — matching `inventory_lots.quantity_on_hand` and
`prescription_fills.quantity_dispensed`, which were always exact — and the write
path passes the `Decimal` through instead of converting it. Widening the column
while still passing a `float` would round in Python instead of in Postgres.

`pharmacy_shelves.capacity_units` deliberately stays `INTEGER`. It describes the
furniture, not a quantity anything divides.

### The clamp

`current_units` was decremented with `GREATEST(0, current_units - :taken)`. When
the cache could not cover the take, that wrote a plausible-looking **0** over a
number nobody knew — the fallback-constant pattern the provenance rule in
`CLAUDE.md` forbids. The shelf then read empty, and no row said why.

A cache that cannot cover its own take has *already* drifted from the rows it
caches. The repaired path performs the real arithmetic, stores the result even
when it is negative, and flags it in two places:

- on the returned take — `cached_total_after`, `cached_total_inconsistent`;
- on the durable `shelf_transfer_events` row, inside
  `barcode_verification_result`, because a reconciliation weeks later reads rows
  and not a response body.

A negative `current_units` is **intentional and diagnostic**. It means the cache
disagreed with its placements before the dispense touched it. Do not add a
constraint or a clamp to "fix" it; that reinstates the defect. Fix the drift.

### Other write paths

`services/platform/routers/depot_transfer.py` increments the same cache on
placement. It did `int(shelf.current_units) + int(body.quantity)`, which after
0054 would round a fractional cached total away on the very next placement and
re-open the drift. It now does `Decimal` arithmetic. `body.quantity` stays an
integer: a placement is a scanned, whole-unit act. It is the running total that
is not whole.

**If you add a write path to any of these three columns, it must use `Decimal`.**

---

## 4. What guards this now

| guard | what it would catch |
|---|---|
| `scripts/verify_shelf_domain.py` | the whole domain against an independent oracle, through the real `apply_dispense`. Prints `check` for properties that must hold and `note` for known referred gaps |
| `tests/unit/test_inventory_shelf_ledger_e2e.py` | exactness of both copies and the movement row; the clamp; that the inconsistency flag does not fire on a healthy shelf |
| `tests/unit/test_model_column_parity.py::test_every_dispensable_quantity_is_numeric_not_integer` | any quantity column reverting to `INTEGER` |
| `tests/unit/test_inventory_shelf.py` | `allocate` / `position` / `reconcile` as pure functions |
| `tests/simulation/domains/shelf.py` | the oracle: an independent re-derivation of allocation and the verdicts. `policy_drift()` reports threshold divergence separately, so "the policy moved" stays a different sentence from "the detector is wrong" |

The parity test compares column **names**, which is the failure it was written
for. The type check is a separate, explicitly-listed pin because the guard cannot
infer which columns are quantities and which are counts of things that do not
divide.

---

## 5. Open design question — two writable copies of one number

**Referred to the inventory owner. Not decided here.**

`pharmacy_shelves.current_units` is a denormalised cache of
`SUM(shelf_placements.units)`. The module docstring in `shared/models/depot.py`
says depot quantity is computed and "never double-stored" — but this total *is*
double-stored, and both copies are writable.

Exact arithmetic stops the two drifting **by accident**. It does not stop a new
write path updating one and forgetting the other, and that is the failure mode
that produced this document. Every future change to shelf quantities has to
remember both copies, forever, correctly.

Three options, in rough order of durability:

1. **Delete the column** and compute `SUM(shelf_placements.units)` at read time.
   One writable copy, drift impossible by construction. Costs a join on the read
   paths (`depot_transfer` listing, the pick list, `inventory_integrity`).
2. **Make it a generated/materialised column** maintained by the database rather
   than by application code. Keeps the read cheap, removes the second writer.
3. **Keep it and rely on reconciliation** to report divergence. This is the
   status quo plus the new inconsistency flag — cheapest, and the only one of the
   three that leaves the drift generator in place.

This is a schema and performance trade-off on a table the inventory owner owns,
so it is raised rather than taken. The arithmetic fix above is correct and
complete under any of the three.

---

## 6. If you are changing this code

- Quantities are `Decimal` end to end. Never `float`, never `int`, at any layer.
- Never clamp a quantity to make it look plausible. Record what happened and
  flag it.
- Changing a threshold in `services/core/inventory/shelf.py` means changing it in
  `tests/simulation/domains/shelf.py` too, or `policy_drift()` will report the
  oracle as stale — which is the intended signal, not a bug.
- Run `python scripts/verify_shelf_domain.py` against a **disposable** database.
  It writes.
- A migration touching these columns must be verified up→down→up on a disposable
  clone, never on the shared `pharmpilot_test` — see the shared-database hazard
  note in `docs/ai-context/AI_COLLABORATION.md`.
