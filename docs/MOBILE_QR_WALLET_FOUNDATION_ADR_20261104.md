# Charger on Wheels: serviceability, roaming/QR and wallet contract

**Status:** adopted business requirements; implementation foundation candidate.
**Decision date:** 2026-11-04 (Asia/Kolkata).
**Basis:** user-confirmed amendments to the 2026-09-24 CoW normative blueprint, notably Annexes 35, 36, 39, 40 and 42. These decisions supersede the previous blueprint only where contradicted: scheduled customer bookings are out of the active MVP; service-area centres are **not** the default fare distance origin; and wallet-first commercial settlement is mandatory. All unaffected auth, locking, HAL, event, audit and privacy requirements remain.
**Runtime status:** PURE policy module only. No changes to live API, VPS, PostgreSQL, schema, wallet, charging hardware, or Razorpay credentials. Do not call this payment/charging ready.

## 1. Required product behavior

### 1.1 Two ways to charge, one physical/financial truth

| Mode | Start of intent | Driver involvement | Mandatory QR start | Transport |
|---|---|---|---|---|
| MOBILE_REQUEST | Immediate customer service request (no future-date booking) | Eligible, on-duty Driver **voluntarily accepts** a durable offer | Yes, on arrival at permitted location; an authenticated customer scan binds session to arriving charger/connector | Zone+distance-slab transport charge |
| WALK_UP_QR | Customer physically present at charger, scans verified QR | None; charger may be fixed or parked mobile if policy permits | Yes | No default travel charge; separate QR tariff policy |

Keep `ChargingIntent` as common parent commercial request; `MobileServiceRequest/ServiceJob` is an **optional operational child**, not a requirement for any `ChargingSession` or `HALCommand`. Shared billing, charging session, secure HAL command/fact, energy measurement, stop, final settlement and reconciliation should not branch by origin except at admission/tariff selection. An authenticated QR scan is not authorization to run an arbitrary charger. Hardware proof of charging must come from the HAL facts, not HTTP 202.

### 1.2 Service zones, not dispatch depots

A named, versioned zone has WGS84 centre and radius in metres. Its centre is used **only** to evaluate serviceability, policy, and alerts by default. It does **not** define where vehicles/drivers must remain or where travel fare starts. Vehicle/Driver zone assignment is soft/suggested unless a separate Admin restriction is explicitly enabled. Operating restrictions and Driver preferences are separate constraints; a Driver's voluntary area cannot expand a platform hard-restricted area.

Zone modes:

- `STRICT` (default): customer/service destination beyond radius is rejected before accepting payment or dispatching. Radius boundary counts as inside.
- `ADVISORY`: outside destination is allowed but flagged to Admin/customer, and MUST use an explicitly configured out-of-zone tariff; if no valid outside tariff exists, reject. Do not silently use the inside tariff.

**Tariff ownership: the assigned Driver's operating zone, not the customer's nearest zone.** Every mobile distance-slab tariff revision must be published for one named service zone and one specific zone-geometry version. That single zone has *two separate tariff schedules*: INSIDE and OUTSIDE. When a selected eligible Driver is assigned to Zone A, evaluate the requested destination against **Zone A**. If inside, apply **Zone A INSIDE** slabs; if outside and Zone A is ADVISORY, apply **Zone A OUTSIDE** slabs. If Zone A is STRICT, reject the out-of-zone request even if another zone covers the destination. A customer geographically inside Zone B does **not** switch a Zone A Driver to Zone B's rates. No broad platform/global mobile fallback, and no price shopping across overlapping zones.

**Driver-zone assignment is an authorization and tariff-selection identity, not a movement fence.** An authorized operations process may loosely assign a Driver to more than one zone. If there is exactly one effective assignment, use it; with several effective assignments, use the single designated primary assignment or an explicit, authorized assignment selection made by the dispatch service. Otherwise reject an ambiguous quote. The customer must never select an arbitrary zone, assignment, or rate. Selecting a candidate Driver happens *before* a binding quote. Quote snapshots pin that Driver, chosen assignment, zone and geometry version, published tariff revision, inside/outside classification, road route, and expiry. A later acceptance must revalidate the Driver assignment and quote expiration. If the Driver/assignment changes, re-quote with the customer's authorization or use an explicitly approved audited subsidy; never quietly increase the transport charge.

