# Inventory intelligence — the engine roster

**Date:** 2026-08-15 · **Branch:** `feat/inventory-integrity`
**Sources:** `docs/MASTER_PROMPT_intelligent_services.md` (the founding specification,
14 services), `docs/design/PHARMACY_SUPERVISION_PLATFORM.md` §6 Domain 3, and the
owner's extension of 2026-08-15 adding two engines that were never specified.

---

## 0. The rule every engine here obeys

Two rules, and they are not negotiable because both have already been broken once
in this codebase.

**The offline doctrine (from the master prompt).** Every service returns a useful
answer from the LOCAL brain with the cable unplugged. The cloud only enriches.
Losing connectivity reduces options and confidence; it never produces an error or
a blank screen.

**The provenance rule (learned the hard way, 2026-08-09).** Every number an engine
emits declares where it came from: `observed`, `sparse`, `no_history`, or
`declared_default`. Where there is nothing to derive a value from, the engine
returns **NULL and says so** — it does not substitute a constant. This exists
because `stock_levels.avg_daily_demand` held seeded values claiming 14 units/day
for an item with zero dispensing, and every engine downstream consumed them as
measurements.

The combination gives each engine a third state beyond online/offline:
**"not measurable yet."** That state is a first-class answer, not a failure.

---

## 1. What was specified, and what it is actually doing

| # | Engine | Specified | Code | Routed | Running on real data? |
|---|---|---|---|---|---|
| ⑫ | Inventory Expiry Waste Prevention | ✅ | `expiry_prevention.py` | `/expiry-risk` | ❌ demand is `no_history` |
| ⑰ | Supply-Chain Disruption Early Warning | ✅ | `supply_warning.py` | `/supply-risk` | ❌ **zero purchase orders exist** |
| ⑲ | Financial Intelligence & Margin Optimization | ✅ | `margin_optimizer.py` | `/margin-insights` | partial |
| ② | Predictive Queue Prioritization (arrival forecast) | ✅ | `intel_workflow.py` | `/queue/forecast` | partial |
| — | Demand forecaster (Prophet/XGBoost) + `SmartReorderEngine` | reuse, don't rebuild | `forecaster.py` | `/forecast` | ❌ needs 30 days history; has 0 |
| — | Anomaly / diversion screener | reuse, don't rebuild | `anomaly_detector.py` | via recommendations | ⚠️ wired 2026-08-09 |
| — | Morning pick list (LightGBM q85–q90) | ✅ supervision §6 | — | — | **not built** |

**The finding that matters:** every specified inventory engine exists as code and
**none has yet run on real data.** `supply_warning` derives fill-rate from
`purchase_orders`, a table with zero rows; `expiry_prevention` divides on-hand by a
daily rate that is currently `no_history` for all 16 stocked items. They were not
wrong to build — they are waiting on a pilot, and until this month they were
disguising that wait by consuming fabricated inputs.

---

## 2. The full roster, tiered by what each needs

Tiering is by **data prerequisite**, not by difficulty. An engine in Tier A can be
honest today; one in Tier C cannot, and shipping it early only produces confident
nonsense.

### Tier A — honest today, no dispensing history required

| Engine | Question it answers | Inputs that exist now |
|---|---|---|
| **E1 Expiry risk** (⑫, rebuild) | Which lots expire before they sell, and what can still be done | lot expiry, quantity, unit cost, FEFO order |
| **E2 Receipt anomaly** | Is this delivery's price or quantity unlike every previous one | 23,859-row formulary, lot cost history |
| **E3 Adjustment/diversion screener** (existing) | Which write-offs cluster suspiciously by actor, round number, or threshold | `inventory_movements` |
| **E4 ABC/XYZ + cycle-count planner** (built 2026-08-09) | Where the counting hours go | value on hand, criticality, variance history |
| **E5 Formulary binding confidence** (built) | Which stock rows can be priced at all | GTIN → generic+strength+form ladder |

E1 is the only Tier A engine with genuine money attached, and it degrades cleanly:
with no demand rate it reports *"N units exposed, projection unavailable"* rather
than zero risk (which reads as safe) or total risk (which floods the list).

