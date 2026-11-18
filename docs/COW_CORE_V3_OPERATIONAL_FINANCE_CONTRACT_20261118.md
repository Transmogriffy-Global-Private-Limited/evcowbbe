# CoW V3: maps-independent booking operations + universal wallet authority

**Source-only release candidate**, 2026-11-18 Asia/Kolkata. Prior verified Commercial V2 checkout = pinned branch `anubhab-work`, HEAD `58a4144d8f8a6a26ed718028068029fda24ef97b`, 8 modified + 21 untracked files. Go HAL stays at `1908c934c0e2570b81523e8ef3fe20be1e519da0`; untouched. Migration **0019** depends on unapplied 0018 which depends on unapplied 0017. **No migration, app tests, DB work, Go changes, physical charger, deployment, commit or push has been executed.** All feature-release gates default OFF.

## Source truth and limits

1. This overlay implements **one wallet write authority** (`cow.services.wallet_authority`). The original Razorpay captured-recharge path and V2 deferred booking settlement delegate posted writes to it; source search against pinned HEAD found the prior direct writer under `cow/users/repository.py`. It serializes on the wallet row, counts all active commercial holds, refuses other-booking reservation spending, rejects negative balances, and persists a uniquely keyed `CoWWalletOperation` plus `WalletTransaction` within one database transaction. An exception rolls back all. This does not certify unreviewed plugins, raw SQL, external actors or historical data.
2. One accepted ServiceJob reserves fixed fees + minimum charging + buffer. Later physical starts reserve the full allowed exposure and send **minimum affordable Wh plus affordable extra Wh** under the one frozen tariff, minus prior completed sessions, through existing HAL V1. Only physically accepted HAL `transaction.started` advances job `SERVICE_PREPARING -> CHARGING` in V3. Posting still occurs only after separately approved final completion. Overshoot and uncertain commands remain quarantined. Existing V2 commercial arithmetic and tariff/GST evaluator stay authoritative.
3. Maps provider is **not required**. Pure geometry uses WGS84 great-circle (geodesic) distances. `approved_distance_m` for tariffs is customer-frozen address pin to published Zone centre, not road mileage and not an independent GPS-hardware certificate. Business must explicitly approve this price metric. Source-selected Zone must be published/eligible and consistent. The price and basis freeze on acceptance. Driver GPS is authenticated **device-submitted** evidence: field validation, observation age, sequence and plausibility are checked, but spoof-resistant geolocation is not guaranteed.
4. Driver arrival must be *definitively within radius*, taking claimed accuracy into account. On uncertain/outside or stale GPS, wait or fail closed. OTP is generated server-side, stored as a keyed HMAC, shown only to the authenticated customer, entered by the assigned driver (max 5 attempts), then followed by explicit customer consent. A one-use handover token is verified and consumed by the booking Start transaction. Distinct token types for preview, OTP and handover prevent upgrading mere location to charging authority.
5. Admin dispatch assignment requires current eligible duty, available assets, active duty-to-Zone assignment, and no second open job on that duty. Driver progression is `ASSIGNED -> ACCEPTED -> EN_ROUTE -> ARRIVED -> SERVICE_PREPARING [OTP] -> CHARGING [actual HAL fact] -> WRAP_UP [completed HAL evidence] -> COMPLETED [independent Admin approval]`. The old `ServiceJob` machine remains authoritative; no parallel state machine replaces it.
6. Completion approval verifies HAL physical evidence and a separate driver closure. Only the approved closure ref can permit V2 final debit. Refunds in V3 are **wallet credits only**, with a second Admin approving the original request and an aggregate refund ceiling not exceeding the posted booking charge. Zero delivered Wh cannot enter the ordinary paid-completion path and requires independent service-fault review. These are NOT PSP reversals, bank refunds, GST credit notes or tax invoices.
7. Customer tracking is owner-only and exposes an explicit `LIVE`/`STALE`/`NO_SIGNAL` state. Admin needs `dispatch.read`; drivers are limited to their assigned jobs. **Real WebSocket streaming is not included** because the current project's ASGI realtime transport is uncommissioned. HTTP position polling works independently; the event envelope `cow.driver-location.v1` is stable for later push transport. Do not advertise turn-by-turn guidance or a road ETA without a configured routing engine.

## Feature switches, all OFF unless separately approved