**Publication and consistency:** at most one effective PUBLISHED mobile tariff revision for each zone/version/time; overlapping published revisions are an administrative conflict, not a reason to pick the lowest/highest. Rate publication and zone edits must be versioned, audited and immutable for completed quotes. A newer zone geometry version needs a matching published tariff revision before new quotes can use it. A zone's default restriction remains STRICT; publishing an OUTSIDE schedule alone does not override strict restriction.

### 1.3 Distance and slabs

Default price distance = **fresh, routed road distance from a selected eligible Driver's CURRENT observed location to the requested service destination**. The Driver's actual live location must have acceptable age/accuracy. Geodesic/Haversine is for zone membership or provisional ranking, NOT a billable road distance. A routing provider timeout or stale location blocks a binding quote; present `PRICE_UNAVAILABLE` rather than fabricate a distance. Admin may opt into a published alternative origin (e.g. zone centre) as a *new tariff policy version*. Never silently substitute it.

Since the accepting Driver is unknown at initial customer confirmation, first select one eligible, zone-assigned candidate, quote using **that candidate's selected operating zone** and current verified route origin, and initially offer that Driver. The assignment is a server-verified choice, not an arbitrary price parameter in customer JSON. If another Driver must be offered, the user's already-accepted price remains protected: re-quote/re-authorize explicitly (releasing/reconciling the prior debit) or apply an separately approved, audited platform subsidy. Never increase a customer's frozen transport charge merely because dispatch reassigns the Driver. A quote is immutable and expires; stale quotes cannot auto-renew or reprice.

Slabs are `[from_m, to_m_exclusive)` with contiguous, ordered ranges, starting at 0 and one unbounded last slab. Exact boundary belongs to the NEXT slab. Inside/outside tariffs may use separate rates, floors and time-effective revisions. Rate amounts are **Admin inputs only**; there are no live defaults in source. Changes affect only new quotations after policy activation; accepted historical quotes keep snapshots.

### 1.4 Wallet and `one final transaction`

User-facing upfront amount on mobile acceptance: `transport_minor + minimum_minor` in a published tariff currency (INR/paise initially). The **available wallet balance drops immediately** through one idempotent, ledger-backed booking debit. Do not represent that reduction as earned charging revenue before performance. Backing accounting records distinguish prepaid liability, reserved/consumed value and recognition, as dictated by the final seller/commercial structure.

Default `minimum_treatment=SPEND_FLOOR`: on successful charging settlement, the user pays `transport + max(verified_charging_amount, slab_minimum)`. The final difference against the already-posted upfront amount is applied **once** as ONE atomic additional debit, credit or zero adjustment. No repeated wallet deductions on meter ticks. If `REFUNDABLE_ADVANCE` is explicitly selected for a new tariff revision, the minimum is an advance and unused value may be credited back, but this is not the default.

Example (illustrative amounts, NOT published rates): ₹100 transport + ₹200 slab minimum = ₹300 upfront. Verified charging final ₹350 implies total ₹450 and ONE ₹150 final extra debit. Verified charging ₹150 under spend-floor implies ₹300 final total and no extra debit. Invalid/unknown HAL meter proof blocks finalization; don't infer power or earnings from accepted commands.

**Invariant:** booking debit and final settlement are separate chronological business events. “One transaction” is interpreted as **one final settlement posting**, not an impossible single ledger event both before booking and after charging. Ledger entries are immutable, duplicate effects prevented with DB uniqueness; cancellation credits have their own auditable event identity.

Before starting or continuing a session, a conservative balance/energy control must limit exposure beyond the booking deposit. This requires policy-configured estimates, safety buffers and trusted HAL state. At insufficient balance or an uncertain stop, move to explicit hold/reconciliation rather than inventing a successful stop or silently creating negative-wallet debt. Real-money enablement is blocked pending provider and regulatory checks.

### 1.5 Cancellation and Driver penalty

Cancellation is a different financial decision from successful charging. Publish versioned rules on **actor** (customer/Driver/platform), **phase** (before accept, en route, arrived, charging started), reason and verified evidence. Supports:

- `FULL`: refund all upfront via one wallet credit.
- `NONE`: refund none, if allowed by the approved customer policy and law.
- `TRAVEL_PROPORTIONAL`: retain a fee derived from **verified distance actually travelled AFTER Driver acceptance** toward the user, capped at upfront transport or a tighter configured cap; return the remainder. The charged minimum is normally returned if charging never begins.