### Tier B — needs ~8–12 weeks of real dispensing

| Engine | Question | Blocked on |
|---|---|---|
| **E6 Intermittent demand** — **built 2026-08-18** | What *shape* the demand has, which a rate cannot express | Nothing to build. `intermittent.py` classifies (Syntetos–Boylan) and sets an event-cover floor on the reorder point; it degrades to `unknown` until an item has three demand events. Real *validation* still needs months of dispensing |
| **E7 / ㉑ Seasonality** *(new)* — **built 2026-08-18** | Is this a real annual pattern or last month's noise | Nothing to build. `seasonality.py`, Jalali-month buckets, at `GET /inventory/seasonality`. It will answer `insufficient_cycles` until the pilot has two full years, and that is the engine working |
| **E8 Reorder point / safety stock** — **live 2026-08-18** | When to order and how much cover | Nothing. Fed by E6's shape and E11's per-supplier lead time; returns None where either input is unmeasured rather than assuming one |
| **E9 Expiry-before-sale probability** — **built 2026-08-18** | Upgrade of E1 from deterministic to probabilistic | Nothing. `expiry_probability.py` at `GET /inventory/expiry-odds`; refuses a probability where the normal approximation has too few demand events behind it |
| **E10 Morning pick list** (supervision §6) — **built 2026-08-18** | What to pull from depot to shelf before opening | Nothing. `pick_list.py` at `GET /inventory/pick-list`, stocked to the 90th percentile — or to one whole demand event where demand arrives whole |

### Tier C — needs supplier event history

| Engine | Question | Blocked on |
|---|---|---|
| **E11 Lead-time distribution** — **built 2026-08-17** | How long each supplier really takes, and how much that varies | Nothing. `lead_time.by_supplier` on delivered orders; surfaced at `GET /inventory/suppliers` |
| **E12 Supplier reliability** — **built 2026-08-17** | Who fills short, who is unpredictable, who substitutes | Nothing for fill/consistency. Substitution stays `not_captured` — no receiving path records one |
| **E13 Shortage early warning** (⑰, rebuild) — **built 2026-08-17** | Which NDCs are about to become unobtainable, **and whether the market or one supplier is the cause** | Nothing. `shortage.py`, at `GET /inventory/shortage-warning`; ⑰ now delegates to it. Cloud (national feeds) still inert |
| **E14 / ⑳ Negotiation mentor** *(new)* — **built 2026-08-17** | What to ask this distributor for, and what to concede | Nothing for the local tier. `negotiation.py`, at `GET /inventory/negotiation-brief`. Cross-pharmacy benchmark needs a second pharmacy and declares itself inert |

---

## 3. The two new engines, specified

### ⑳ Distributor Negotiation Mentor *(new, 2026-08-15)*

**Purpose.** Prepare the owner for a conversation with a distributor: what this
pharmacy is actually worth to them, where the current terms are worse than the
alternatives, and which concessions are cheap to give.

**LOCAL brain.** Everything from the pharmacy's own records:
- **Volume leverage** — annual spend per supplier, share of their categories, and
  how concentrated the pharmacy's purchasing is. A supplier taking 40% of spend
  hears a different argument than one taking 4%.
- **Term comparison** — for every molecule bought from more than one supplier, the
  realised unit cost after discounts, freight and credit terms. The negotiable gap
  is the difference, not the list price.
- **Reliability cost** — E12's fill-rate translated into money: what this
  supplier's short-fills cost in lost sales and emergency substitutions. This is
  the strongest card and nobody currently computes it.
- **Concession ledger** — which asks (larger order, longer payment window, fewer
  delivery days) cost this pharmacy least, computed from E8's safety stock and the
  cash-conversion cycle.
- **Preparation brief, generated by the local LLM (Ollama)** from those numbers.

**CLOUD brain.** Anonymised cross-pharmacy benchmark terms for the same molecule
and supplier; national price indices. Offline → local-only with `degraded: true`.