```dotenv
COW_LOCATION_MODE=DISABLED
COW_MAPS_ROUTING_PROVIDER=NONE
COW_LOCATION_RECEIVE_ENABLED=false
COW_DRIVER_WORKFLOW_ENABLED=false
COW_HANDOVER_OTP_ENABLED=false
COW_ADMIN_CLOSURE_APPROVAL_ENABLED=false
COW_WALLET_REFUND_POST_ENABLED=false
COW_BOOKING_TRUST_PROVIDER=
COW_HANDOVER_HMAC_KEY=
COW_ARRIVAL_RADIUS_M=100
COW_LOCATION_FRESH_SECONDS=90
COW_LOCATION_MAX_ACCURACY_M=50
COW_BOOKING_SESSION_CEILING_WH=100000
COW_BOOKING_PROTECTED_BUFFER_PAISE=0
# All existing Commercial V2, HAL V1, and financial-posting feature gates stay false.
```

After separate integration/QA, the candidate no-maps mode would use `COW_LOCATION_MODE=DEVICE_GPS_WITH_OTP`, `COW_MAPS_ROUTING_PROVIDER=NONE`, `COW_BOOKING_TRUST_PROVIDER=cow.services.booking_trust_nomap.provider`, a **genuinely approved positive** buffer, and a secret managed 32+ character OTP HMAC key. Do not enable this recipe without approving policies, DB migrations, security tests, source audit, real Go-HAL command contracts and driver/customer app acceptance. Any non-NONE routing provider must be a separately reviewed dotted adapter implementing `route(start,end,context) -> RouteEstimate` with normalized units and timestamp; it must own its own credentials/timeouts. Swapping routes does not rewrite the frozen pricing basis.

## HTTP API contracts for independently developed frontends

All return `{ "data": <typed JSON object> }` with explicit Django Ninja/Pydantic V3 response schemas in `cow/common/nomap_wire.py` unless a domain error occurs. All authenticated operations use the existing app/driver/admin bearer planes, not user-provided plane/role booleans. `Idempotency-Key` is a unique bounded client value for guarded mutations; location samples additionally have durable `client_event_id` and monotonically increasing `sequence` per (job,actor). `expected_version` is the current `ServiceJob.version` for state changes. Existing V2 customer booking acceptance/start/stop and HAL read interfaces remain unchanged.

| Plane | Operation | Path below plane root | Semantics |
|---|---|---|---|
| Customer | Generate quote/consent ticket | `POST /commercial/no-maps/preview` | Payload `request_id`, optional `zone_id` assertion; server chooses the canonical published Zone deterministically (most-specific covering Zone, otherwise nearest advisory Zone); returns exact quote and short-lived `acceptance_token` bound to published Zone + geodesic metric |
| Customer | Accept one booking | EXISTING `POST /commercial/bookings/accept` | Customer passes preview token + request id; durable mandatory wallet hold, no debit |
| Customer | Submit on-demand location | `POST /jobs/{job_id}/location` | GPS observation: latitude, longitude, accuracy_m, ISO-aware `observed_at`, sequence, `client_event_id` |
| Customer | Where is my driver? | `GET /jobs/{job_id}/driver-tracking` | Owner-only latest position, state/staleness; no fabricated road ETA |
| Customer | Create OTP for assigned driver | `POST /commercial/bookings/{booking_id}/handover/otp` | Returns OTP once; retries are credential-safe; new request key can revoke an unverified OTP, bounded rate |
| Driver | Assigned job list/detail | `GET /jobs`, `GET /jobs/{job_id}` | Current duty/assignment authorization |
| Driver | Submit location | `POST /jobs/{job_id}/location` | Validates device observation, job+duty binding, age/sequence/movement |
| Driver | Accept, depart, arrive | `POST /jobs/{job_id}/accept`, `/depart`, `/arrive` | `expected_version`; arrival must prove geofence and accuracy |
| Driver | Verify customer OTP | `POST /handover/verify-otp` | `ticket_id`, `otp`, `connector_id`; binds current duty/charger and transitions ARRIVED -> SERVICE_PREPARING |
| Customer | Confirm handover | `POST /commercial/handover/{ticket_id}/customer-consent` | After driver verification, issues one-use, expiring `handover_token` |
| Customer | Start physical charging | EXISTING `POST /commercial/bookings/{booking_id}/charge` | Rechecks wallet, duty, OTP and geofence; stages HAL-local Wh cutoff |
| Driver | Propose completed service | `POST /jobs/{job_id}/completion` | `expected_version`, durable `driver_ref`; requires all physical sessions complete |
| Admin | Assign duty to booking | `POST /jobs/{job_id}/assign-duty` | `duty_id`, `expected_version`, `reason` and RBAC `dispatch.manage` |
| Admin | Inspect driver's live position | `GET /jobs/{job_id}/driver-tracking` | RBAC `dispatch.read`, active job scope only |
| Admin | Approve driver-completed booking | `POST /commercial/bookings/{booking_id}/approve-completion` | `review_ref`, `reason`; independent `jobs.manage` + `payments.manage` |
| Admin | Post one final debit | EXISTING `POST /hal-v1/commercial/bookings/{booking_id}/settle` | Requires real completion review and original financial gates; uses universal wallet authority |
| Admin | Request wallet refund | `POST /commercial/wallet-refunds` | `settlement_id`, `amount_paise`, `reason`, `evidence_ref`, idempotency |
| Admin | Independently approve refund | `POST /commercial/wallet-refunds/{refund_id}/approve` | Separate authorized approver, irreversible wallet CREDIT only, no PSP action |