Recommended initial defaults for later Admin publication: no Driver accepted/platform failure = FULL; Driver cancellation = FULL customer refund, with optional **separately adjudicated** Driver penalty; customer cancellation en route = transport-only proportional fee upon proof; active charging cancellation = `STOP_PENDING` then HAL reconciliation before any settlement. All rate/fee/penalty choices remain configurable and require publication. Driver penalty is an assessment, NOT a customer-wallet debit or an automatic payroll deduction. Unknown travel evidence cannot justify a travel charge.

### 1.6 Address and proximity

Customer addresses may have optional latitude/longitude during address-book CRUD; a location-based mobile order requires an explicitly selected, verified coordinate before quoting. No fake `0,0`, inferred city centre or fallback to stale map pin. The user may choose `REQUEST_ADDRESS` or consent to `CURRENT_CUSTOMER` location at mobile-arrival QR scan. Accepted quote/request location snapshots must never be silently rewritten.

Admin publishes QR proximity radius, accepted location basis, sample max age and accuracy. At mobile arrival, the authenticated user scans a **server-bound, short-lived, single-use** QR challenge identifying the exact charger and connector, current request/customer, expiry and nonce. Both customer location and independently attested charger location must pass proximity; raw browser GPS, printed static QR content or Driver self-report alone is insufficient proof. Geolocation alone is not foolproof against spoofing, so admission also requires a server-challenge/connector lock. If a trusted charger location is unavailable, block physical start until approved verification or explicit audited safety override. No general bypass toggle for arbitrary FE callers.

Walk-up QR follows the same identity/connector ownership and lock contract, with no Driver/ServiceJob or transport charge. At most one active session per physical connector; hardware boot/change, replay, reassignment and double-scan are reconciled against HAL transaction identity.

### 1.7 Payment provider/merchant agnostic

Customer can pay for charging **only from wallet**. The configured top-up adapter is `RAZORPAY` for now, behind a `PaymentProvider` interface. Razorpay success UI callback **never** credits wallet. Verify webhook HMAC over *raw bytes*, durably deduplicate provider events and separately enforce exactly-once credit by `(provider, merchant_account, environment, payment_id)` after authoritative captured amount/currency/order/ownership confirmation. Timeout is `UNKNOWN` until reconciled; do not credit or recreate orders speculatively. No secrets in source/client.

Seller-of-record/merchant of record remains unresolved. Do not assume TransEV sole seller or route third-party wallet payments before commercial/legal classification, merchant KYC, refund policy and RBI applicability are established. **Production charging and real-money top-ups remain feature-gated.** Provider/payment models must support seller/account identity instead of hardcoded business ownership. The future provider may be swapped without changing the wallet ledger identity or pricing/dispatch lifecycle.

## 2. Persistence and boundary contracts to implement next

This code package intentionally does not add a Django migration yet. The existing project has schema-identity-only deployment. First extend `rehost-evcowbbe` with an independently reviewed/verified migration-capable lifecycle; then perform additive, reversible migrations in the isolated acceptance database before attempting active development DB upgrade. Preserve old `ServiceJob`/`ChargingSession` relation semantics while adding nullable shared references, backfill only source-verifiable records, and use staged constraints rather than dropping old FKs.

