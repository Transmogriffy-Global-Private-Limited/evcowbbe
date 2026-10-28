# Admin rules: hub-independent tariff policy engine (source sprint)

**Status:** implemented in source on top of Fleet commit `c0db398d106ae84cd01ceff7501fc4fb23d576ba`.
**Not published, migrated, deployed, or enabled for customer quotes/payments.** Migration `004` is reserved
for the junior's User backend; this slice is `005` and its SQL explicitly refuses to run before `004`.

## Why this model

The reference [New Go CMS](https://github.com/Transmogriffy-Global-Private-Limited/ev-cms-backend-new)
separates (1) choosing a tariff by exact scope/effective date, (2) calculating energy/time/session
charges from measured quantities, and (3) applying independently resolved tax decisions.
Its hub is a **scope**, not an essential part of the price calculation. We preserve the useful mechanism,
not the CMS-specific `UserGroup > Charger > Hub` scope order, hub tax lookup, or charging-only economics.

Charger on Wheels has two distinct commercial dimensions:

- **Mobility and service:** visit fee, route distance, travel time, priority, eligible waiting, etc.
- **Charging:** metered kWh, measured charging duration, session fee, etc.

Each component has an explicit *basis*, per-unit decimal INR rate, tax class, and optional conditions.
Policy resolution uses trusted caller-provided scope precedence such as service tier, service zone,
or `GLOBAL/ALL`, not a hardcoded hub. A future job/quote owner must authenticate those attributes;
they cannot be asserted by a browser merely to obtain a discount.

## Actual source capabilities

### 1. Policy selection

`internal/pricing/temporal.go` reproduces the CMS temporal invariants per **exact** scope:

1. At most one published unbounded root (`starts_at` and `ends_at` absent).
2. Distinct open-ended effective starts are allowed; identical starts are rejected.
3. Bounded effective windows `[starts_at, ends_at)` may be strictly nested; crossing or identical
   bounded windows are rejected, including at publication under a same-scope advisory lock.
4. At an instant, deepest active bounded window wins over newest eligible open-ended policy,
   which wins over root. Across scopes, the **trusted caller** supplies precedence.
5. Missing policy, ambiguous topology, and malformed time intervals fail closed. A revision's
   terms are detached on resolution, not returned as mutable live database slices.

### 2. Deterministic evaluator

`internal/pricing/engine.go` evaluates frozen terms independently of HTTP, database or HAL.

| Kind | Meaning | `rate` convention | Interaction |
| --- | --- | --- | --- |
| `BASE` | One additive price component | INR per `basis` unit | Requires measured quantity and source |
| `OVERRIDE_RATE` | Substitute the target base component's rate | INR per `basis` unit | At most one override per base; applied before modifiers |
| `MULTIPLIER` | Time/tier/priority premium or reduction | Percentage, `100` unchanged, `120` means 1.2 times | Ordered by priority then rule ID |
| `DISCOUNT_PERCENT` | Pre-tax percentage reduction | `0..100` percent | Ordered by priority then rule ID |

Condition maps are exact, explicit equality predicates on trusted normalized facts. The caller must
provide all condition dimensions referenced by the policy; a missing priority/peak dimension is
**unknown**, not a reason to omit a surcharge silently. Rates and quantities
are **decimal strings** with at most six decimal places; Go `big.Rat` prevents IEEE-754 drift. All
line amounts, subtotals and taxes are rounded *half-up* to integer **INR paise**. The result exposes
the line-level evidence source, estimation marker, policy key/version, exact subtotal, tax and total.
Modifiers appear as signed adjustment lines, and `sum(lines.amount_minor) == total_minor`.

`PREVIEW` can use declared estimates; `FINAL` rejects estimated measurements. Both modes use the
same evaluator; neither invents missing measurements, route distance, HAL energy or service duration.
No matching base rule is an error, not a free quote. Unknown or unapproved tax treatment is an error,
not silently treated as zero tax. An explicitly approved tax class with all zero rates is different.
Central/State versus integrated GST cannot be mixed for the same class; rates and taxability are
provided externally from a legal/business decision, **never inferred from the CMS hub**.

An *illustrative mathematical test*, **not a configured company tariff or GST decision**:

```
TRAVEL: 5.5 km x INR 12.00/km = INR 66.00
VISIT:  1 x INR 100.00          = INR 100.00
VISIT priority: 120%             -> INR 120.00
VISIT discount: 10% after premium -> INR 108.00
Pre-tax subtotal: INR 174.00
```

### 3. Admin policy lifecycle

`DRAFT -> PUBLISHED -> WITHDRAWN`. Publication/withdrawal changes only lifecycle,
**never the stored rules**. A new tariff or policy revision is a new `policy_key + policy_version`
record. Audited change reasons, author, publication/withdrawal actor and full before/after snapshots
are stored transactionally. No implicit retroactive repricing. `pricing.read` and `pricing.manage`
are already in the deployed RBAC catalog, and the history route additionally needs `audit.read`.

| Method | Admin path | Purpose |
| --- | --- | --- |
| GET / POST | `/api/v1/admin/pricing/policies` | List (optional `status`, `limit`) / create immutable draft |
| GET | `/api/v1/admin/pricing/policies/{policy_id}` | Read, including publication state `ETag` |
| GET | `/api/v1/admin/pricing/policies/{policy_id}/history` | Last 100 audit changes |
| POST | `/api/v1/admin/pricing/policies/{policy_id}/publish` | Publish draft after conflict checks |
| POST | `/api/v1/admin/pricing/policies/{policy_id}/withdraw` | Stop future resolution |

Mutations require `Content-Type: application/json`, `Idempotency-Key` (16-128 printable ASCII chars),
and an auditable reason. Publish/withdraw additionally require strong quoted `If-Match` from the
policy's current `state_version`; `412` requires a fresh GET. A repeated identical idempotency key
replays the original committed response; reuse for different requests fails with `409`.

Example draft payload, **placeholder values only, do not publish as business rates**:

```json
{
  "policy_key":"ILLUSTRATIVE_STANDARD",
  "policy_version":1,
  "scope":{"kind":"GLOBAL","key":"ALL"},
  "currency":"INR",
  "reason":"Example for development review only",
  "rules":[
    {"id":"VISIT","kind":"BASE","component":"VISIT","basis":"VISITS","rate":"100","tax_class":"MOBILITY_SERVICE","priority":10},
    {"id":"TRAVEL","kind":"BASE","component":"TRAVEL","basis":"ROUTE_KM","rate":"12","tax_class":"MOBILITY_SERVICE","priority":20},
    {"id":"ENERGY","kind":"BASE","component":"CHARGING_ENERGY","basis":"METER_KWH","rate":"16.91","tax_class":"CHARGING_SERVICE","priority":30},
    {"id":"VISIT_PREMIUM","kind":"MULTIPLIER","target":"VISIT","rate":"120","priority":100,"conditions":{"SERVICE_TIER":"PRIORITY"}}
  ]
}
```

The API never lets the FE provide arbitrary “I am in this discounted group” attributes to the
resolver, nor does it offer public price evaluation before that trusted evidence source exists.

## Decision gates: not implemented or not yet authorized

This is a genuine **Admin tariff policy-management slice**, not a complete charging commerce stack.
Before a production quote/charge, the following must be implemented and independently accepted:

1. **Operator-approved values:** actual INR rates, per-vehicle/service-tier variations,
   zone definitions, time/holiday rules, minimums, caps, priority fees, wait and cancellation fees.
2. **Service scheduler and fact ownership:** trusted coverage-zone, route, visit count, travel/wait
   minutes, duty/dispatch and asset/driver evidence; no FE-provided “measurements” as authority.
3. **Approved tax decisions:** issuer and location classification, component taxability, rates,
   intra-/inter-state treatment, legal evidence and change history. Neither a missing profile nor
   the old CMS's illustrative GST split is a usable default.
4. **Quote and checkout lifecycle:** immutable accepted quote ID/version/expiry, evidence assumptions,
   client approval of repricing, availability and inventory reservation, cancellation semantics.
5. **Metering and settlement:** authoritative HAL readings/job timeline, pauses/resume, final
   bill reconciliation, payments, adjustment authority, refunds, invoices, customer/driver views.
6. **Expanded policy grammar:** price ceilings/floors, flat credits, post-tax adjustments, tiered
   distance/time brackets and holiday predicates need explicit semantics and tests before extension.

Other Admin rules belong to separate but integrated domains: service coverage and availability;
schedule/dispatch policy; driver/asset eligibility; cancellations/no-shows/waiting; promotions;
support/escalation SLAs; refunds and financial approval thresholds. Do not turn all of these into
an undocumented generic JSON-expression language; each needs typed inputs, approval and audit.

## Integration, deployment, verification and recovery

- **Source base:** Fleet `c0db398...`. No changes made to migration 001-003. Migration 004 is
  owned by the User branch. Merge/review/deploy it **before** running pricing migration 005.
- The two `005_admin_pricing_rules.sql` copies must be byte-identical. The SQL explicitly raises
  an exception if schema migration 004 is absent. Existing database migration commands must not
  be run blindly. First take and restore-check a new backup of the target database.
- On the original source or a clean isolated candidate checkout: `go test -p 1 -count=1 ./...`,
  `go vet ./...`, `go build ./cmd/server`, `git diff --check`.
- Real Postgres lifecycle/concurrency integration is guarded by `TEST_DATABASE_URL` and refuses
  an app DB. It requires a disposable `*_test` database with migration 004 available; otherwise
  the guarded test is **not exercised**. Test API roles, preconditions and replay with FE before
  any production publication. A green mockless unit suite is not a live Postgres proof.
- On VPS2, inspect exact Git commit, check `rehost-evcowbbe check-env`, verify current schema,
  archive hash and isolated restore, apply 004 then 005 with explicit authorization, deploy via
  the existing `rehost-evcowbbe deploy`, then verify health, version, OpenAPI, RBAC allow/deny,
  audit, and legitimate Admin-only requests. **Do not auto-apply migration on server startup.**
- Binary rollback is not schema rollback. Published policies and historical invoices must be
  preserved; repair forward if needed, or perform a separately approved data recovery procedure.

Reference CMS implementation:

- [`src/commercial/tariff_temporal.go`](https://github.com/Transmogriffy-Global-Private-Limited/ev-cms-backend-new/blob/main/src/commercial/tariff_temporal.go)
- [`src/commercial/session_amount.go`](https://github.com/Transmogriffy-Global-Private-Limited/ev-cms-backend-new/blob/main/src/commercial/session_amount.go)
- [`src/customerauth/pricing.go`](https://github.com/Transmogriffy-Global-Private-Limited/ev-cms-backend-new/blob/main/src/customerauth/pricing.go)

Program blueprint: `normative/36_PRICING_TAX_PAYMENT_REFUND_INVOICE_CONTRACT.md` and
`normative/27_BUSINESS_DECISION_GATES_AND_POLICY_REGISTER.md` from the sole-source package.
