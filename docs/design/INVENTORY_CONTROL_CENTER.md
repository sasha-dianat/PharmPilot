# Inventory Integrity & Control Center — gap analysis and roadmap

Date: 2026-08-03 · Assessed against `master` @ `8096127` + `feat/inventory-integrity`
Migration head `0033` · All figures measured against the live database, not inferred.

---

## 1. Assessment of the current panel

### Strengths (keep, do not rebuild)

| | Evidence |
|---|---|
| **The ledger conserves and cannot be edited.** Every quantity moves through `ledger.plan_*`; a DB trigger blocks UPDATE/DELETE on `inventory_movements`; each row is SHA-256 chained to its predecessor. | Verified live: UPDATE and DELETE both rejected. `verify_chain` intact. |
| **The admin panel cannot change a quantity.** Three field classes; all quantity columns unreachable from the UI. | 4 parametrised tests |
| **Maker–checker exists and is enforced in the database**, not only the service layer. | `ck_approval_not_self`, `ck_witness_not_requester` |
| **Corrections that could hide something are routed to approval**, with a deliberate asymmetry: shortening an expiry is free, extending it needs two signatures. | 31 policy tests |
| **Reconciliation is 12 real checks over real rows**, with a three-state verdict that refuses to call a broken ledger healthy. | Currently `blocking=true` |
| **Dispensing now decrements**, FEFO, idempotent, lot stamped on the fill. | 7 end-to-end tests |

This is a stronger foundation than most pharmacy systems have. The gaps below are
about **workflow and persistence**, not about correctness of the core.

### Weaknesses

**W1 — Two panels, one workflow, no bridge.** `InventoryAdmin` (Alt+V) and
`InventoryIntegrity` (Alt+I) serve one loop — *notice an exception → reach the
records → correct under control → confirm it cleared* — but there is no
navigation between them. Today an operator reads "46 fills without movement",
memorises an NDC, switches panel, and re-searches by hand. **No KPI or finding
is clickable.** This is the defect that makes the current UI a dashboard rather
than a control centre.

**W2 — Findings are ephemeral.** `/reconciliation` recomputes all 12 checks per
request and returns `samples[:10]`. A finding has no identity that survives the
next run. Therefore it cannot be assigned, dispositioned, deduplicated,
suppressed, or measured. "Is this new or the same one as yesterday?" is
unanswerable, and 46 orphan fills display as 10.

**W3 — No ranking.** Findings sort by severity then count. Nothing weighs
financial value, patient-safety exposure, or confidence, so a 2-unit
controlled-substance loss ranks below 46 legacy demo rows.

**W4 — Reporting-only surface.** No saved views, no column customisation, no
export, no record comparison, no bulk operation from a finding.

**W5 — Three of eleven stock states are not representable.** `in_transit`,
`damaged`, `returned` have no bucket. `DAMAGE` exists as a movement type but
merely decrements — damaged stock disappears rather than moving to a damaged
state, so it cannot be reported, valued, or returned to a supplier.

**W6 — Audit trail is missing forensic context.** `created_by`, `reason`,
`approval_id` and the hash chain are present; `session_id`, `device_id`,
`actor_role` and `source_system` are not. "After-hours access from an unusual
device" is therefore not a query that can be written.

**W7 — The new write paths have never run in production.** All 8 movements in
the live database are legacy (`DAMAGE` 3, `EXPIRY_REMOVAL` 2, `ADJUSTMENT` 2,
`RECALL_REMOVAL` 1). Zero `RECEIPT`, zero `DISPENSE`. The receive endpoint and
dispense hook are proven only against the test database.

---

## 2. Capability-gap matrix

Priority = (blocks other capabilities) × (patient-safety or financial exposure).