Existing driver duty/schedule, Admin Zone/tariff, customer wallet/Razorpay and HAL V1 endpoints remain available and must be reused. No Client/Admin/Driver endpoint may claim a location proof or finance authorization by setting a bare boolean.

## Critical integration tests required BEFORE any feature enablement (not executed)

- Django startup/model and migration graph: `0017 -> 0018 -> 0019`; `makemigrations --check`; disposable PostgreSQL migration and reverse-migration recovery; fresh and upgraded data sets.
- Payment order capture concurrently: same payment ID and same/different order, exactly-one credit; existing wallet tests must still pass. Concurrent captured top-up vs booking acceptance/start/settlement/refund; global lock order, ledger receipt rollback if an exception occurs, wallet non-negative and reserved <= posted invariants; refund replay/different-approval conflict; dual Admin approval and ceiling.
- Quote preview: server-determined canonical most-specific published covering Zone, nearest advisory fallback, strict/advisory boundaries, geodesic vs road, multiple Zones, scheduled tariff/GST revision, expiry, HMAC/OTP and idempotency; no duplicate request job or acceptance without customer assent.
- GPS: lat/lon overflow, nonexistent coordinate, stale/future/naive time, speed jump, accuracy boundary uncertainty, out-of-order sequence, owner/driver cross-job information disclosure, tracker state after terminal state, location retention and sensitive logs.
- Driver/Admin: concurrent assignment to same duty, dispatch/OTP/duty switch, expired/failed OTP under transaction rollback, customer consent after location changes, alternate connector, physical START fact job transition, metering STOP uncertainty, multiple physical starts, driver-completed state only after all HAL receipts, Admin reviewer independence.
- Physical charger and Go HAL: previous caps persisted before energizing, network split Go-HAL <-> CMS, stop latency and overshoot, charger-offline gaps, top-up after first session, physical completion identity, financial quarantine; no live cutover until certified.
- Provider swapping: `NONE` yields explicit unavailable road ETA; adapter error never downgrades geofence or OTP; frozen booking tariff/route reference never reprices from a new adapter.

## Explicit unfinished items and policy owners

- Road-aware navigation, ETA, map rendering and scalable WebSocket delivery are LATER optional provider modules, not required for existing location/OTP business logic.
- Cancellation/no-show/operator-fault and partial delivery service charge policy, tax invoices/credit notes, PSP-initiated original-payment-method refunds and financial debt recovery remain **not implemented**. Owners: commercial/legal/tax/operations. Fail closed; never invent charges, waive outstanding evidence or bypass reserved funds.
- Full production device attestation, GPS spoofing mitigation, fraud monitoring, data retention controls and tested push-based tracking require privacy/security and infrastructure approval. Device submitted GPS is not authoritative location truth.
- This package is **not** an assertion that the entire payments/commercial/dispatch system is production complete. Its source status is untested until later explicit runtime authorization.

## Installation/rollback constraints

Use `Apply-CoW-Core-V3-Source.py --check`, `--apply`, `--verify`, or `--rollback` ONLY against the pinned, unchanged installed V2 checkout. Preserve the extracted V2 and V3 backup/state folders separately. The V3 installer checks all 29 predecessor files including the prior anchored wallet patch, all new hash digests, exact router Git blob identities, no staged/unrelated changes, then atomically replaces source files with backups recorded before the first edit. All old feature switches remain OFF; migrations/files are added but never run. Never manually delete the prior source or reinstall V2.