**Explicit non-goals, and why.**
- It **does not negotiate**, send, or commit to anything. It prepares a person.
- It **never invents a benchmark**. With one supplier and no history it says so —
  a fabricated "market rate" in a negotiation is worse than silence, because the
  owner would repeat it aloud to a distributor who knows the real number.
- No patient data enters it. Volumes are aggregate.

**Endpoint.** `GET /api/v1/intelligence/procurement/negotiation-brief?supplier=…`

**Data prerequisite.** Tier C. Meaningful at ≥2 suppliers and ≥6 months of POs.
Before that it returns the volume picture and states that terms cannot be compared.

### ㉑ Seasonal Demand Decomposition *(new, 2026-08-15)*

**Purpose.** Separate a real annual pattern from noise, so ordering anticipates
the season instead of reacting to it a month late.

**LOCAL brain.** STL decomposition (trend / seasonal / residual) per molecule, with
an explicit **seasonality strength** statistic. Two guards that decide whether it
is allowed to speak:
- **Cycle count.** Fewer than two complete cycles → no seasonal claim, ever. One
  cold season is an anecdote.
- **Strength threshold.** Where the seasonal component does not exceed the
  residual, it reports "no detectable seasonality" rather than a flat line
  presented as a finding.

Persian-calendar aware: Nowruz, Ramadan and the school year move against the
Gregorian calendar, and a model keyed to Gregorian months will smear them.

**CLOUD brain.** Regional epidemiological signals; cross-pharmacy seasonal priors
for molecules this pharmacy has too little history for.

**Endpoint.** `GET /api/v1/intelligence/inventory/seasonality`

**Data prerequisite.** Tier B, and the strictest here: **two full years** before a
confident annual claim.

---

## 4. Build order

Ordered by *value per unit of available data*, not by ambition.

1. **E1 expiry risk, rebuilt** — Tier A, real money, degrades honestly. The only
   large win available before the pilot generates anything.
2. **E2 receipt anomaly** — Tier A, uses the formulary that already exists.
3. **Capture the substrate for Tiers B and C.** Nothing here is a model: it is
   making sure that when purchase orders and dispenses start flowing, the columns
   that E6/E11/E12 need are populated and provenance-stamped. Skipping this is how
   a team arrives in three months with a working detector and no labels.

   *Done for the supplier half, 2026-08-16.* `ordered_at` was already stamped on
   submit; `received_at` and `quantity_received` were read in five places and
   written in none, which is why service ⑰ has never produced a real fill rate.
   `services/core/inventory/receiving.py` now closes the loop: a receipt against
   a purchase order matches the oldest open line, records the delivery as
   **short / exact / over**, re-derives the order's status from its lines, and
   stamps `received_at` only when nothing is outstanding — a part-filled order
   must not look faster than it was. `GET /inventory/admin/open-orders` and the
   order picker on the receiving form make the attribution a click, because a
   column nobody can fill from the bench stays empty.

   Still uncaptured: the *dispensing* half (E6/E7/E10), which needs prescriptions
   flowing through the platform rather than any further code here.