| # | Target capability | Now | Gap | Blocks | Pri |
|---|---|---|---|---|---|
| G1 | Finding has a durable identity, lifecycle, owner | none | **Exception Register** | 1,2,5,7 | **P0** |
| G2 | Drill-down from KPI/alert → affected rows → action | none | deep-link contract | 1,2,3 | **P0** |
| G3 | Alerts ranked by urgency × value × safety × confidence | severity only | scoring function | 1,7 | **P0** |
| G4 | in-transit / damaged / returned states | absent | 3 buckets + transitions | 1,4,6 | P1 |
| G5 | Forensic audit fields (session, device, role, source) | 4 of 8 | additive migration | 5 | P1 |
| G6 | Cycle-count planning by ABC/risk/discrepancy history | manual scope only | ABC + planner | 4 | P1 |
| G7 | Saved views, column choice, export, comparison | none | view persistence | 2 | P1 |
| G8 | Recall workflow: batch → locations → patients | data exists, no workflow | recall entity | 6 | P1 |
| G9 | Valuation + valuation anomalies | cost present (17/17 lots) | valuation service | 1 | P2 |
| G10 | Financial/purchasing/returns reconciliation | stock-only | source adapters | 4 | P2 |
| G11 | Forecasting, shortage prediction, expiry forecast | fallback only | demand model | 7 | P2 |
| G12 | Recommendation acceptance tracking | none | decision ledger | 7 | P2 |
| G13 | Surveillance-derived event correlation | separate plane | event bridge | 4,7 | P3 |

**Feasibility notes from the data:** valuation is computable today (17/17 lots
carry `unit_cost`); ABC is computable (15 rows carry a demand signal); par levels
exist on 16 rows. G9 and G6 are not blocked by missing data — only by missing
code.

---

## 3. Information architecture

One section, four tabs, replacing two disconnected panels. The tab is the *stage
of the loop*, not the data type.

```
مرکز کنترل موجودی  (Inventory Control Center)   Alt+V
├── ۱ برج مراقبت      Control Tower   exception-first; every tile is a link
├── ۲ میز کار         Workbench       search, filter, saved views, bulk, export
├── ۳ شمارش و تطبیق   Count & Reconcile  cycle plans, blind counts, variances
└── ۴ حسابرسی         Audit & Forensics  timelines, chain verification, cases
```

Cross-cutting, reachable from anywhere:
- **Exception drawer** — one finding: evidence, history, owner, actions.
- **Record drawer** — one item/lot/batch: state, lots, movements, timeline.
- **Approval queue** — badge in the header; the same queue for every sensitive act.

Navigation rule: **every number is a link.** A tile is a saved query over the
Exception Register; clicking it lands in the Workbench with that filter applied
and the corrective actions in reach.

---

## 4. Roles and permissions

| Role | read | write | approve | count | admin |
|---|---|---|---|---|---|
| Owner / Pharmacy Manager | ✓ | ✓ | ✓ | ✓ | ✓ |
| Pharmacist | ✓ | — | ✓ | ✓ | — |
| Inventory staff | ✓ | ✓ | — | ✓ | — |
| Technician | ✓ | — | — | ✓ | — |
| Auditor (new) | ✓ | — | — | — | — |

Existing: `inventory:read`, `inventory:write`, `inventory:order`,
`inventory:approve`. **Add:** `inventory:count` (perform a count without holding
general write), `inventory:audit` (read-only forensic access, including other
users' actions), `inventory:admin` (thresholds, ABC policy, saved-view
publishing).

The separation that must not erode: **`write` and `approve` are disjoint for
inventory staff.** A requester who can approve their own write-off is the
control failing silently.

---

## 5. Workflows

**Search** — one box classifies the token (name/IRC/NDC/GTIN/lot/invoice) →
results with state badges → row opens the Record drawer → actions filtered by
role and item class.

**Modification** — select field → policy resolves class (direct / sensitive /
ledger-only) → **preview shows before → after and the blast radius** → reason
required → direct applies immediately; sensitive creates an approval carrying
the payload → optimistic-concurrency token prevents silent overwrite → applied
change re-validates policy at approval time and 409s if the row moved.

**Approval** — queue grouped by risk → evidence attached → requester ≠ approver
→ controlled items require a witness ≠ requester ≠ approver → approve applies
through the ledger, reject changes nothing, both are recorded.

**Counting** — planner proposes scope (ABC class, risk, discrepancy history,
expiry sensitivity, controlled status) → session opens and **snapshots expected
quantity per lot** → blind entry, barcode-assisted → variance beyond tolerance
routes to recount → supervisor posts → each non-zero variance becomes a
COUNT_GAIN/COUNT_LOSS approval. Stock never moves until approved.

**Reconciliation** — scheduled run writes findings into the Exception Register
with a stable fingerprint → new findings alert, recurring ones increment,
resolved ones close automatically → each carries evidence and a suggested
corrective action.

**Recall** — enter batch/lot → system resolves every location, movement, fill
and patient → quarantine in bulk (one action, one reason) → generate the
affected-patient list → write-offs flow through approval → the recall case
closes only when no affected lot holds sellable quantity.