| Aggregate | Stable identity, data and constraints | Owner |
|---|---|---|
| `ServiceZoneVersion` | id, immutable version, centre, radius, STRICT/ADVISORY, effective window, author/audit, one effective published geometry | Admin policy |
| `DriverZoneAssignment` | immutable assignment ID, Driver ID, zone ID, effective start/end, unique effective primary where needed, authorization audit; no hard location confinement implied | Workforce |
| `TariffRevision`/`DistanceSlabRevision` | one zone ID + geometry version, MOBILE_REQUEST mode, separate INSIDE/OUTSIDE contiguous slabs, route origin, minimum treatment, effective window, PUBLISHED state and publication audit; reject overlapping effective revisions rather than use global fallbacks | Pricing |
| `ChargingIntent` | one customer, mode, requested/snapshotted position, selected charger/connector (when known), immutable quote reference, current lifecycle and events | Charging admission |
| `CommercialQuote` | selected Driver + assignment ID, assigned zone + geometry version, matching tariff revision, classification INSIDE/OUTSIDE, route provider/origin/distance/expiry, line items, upfront requirement, immutable accepted snapshot | Pricing |
| `WalletAccount`/`WalletEntry` | owner, currency, immutable balanced journal, unique business idempotency identity, monotonic balance/version, account-level DB locks, no overspend | Wallet |
| `WalletBookingDebit` | exact quote/intent, transport+minimum, single chronological upfront effect, recoverable outcome unknown state | Wallet |
| `WalletFinalization` | once per successful terminal intent/session, verified HAL energy and actual amount, at most one final delta posting, `RECONCILIATION_REQUIRED` when uncertain | Settlement |
| `TopUpIntent`/`ProviderEvent` | internal ID, merchant/seller account, Razorpay order, captured payment, durable event digest, environment, idempotent wallet credit | Payment adapter |
| `DriverLocationSample`/`Offer`/`Assignment` | time and accuracy, fresh eligibility, bounded offer expiry, winner accepted under transaction locks, travel evidence after accept | Dispatch |
| `QrChallenge`/`QrAdmission` | nonce hash, expiry, subject, intent, exact connector, consumed_at, trusted geo evidence, enforce once-only activation | QR |
| `ChargingSession`/`HALCommand` | common intent FK; existing job FK optional by mode; physical connector exclusion, immutable HAL fact ingestion; genuine stop/recovery | HAL |
| `CancellationCase`/`DriverPenaltyCase` | actor, phase, policy revision, route evidence, retained/refund, explicit review and audit, unique settlement identity | Cancellation |

**Concurrency ordering:** deterministic locks on wallet account, intent/job, quote, offer, and connector/charging physical resource according to the global annex-42 lock graph; do not accidentally introduce a second lock-order cycle. Each public mutation has an idempotency key, and unique DB constraints enforce invariants even when two workers bypass normal path. External API calls, SMS and HAL commands are queued *after* durable local state (outbox/worker), not sent inside a transaction.

**Temporal rule:** store observed/event times and trusted received times separately; reject stale/out-of-order location and payment/HAL facts from advancing current truth. Quote/tariff version and rate/timezone are frozen at confirmed customer transaction. End/cancel/requote are explicit transitions, not overwritten columns.

## 3. Minimal API contract plan (PROPOSED, NOT REGISTERED)

Customer: `GET /api/v1/app/serviceability`, `POST /api/v1/app/mobile/quotes`, `POST /api/v1/app/mobile/confirm`, `GET /api/v1/app/charging-intents/{id}`, `POST /api/v1/app/qr/admissions`, `GET /api/v1/app/wallet`, `GET /api/v1/app/wallet/entries`, `POST /api/v1/app/wallet/topups`, `GET /api/v1/app/wallet/topups/{id}`, `POST /api/v1/app/charging-intents/{id}/cancel`.

Driver: `POST /api/v1/driver/location`, `GET /api/v1/driver/offers`, `POST /api/v1/driver/offers/{id}/accept`, `POST /api/v1/driver/offers/{id}/decline`, `GET /api/v1/driver/duty`, duty lifecycle and arrival routes with source-verifiable evidence.

Admin: zone records/geometry revisions, `GET/POST /api/v1/admin/zones/{zone_id}/mobile-tariffs/revisions` (proposed, not registered), publish/withdraw per-zone INSIDE/OUTSIDE slabs, read-only quote simulation, `GET/POST /api/v1/admin/drivers/{driver_id}/zones` (proposed), explicit primary-zone changes, Driver eligibility/assignments, dispatch diagnostics, cancellation/refund approvals, wallet reconciliation, provider event audit and QR/charger policy. List/preview responses must show tariff owner zone/geometry revision, active Driver assignment, inside/outside classification, effective policy and quoted distance. Do NOT allow customer requests to supply a price-owning zone ID, arbitrary assignment ID, slab or rate. Only the authorized dispatch coordinator can select from authoritative effective Driver assignments.

Admin pricing migration: **do not reuse the generic legacy `PricingPolicy` selection as a mobile tariff fallback**. The existing Admin `pricing/policies` may remain for compatibility while we add explicit mobile zone-owned revision resources, migration/backfill validation and a gated quote cutover. A published mobile tariff belongs to one zone and one geometry version; no global/platform mobile default, and no silent selection by overlapping customer zones. The WALK_UP_QR tariff is a separate charge-only policy keyed to an authenticated charger/site context; it does not inherit a mobile Driver's transport slabs. Both modes settle through the same wallet journal.

Payment provider: dedicated `POST /api/v1/internal/providers/razorpay/webhook` using raw request bytes, bounded size, independent secret and replay inbox. Never expose signing material or provider operations to browser FE.

