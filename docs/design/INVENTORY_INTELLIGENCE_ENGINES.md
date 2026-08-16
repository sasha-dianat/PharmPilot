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
| **E6 Intermittent demand forecast** | How much will actually sell | dispense history. **Croston/TSB, not ARIMA** — pharmacy line-item demand is mostly zeros, and a mean fitted to zeros is a lie |
| **E7 Seasonality decomposition** *(new)* | Is this a real annual pattern or last month's noise | ≥2 seasonal cycles; before that it reports "insufficient cycles" |
| **E8 Reorder point / safety stock** (built, dormant) | When to order and how much cover | E6 + measured lead time |
| **E9 Expiry-before-sale probability** | Upgrade of E1 from deterministic to probabilistic | E6's variance |
| **E10 Morning pick list** (supervision §6) | What to pull from depot to shelf before opening | E6 at q85–q90 |

### Tier C — needs supplier event history

| Engine | Question | Blocked on |
|---|---|---|
| **E11 Lead-time distribution** (partially built) | How long each supplier really takes | ~~PO `received_at` is never written~~ — **unblocked 2026-08-16**; now stamped on completion by `receiving.py`. Waiting only on real orders |
| **E12 Supplier reliability** | Who fills short, who fills late, who substitutes | ~~`quantity_received` is never written~~ — **unblocked 2026-08-16**; per-line receipts and short/exact/over shape now recorded |
| **E13 Shortage early warning** (⑰, rebuild) | Which NDCs are about to become unobtainable | E11 + E12 + fill-rate CUSUM; cloud adds national feeds |
| **E14 Negotiation mentor** *(new)* | What to ask this distributor for, and what to concede | E11 + E12 + price history + volume commitments |

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
5. **E13 shortage warning, ⑳ negotiation mentor** — both sit on E11/E12.
6. **E6 demand, E7 seasonality, E8 reorder point, E10 pick list** — as history
   accumulates, in that order.

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