**Investigation** — start from a finding, a person, an item, or a time window →
timeline merges movements, approvals, counts, access events → chain verification
on demand → case notes and disposition recorded → export.

---

## 6. Textual wireframes

### Control Tower

```
┌ مرکز کنترل موجودی ─────────────────── [برج مراقبت] میز کار  شمارش  حسابرسی ┐
│ وضعیت: ⛔ غیرقابل اتکا     ۴ از ۱۲ بررسی هشدار    ۱۱۱ ردیف   ۳ در انتظار تأیید│
├────────────────────────────────────────────────────────────────────────────┤
│ STATE OF STOCK        on-hand 3,335  available 3,290  reserved 45          │
│                       ordered 0  in-transit —  quarantined 0  expired 240  │
│                       [each figure is a link into the Workbench]           │
├────────────────────────────────────────────────────────────────────────────┤
│ EXCEPTIONS — ranked by impact, not severity alone                          │
│ ┌──────────────────────────────────────────────────────────────────────┐  │
│ │ ⛔ 98  داروی منقضی در دسترس فروش        2 lots · 240 u · ﷼129k       │  │
│ │      CEF250-OLD −31d · MTP50-OLD −27d                                │  │
│ │      اولین بار ۵ روز پیش · بدون مسئول                                │  │
│ │      [قرنطینه هر دو] [مشاهدهٔ ردیف‌ها] [واگذاری] [رد با دلیل]        │  │
│ ├──────────────────────────────────────────────────────────────────────┤  │
│ │ ⛔ 71  تحویل بدون کسر از موجودی          46 fills · legacy           │  │
│ │      آخرین رخداد ۴۶ روز پیش · ثابت، رو به افزایش نیست               │  │
│ │      [بازبینی پیشنهاد جبران] [مشاهدهٔ ۴۶ ردیف]                       │  │
│ └──────────────────────────────────────────────────────────────────────┘  │
│ score = urgency × financial × safety × confidence   [چرا این رتبه؟]       │
└────────────────────────────────────────────────────────────────────────────┘
```

### Exception drawer

```
┌ تحویل بدون کسر — EXC-2026-0731-014 ───────────────────────────── [بستن] ┐
│ وضعیت باز · شدت بحرانی · امتیاز ۷۱ · نخستین‌بار ۱۴۰۵/۰۵/۰۹ · تکرار ۳    │
│ مسئول: — [واگذاری به…]                                                  │
├─────────────────────────────────────────────────────────────────────────┤
│ WHY  46 prescription fills have no DISPENSE movement. Units left the    │
│      building without leaving the ledger.  rule: fill_without_movement  │
│      قطعیت: ۱٫۰ (قاعدهٔ قطعی، نه مدل)                                   │
│ IMPACT  financial ﷼—  ·  safety: recall untraceable for these fills     │
├─────────────────────────────────────────────────────────────────────────┤
│ AFFECTED (46)          [همه] [صادرات CSV] [مقایسه]                      │
│  fill_id    ndc         qty   filled       lot                          │
│  ea619725   00009001903  60   1405/03/28   —                            │
│  …                                          [۴۶ ردیف — نه ۱۰]           │
├─────────────────────────────────────────────────────────────────────────┤
│ ACTIONS  [شبیه‌سازی جبران (dry-run)]  [ارسال برای تأیید]  [پذیرش با دلیل]│
│ HISTORY  1405/05/09 detected · 1405/05/11 recurred · no disposition     │
└─────────────────────────────────────────────────────────────────────────┘
```

### Workbench

```
[🔍 نام / IRC / NDC / بارکد / بچ / فاکتور]        نماها: [منقضی‌شونده ▾] [+ذخیره]
chips: همه ۱۶ · زیر حد ۰ · منقضی ۲ · بدون IRC ۱۶ · راکد ۲ · قرنطینه ۰ · مازاد ۲
┌───────────────────────────────────────────────────────────────────────────┐
│ ☐ دارو            موجودی  رزرو  پوشش  انقضا      بچ  ارزش    وضعیت        │
│ ☑ Ceftin 250      60      0     —     1405/04/11  1  ﷼117k  ⛔منقضی      │
│ ☑ Lopressor 50    300     0     —     1405/04/15  1  ﷼12.6k ⛔منقضی      │
├───────────────────────────────────────────────────────────────────────────┤
│ ۲ انتخاب‌شده → [قرنطینه گروهی] [جابجایی] [درخواست کسر] [صادرات] [مقایسه]  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Required changes

### Database (migration 0034 — additive)

```
inventory_exceptions
  id, pharmacy_id, fingerprint (unique per open exception), check_code,
  severity, score, status(open|assigned|accepted|resolved|suppressed),
  first_seen_at, last_seen_at, occurrences, resolved_at, resolved_by_id,
  assigned_to_id, disposition, disposition_reason,
  financial_impact, safety_impact, confidence, evidence jsonb