4. **E11 lead time, E12 supplier reliability** — the moment POs exist.

   *Done, 2026-08-17.* `supplier_reliability.py` scores each supplier on
   **fill rate × consistency**, and deliberately **not** on speed: a supplier
   that takes eleven days every time is already handled, because those eleven
   days are in the reorder point, and scoring duration here would punish it
   twice for something the planner has absorbed. What cannot be planned around
   is variability, so that is what is scored.

   Three refusals matter more than the score: an unmeasured supplier is not
   ranked (a withheld score sorts last, never first); consistency requires an
   *observed* lead time, because one delivery has a standard deviation of zero
   and would hand a newcomer a perfect record; and two suppliers are not
   compared unless their baskets overlap, or the comparison measures catalogues
   rather than suppliers.

   Running it against real rows found a blind spot the unit tests could not:
   a supplier that *always* short-ships never completes an order, so
   `received_at` is never stamped, so it acquires no lead time — and the worst
   supplier on the roster became indistinguishable from one that had never
   delivered. Fixed by `close_short`: someone can now record that the rest is
   not coming, the outstanding units settle as `backordered` (the supplier's
   failure) rather than `cancelled` (the pharmacy's withdrawal), and the
   shortfall stays in the fill rate. Closing an order must not launder the
   failure that made closing it necessary.
5. **E13 shortage warning, ⑳ negotiation mentor** — both sit on E11/E12.

   *Done, 2026-08-17.*

   **E13** (`shortage.py`) exists for one distinction the version it replaces
   could not make: a short fill is not a shortage. Short from *every* supplier
   that carries it means the market is out, and the remedies are cover,
   substitution and warning prescribers. Short from one while another delivers
   in full means the molecule is available and the remedy is a phone call.
   Service ⑰ scored both identically and recommended buffer stock for both —
   which is paying to hold inventory that expires on the shelf against a problem
   that costs nothing to fix. The verdicts are therefore about *cause*, and the
   actions differ.

   Rebuilding it also removed four defects that were harmless only while
   `quantity_received` was never written and the output was visibly garbage.
   Once the column carries real numbers the same code produces *plausible* wrong
   answers, which is worse: a fill rate of 1.0 for anything never ordered;
   `COALESCE(avg_daily_demand, 0)` turning unmeasured demand into infinite cover;
   a third unlabelled lead-time constant (5 days, against `lead_time`'s 7); and
   in-flight lines counted as total short-fills. ⑰ is now the envelope and cloud
   tier around E13 and computes nothing itself; `disruption_risk` survives for
   the payload contract but is documented as a severity band, not the
   three-decimal pseudo-probability it used to present.

   The CUSUM was recalibrated from 0.5 to 0.2 during the build. At 0.5 the creep
   detector needed fifteen consecutive short deliveries, by which time the
   aggregate rate had long since fallen through the short-fill threshold and the
   item was flagged on that instead — so the verdict it feeds was unreachable.
   Where it earns its place is a molecule with a long clean history and a recent
   decline: twenty good deliveries then four at 85% leaves the average at 97.5%
   precisely *because* the past was good.

   **⑳** (`negotiation.py`) computes leverage, the negotiable gap, and what
   unreliability costs — the last of which is E12's fill rate turned into money
   and is the strongest card in the conversation. Its governing rule is a refusal:
   **it never invents a benchmark.** Every comparison is between two prices this
   pharmacy has actually paid, because the failure mode is not a wrong figure on
   a screen but the owner repeating a fabricated market rate to a distributor who
   knows the real one. Where a molecule has one supplier there is no gap and the
   brief says so. Where no sale price is recorded, the undelivered *units* are
   reported and the money is **not** — a guessed margin quoted to someone who
   sells these for a living is the same mistake as a guessed benchmark. Payment
   terms and cost of capital are recorded nowhere, so a longer payment window
   stays explicitly unpriced. The `cannot_say` list is rendered as prominently as
   the asks.

   ⑳ deliberately does **not** file into the recommendation ledger. That
   machinery measures unsolicited advice; a brief the owner asked for has already
   been accepted by being requested, and filing it would inflate the denominator
   the fingerprint exists to protect.
6. **E6 demand, E7 seasonality, E8 reorder point, E10 pick list** — as history
   accumulates, in that order.

   *E6 and E8 done, 2026-08-18.* The framing in this document was
   "Croston/TSB, not ARIMA", and building it corrected that. Croston is in
   `intermittent.py` and reported, but it is **not** what makes E6 worth having.
   A mean over the window is already an unbiased estimate of the long-run rate;
   beating it is not the problem. The problem is that a rate cannot express
   *shape*:

   | | rate | reorder point |
   |---|---|---|
   | 2 units a day, most days | 2.00/day | **8.6** |
   | 14 units every week | 2.00/day | **22.9** |
   | 5, 60, 12, 80, 8 at random | 1.96/day | **87.6** |

   Those three reorder points are measured, from the refresh endpoint, on items
   whose rates differ by less than 0.2 units a day. Classification is
   Syntetos–Boylan on ADI and CV² of event sizes, at the published cut-offs
   rather than house values — inventing our own would make the scheme
   unfalsifiable against the literature it comes from. **Lumpy is the honest
   quadrant**: rare events of wildly varying size cannot be forecast well by any
   method, so the engine says so and reports the size of one event instead of a
   daily figure.

   The reorder point gained an **event-cover floor** for intermittent and lumpy
   items. It is a floor and not an override: for a *regular* burst pattern the
   daily-bucket σ — with the zeros in it — already covers an event, and raising
   nothing there is correct. It bit on the lumpy item and on nothing else.

   Two things found by building it, both corrections to my own first design:
   - **Croston cannot detect a moving rate at α = 0.1.** When demand rises the
     smoothed size climbs while the smoothed interval shortens, and the two
     effects nearly cancel in the ratio. On a series that went from 2 units a
     week to 60 every three days, SBA and the mean differed by 1%. Drift is
     measured by halving the window instead — crude, but it needs no smoothing
     constant to argue about and both numbers can be shown.
   - **`reorder_signals` was inventing a variance.** An unmeasured spread became
     `demand × 0.5` — a coefficient of variation presented as a safety stock,
     several-fold too small for exactly the lumpy items that most need cover. It
     now returns no cover rather than assumed cover, which is the same rule the
     demand signal itself follows.

   *E7, E9 and E10 done, 2026-08-18 — the roster is complete.*

   **E7/㉑ seasonality** buckets by **Jalali month**, and that is arithmetic
   rather than decoration: Nowruz is 1 Farvardin every year and drifts across
   20–21 March, so a Gregorian March bucket splits the new-year peak across two
   months and halves it; the school year turns on 1 Mehr, which lands in
   September or October. Two refusals carry it — nothing is claimed below two
   complete cycles (one cold season is an anecdote, and a system that buys for a
   season that never comes is worse than one that does not try), and where the
   month explains less than 30% of the variance the answer is "no detectable
   seasonality" rather than a flat line with a shape drawn on it. **Ramadan is
   lunar and moves against the Jalali calendar too, so it is not captured** — it
   lands in the residual, which depresses the strength statistic and therefore
   makes a claim harder to make rather than easier. That is stated in every
   response rather than left as a silent gap.

   **E9 expiry probability** gives E1 the middle it has no vocabulary for. A lot
   with a 5% chance of expiring is not worth discounting; the same lot at 60% is
   worth discounting today, while a customer still exists, and E1 puts both in
   the same bucket. The guard is that a normal approximation is the central limit
   theorem doing the work, and the theorem needs a sum of many things — so a
   probability is quoted only where the horizon is expected to contain at least
   ten demand events, computed from E6's measured interval. Lumpy items are
   refused outright: E6 declines to forecast them and this declines to put a
   number on them, which is the same judgement twice.

   **E10 morning pick list** answers the question the replenishment machinery
   never had: `create_session` takes a list of NDCs somebody typed in, so the
   intelligence in the morning round has been a person remembering what ran out
   yesterday. The target is the 90th percentile of a day's demand, because **a
   shelf stocked to the mean runs out half the time** — except for items whose
   demand arrives whole, where a percentile of a daily total is meaningless and
   the shelf needs one event. Two refusals are physical rather than statistical:
   a refrigerated drug is never proposed for a room-temperature shelf, and
   nothing quarantined, recalled or expired is ever picked. Both are drops rather
   than warnings, because a warning on a picking list is read at speed by
   somebody holding a crate. Items with no measured demand are left off and the
   omission is explained — shelf space is the one thing in a pharmacy that cannot
   be ordered more of.

Every engine reports into the **recommendation ledger** built on 2026-08-09, so
each one's acceptance rate is visible from its first week and a detector that is
being ignored is named as such rather than counted as vigilance.

---

## 5. What this document does not claim

- No engine here is validated against real pharmacy behaviour. The simulator
  validates *stock arithmetic*, not forecast quality.
- Tier B and C timings assume the pilot dispenses daily through the system. If
  dispensing does not flow through it, none of those engines ever become
  measurable, and the honest degradation will simply keep saying so.
- The negotiation mentor's cross-pharmacy benchmark needs more than one pharmacy
  on the platform. Until then its cloud tier is inert and it says so.