**Compatibility:** preserve existing current Go-parity operations, operation IDs and token plane isolation until the FE cutover report is reviewed. New APIs use fully typed request/success/error schemas and examples. The original `/api/v1/app/service-requests`, quote and Admin job paths must be bridged or deprecated deliberately; do not silently expose two conflicting live pricing authorities. Existing `PricingPolicy` `ACTIVE` versus new `PUBLISHED` requires explicit cutover with backfill and comparison, not hidden fallback.

## 4. Efficient execution chronology and verified gates

1. **Now:** add pure policy kernel and regression tests (this package), bind requirements to published ADR; no DB/service activation.
2. **Release prerequisite:** implement schema-aware, preflight-tested migration operator, with marker-protected scratch PostgreSQL replay, backup/restore rehearsal, manifest/seal and recovery journal; do not weaken existing code-only gate.
3. **Financial vertical:** additive wallet accounts + double-entry journal + immutable quote/policy tables, atomic booking debit, unique final settlement, refunds and simulator. Add property/concurrency tests for sums, duplicate keys and account races.
4. **Zones/routing vertical:** Admin zone and separate zone-scoped INSIDE/OUTSIDE tariff publication; Driver zone assignments and effective primary-selection rules; strict/advisory classification against the *assigned Driver's* zone; current-location routing evidence; immutable quote and explicit candidate-change re-authorization. Integrate customer price preview/confirm with wallet.
5. **Workforce/dispatch vertical:** Driver onboarding and eligible on-duty claims, self-accepted offers, race winner, cancellation tracking of post-accept travel, operator diagnostics, outbox/retries.
6. **QR vertical:** mobile arrival and walk-up admission, server challenge and trusted proximity, shared connector/session graph, explicit stop/reconciliation. Keep hardware effects simulated until certified.
7. **Gateway vertical:** Razorpay TEST-only order/top-up adapter, signed webhook durable inbox, captured reconciliation exactly once; do not enable LIVE until seller/regulatory review approved.
8. **Contract/FE closure:** typed Swagger per mode, CORS/preflight, idempotent integration fixtures, user balance/receipt screens, known error paths and end-to-end simulator demonstration; targeted PostgreSQL racing tests.
9. **Hardware+commercial approval:** physical charger HAL conformance/energy accounting, tax/entity treatment, monitoring/recovery drill; separate documented decision for real-money go-live. No use of `test` status as proof of live hardware acceptance.

**Nonnegotiable readiness gates:** mixed zone radius boundary, Driver assigned to A while customer inside B, multiple effective Driver assignments, multiple effective published tariff revisions, changing assignment during an offer, no global mobile tariff fallback, distance-slab exact edge, assigned-zone out-of-zone price, route stale/timeout, quote expiry/reassignment, concurrent wallet and provider credit, customer-vs-Driver cancel, Driver post-accept travel provenance, QR spoof/reuse, double connector claims, charger/disconnect/meter mismatch, delayed stop, uncertain provider response, manual reconciliation and rollback. Record actual test provenance and no empty `PASS` claims.

## 5. Open decisions, resolved by safe defaults rather than questions

1. Rate amounts/zone coordinates, Driver assignments and assignment primary choice: unpublished Admin configuration until set. Zone priorities do NOT decide mobile tariff ownership; the selected Driver's authoritative assignment does.
2. Driver candidate pricing drift: fail closed/requote on different accepting Driver; no automatic additional transport debit.
3. Default cancellation matrix: proposal in section 1.5; cannot charge unapproved rates until Admin publishes.
4. Wallet seller and tax/legal responsibility: unresolved, gate live money; no assumption of closed-loop qualification.
5. Road routing and trusted device/hardware location providers: dependency contracts require fresh evidence; unavailable means no binding quote/QR start, not fake proximity.
6. QR proximity radius is policy parameter, no arbitrary fixed default used for live decisions.

## 6. Links to external constraints

- RBI, Master Directions on PPIs (updated February 2024): https://www.rbi.org.in/scripts/BS_ViewMasDirections.aspx?id=12156 . Closed-loop vs third-party merchant usage depends on seller/payment structure; this package takes no legal-position shortcut.
- Razorpay, validating webhook HMAC and durable event dedup: https://razorpay.com/blog/payment-gateway-webhooks-india/ ; signature verification is necessary, not sufficient to authorize wallet credit.