inventory_exception_rows        -- the affected records, all of them
  id, exception_id, entity_type, entity_id, snapshot jsonb
saved_views                     -- G7
  id, pharmacy_id, owner_id, name, scope, query jsonb, columns jsonb, shared
recall_cases / recall_case_lines  -- G8
abc_classification              -- G6: irc, class, basis, computed_at
inventory_movements  += session_id, device_id, actor_role, source_system  -- G5
inventory_lots       += quantity_in_transit, quantity_damaged, quantity_returned  -- G4
```

`fingerprint` is the deduplication key: `hash(check_code, entity_type, entity_id)`.
It is what lets a re-run recognise yesterday's finding instead of creating a
twin.

### API

```
GET    /inventory/exceptions                 ranked, filterable, paginated
GET    /inventory/exceptions/{id}            + ALL affected rows (not samples[:10])
POST   /inventory/exceptions/{id}/assign
POST   /inventory/exceptions/{id}/disposition    accept | suppress | resolve
POST   /inventory/exceptions/{id}/simulate       dry-run of the corrective action
POST   /inventory/reconciliation/run             writes findings; idempotent
GET/POST/DELETE /inventory/views                 saved views
POST   /inventory/recalls                        open a case from a lot/batch
GET    /inventory/recalls/{id}/affected          locations, movements, patients
GET    /inventory/audit/timeline                 merged, filterable
```

### Events

`exception.opened | .recurred | .assigned | .resolved`,
`count.posted`, `approval.decided`, `recall.opened | .closed`.
Consumed by notifications and the surveillance plane; **no event may mutate
stock** — they are observations.

### Audit log

Every mutating call records actor, role, session, device, source system, reason,
before/after, approval chain, and the request's concurrency token. The movement
chain already covers quantity changes; this extends the same discipline to
metadata edits and dispositions.

### Permissions

`inventory:count`, `inventory:audit`, `inventory:admin` (§4).

---

## 8. Deterministic vs statistical vs generative

| Layer | Used for | Must be |
|---|---|---|
| **Deterministic rules** | All 12 reconciliation checks, FEFO, approval policy, expiry, negative stock, chain verification, recall resolution | The *only* basis for anything that blocks, quarantines, or requires a signature. Confidence is always 1.0 — these are deductions, not predictions. |
| **Statistical models** | ABC classification, demand forecast, reorder point, expiry-waste forecast, variance-tolerance bands, diversion indicators | Advisory. Every output carries confidence, the evidence behind it, and an expected impact. Never auto-applies. |
| **Generative AI** | Drafting a case narrative from an established timeline; summarising an investigation for a human reader | **Never** in the decision path. It may describe what the deterministic layer found; it may not decide, score, rank, or identify. No LLM call is required for any routine operation, and the system runs fully offline. |

The boundary is enforceable, not aspirational: an exception's `confidence` field
is 1.0 for rule-derived findings and < 1.0 only for model-derived ones, and the
UI shows which produced it.

---

## 9. Non-functional requirements

**Concurrency** — every mutable record carries a version token; a PATCH with a
stale token 409s rather than overwriting. Approvals re-validate at apply time and
409 if the row moved since the request (already implemented for field edits).
Idempotency keys on all POSTs that create movements, preventing duplicate
submission.

**Rollback** — compensating movements, never destructive edits. The ledger is
append-only at the database level. A wrong count is corrected by a new approved
variance, not by editing the old one.

**Data quality** — reconciliation runs on a schedule and after every bulk apply.
`blocking=true` surfaces in the header regardless of which tab is open.

**Failure handling** — the dispense hook never blocks a dispense; failures are
recorded and detected by `fill_without_movement`. Same principle everywhere: an
inventory subsystem failure must not stop a patient receiving medicine, and must
never fail silently.

**Security** — tenant filter in every query's WHERE clause, not as a later check.
Bulk operations bounded (500 rows) and release-of-blocked-stock never bulk.

---

## 10. Acceptance criteria and KPIs

| Metric | Target |
|---|---|
| Reconciliation verdict | `blocking = false` for 14 consecutive days |
| Orphan fills | 0, sustained |
| Formulary binding | ≥ 95% of stocked items bound by GTIN |
| Count accuracy | ≥ 98% of counted lots with zero variance |
| Exception MTTR | critical < 24h, high < 7d |
| Exception recurrence | < 10% reopened within 30 days |
| Expired sellable stock | 0 at all times |
| Approval turnaround | p50 < 4h |
| Search latency | p95 < 500 ms |
| Drill-down depth | ≤ 2 clicks from any KPI to the affected rows |
| Chain integrity | intact, verified daily |

---

## 11. Roadmap

**Phase 0 — Foundation (this milestone).** Exception Register + drill-down
contract + impact ranking. Unifies the two panels into one loop.

**Phase 1 — MVP control centre.** Merged four-tab IA; saved views, export,
comparison; the three missing stock states; forensic audit fields; ABC-driven
cycle-count planner; recall workflow.

**Phase 2 — Advanced intelligence.** Demand forecast on real dispense history
(unblocked only once the hook has run in production); valuation and valuation
anomalies; explainable diversion indicators; recommendation-acceptance ledger.

**Phase 3 — Automation.** Auto-generated purchase proposals under approval;
surveillance-event correlation; supplier scoring; automated recall notification.

---

## 12. Next milestone: the Exception Register

### What

Give every reconciliation finding a durable identity, a lifecycle, an owner, an
impact score, and a complete list of affected rows — plus the deep-link contract
that makes it reachable from any tile and actionable in the Workbench.

### Why it must come first

Every other capability on the target list assumes a finding is a *thing*:

- **Ranking (Area 1)** needs somewhere to store a score.
- **Drill-down (Area 2)** needs a stable id to link to, and all affected rows
  rather than `samples[:10]`.
- **Corrective action from a finding (Area 3)** needs the finding to survive the
  round trip through the approval queue.
- **Audit of the response (Area 5)** needs first-seen, assignment, disposition
  and resolution timestamps — none of which exist for an ephemeral report.
- **"Was the recommendation accepted?" (Area 7)** is definitionally impossible
  without a persistent finding to accept.

Building any of those on today's stateless report means each one invents its own
half-persistence. **This is the dependency root, and it is a proven pattern in
this codebase already**: the drug-catalog side solved exactly this problem with
`issue_dispositions` (13 rulings recorded, board view, converging triage).
Inventory has no equivalent. The work is to bring the inventory side up to the
standard the catalog side already meets.

It is also what turns the panel from passive to operational, which is the
explicit goal.

### What gets built

**Backend**
1. Migration `0034`: `inventory_exceptions`, `inventory_exception_rows`, with
   `fingerprint` unique among open rows.
2. `services/core/inventory/exceptions.py` (pure): `fingerprint()`,
   `score()` (urgency × financial × safety × confidence, each term explainable),
   `lifecycle()` (open → recurred → assigned → accepted/resolved/suppressed),
   `reconcile_findings()` — diff a fresh report against the register, returning
   opened / recurred / resolved.
3. `POST /inventory/reconciliation/run` — idempotent; persists findings, closes
   the ones that no longer fire, never duplicates an open one.
4. `GET /inventory/exceptions` (ranked, filterable) and
   `GET /inventory/exceptions/{id}` returning **every** affected row.
5. `POST .../assign`, `.../disposition`, `.../simulate` (dry-run of the
   corrective action, reusing the ledger's planning functions so the preview is
   computed by the same code that would apply it).
6. Scheduled run wired to the existing task mechanism.

**Frontend**
1. `ExceptionRegister.tsx` — ranked list with score, age, occurrence count,
   owner, and the "why this rank?" breakdown.
2. `ExceptionDrawer.tsx` — evidence, full affected-row table, history, actions.
3. Deep-link routing: `#/inventory/exceptions/{id}` and
   `#/inventory/workbench?exception={id}` so a tile, an alert and an email all
   land on the same view.
4. Make every tile in `InventoryIntegrity` a link into the register; add the
   register's open-critical count to the header badge beside the approval queue.

**Acceptance for the milestone**
- Running reconciliation twice with no change opens zero new exceptions.
- Fixing an underlying issue resolves its exception automatically on the next run.
- Every finding on the Control Tower reaches its affected rows in ≤ 2 clicks.
- The 46 orphan fills display as 46, not 10.
- An assigned exception survives a backend restart with its owner and history.
