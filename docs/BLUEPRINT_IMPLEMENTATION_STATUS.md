# Product scope vs implementation: honest coverage

Source of business requirements: bundled `reference/CoW_normative_blueprint_2026-09-24.zip`.
This source package is a **backend foundation** and NOT a finished copy of
the entire blueprint. The table is the gating backlog, not a wish list.

| Business domain | Source implemented now | Runtime/DB tested | Remaining to complete |
| --- | --- | --- | --- |
| Identity + opaque sessions | yes | no | email/OTP/provider, rate-limit/refresh DB acceptance, FE parity |
| Admin RBAC | WP3.1C source candidate | PostgreSQL pending | Annex 44 grant/revoke, override, audit and final-admin protection; integration pending |
| Customer profiles/EVs/addresses | WP3.1D source candidate | PostgreSQL pending | profile editing, 12 customer endpoints, immutable request snapshots and ownership/version/default DB guards; integration pending |
| Fleet assets + pairings | vehicle, charger, connector and pairing source parity implemented | WP3.1A PostgreSQL accepted; WP3.1B PostgreSQL accepted 2026-10-30 | Nine vehicle operations passed 15/15 isolated PostgreSQL tests on VPS2 (2026-10-30). Fifteen charger/connector/pairing operations and corrected additive 0004 are source-tested; The reusable isolated database passed 0003-to-0004 historical and 0001-to-0004 fresh acceptance, followed by 18 passing integration tests. |
| Driver recruitment/KYC | principal/profile model | no | entire approval workflow, documents, suspension, audits |
| Scheduling/duty/geofence | duty claim model | no | full eligibility, geolocation, working schedules, live availability |
| Dispatch/assignment/reassignment | state model only | no | eligibility engine, offers, race acceptance, reassignment, ETA |
| Service request/quote/job | partial functional API | no | geo serviceability, all transitions, cancellation/expiration |
| Tariffs/pricing | WP3.1C Admin tariff revisions source candidate | PostgreSQL pending | rule authoring and publication; commercial evaluation, tax and invoices pending |
| Charging/Go HAL | signed v1 contract/ingress/command source | pure simulator only | Go HAL, external acceptance, reconciliation and complete runtime orchestration |
| Payments/refunds | none | no | Razorpay provider, idempotent capture/refund reconciliation |
| GST/invoices | none | no | snapshot, renderer, immutable artifacts, delivery/recovery |
| Compensation | none | no | rule engine, versioning, line ledger, adjustments/aggregates |
| Ratings/reviews | none | no | all three surfaces and authorization |
| Support/incidents | none | no | channel isolation, ownership, audit, retention |
| Notifications/SSE | credential mail outbox partial | no | actual SMTP end-to-end, durable SSE, user/driver/admin projections |
| Admin metrics/audits | base audit + few routes | no | complete actions, analytics, exports |
| FE backwards compatibility | static comparison CLI | no | actual old-vs-new OpenAPI and behavior audit |
| Production deployment | template only | no | capacity, secrets, data cutover, rollback, public acceptance |

Priority after source-quality acceptance: complete Go FE parity *before*
repointing FE users, then driver/fleet/duty/dispatch, then commercial/finance,
and Go HAL conformance/physical checks. These are all in scope for the actual
finished product; they are not treated as optional merely because current
source has a smaller implementation.
