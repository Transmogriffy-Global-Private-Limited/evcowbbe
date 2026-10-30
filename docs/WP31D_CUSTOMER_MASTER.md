# WP3.1D: Customer master-data parity (source candidate)

Authoritative scope: September 24 normative Charger on Wheels blueprint,
`03_USER_APP_PRESERVED_CONTRACT.md` 10.1–10.4, 31.1–31.3 and 31.13.
Source predecessor: `b29ee648575121081461804ca13331a68d13c686`.

Twelve app operations:
- `PATCH /api/v1/app/profile`.
- `GET|POST /api/v1/app/vehicles`; `GET|PATCH|DELETE /api/v1/app/vehicles/{vehicle_id}`.
- `GET|POST /api/v1/app/addresses`; `GET|PATCH|DELETE /api/v1/app/addresses/{address_id}`;
  `POST /api/v1/app/addresses/{address_id}/make-default`.

`GET /api/v1/app/me` now includes `profile` with `version: 0` when uninitialized.
The profile record is created lazily by the first successful profile PATCH, with
`If-Match: "0"` and `Idempotency-Key`.

All new PATCH/DELETE/make-default operations require `If-Match: "<version>"`
and `Idempotency-Key` (16–128 printable ASCII characters). Missing version/key
returns 428; stale version returns 412; wrong customer returns 404; invalid
business values return 422. Replaying the same key/body returns the previous
response; reusing it with different body fails closed. Existing vehicle/address
create calls remain compatible *without* a key, although providing a key is
recommended for durable retries. Customer writes are audited by actor and
changed field *names*; contact/address field contents are not copied to audit.

Customer charging capabilities are **self-reported and unverified**. A new EV
create must explicitly set `supports_ac` and `supports_dc` booleans and cannot
silently assume AC support. This protects new entries; historical rows are
not falsely certified. Power declarations require the corresponding mode.
A service request freezes a complete snapshot of the relevant customer facts;
later edits/soft deletes never mutate job/request snapshots.

Address create preserves the existing freeform `address`, latitude and
longitude request shape. Additional structured address fields are optional and
are preserved in new snapshots. The first active saved address becomes default
if none exists. Explicit make-default switches atomically under the owner row
lock. Deleting the default leaves no default, rather than silently selecting
an unverified location.

Migration `0006_customer_master_parity` is additive, does not backfill guessed
profile/charger-capability/location facts, and installs DB triggers for app
profile ownership, version increments, non-resurrection and default validity.
The partial unique index `cow_one_active_default_address` from earlier
migrations remains the authoritative uniqueness guard.

## Acceptance

1. Clean source gates: Ruff ignoring E501, 5 source contract tests, full
   unittest suite, Django system check, migration-state drift, and generated
   OpenAPI operations for the 12 customer routes.
2. On isolated, identity-marker-verified VPS2 PostgreSQL: reviewed
   `0005 -> 0006` migration, catalog checks, and full integration suite.
3. No live Go rehosting, no production DB migrations, no cutover.

**Not claimed:** full phone verification, address geocoding trust, certification
of customer EV charging compatibility, tariff billing, or physical charging.
