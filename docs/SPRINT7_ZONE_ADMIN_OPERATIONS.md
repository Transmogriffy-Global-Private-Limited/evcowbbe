# Sprint 7: Admin Zone and commercial configuration (development)

## State / authority

- **Django** (`anubhab-work`) is the application runtime. All migrations are authored in `cow/models.py` and `cow/migrations/`.
- **Sprint 7 migration:** `0009_zone_admin_operations`. It follows `0008_zone_commercial_registry` and adds a database-enforced single-published-revision rule plus a composite Zone tariff policy guard. `0008` remains immutable.
- Generic `PricingPolicy` remains the only published tariff timing/identity ledger. Zone-specific policies are represented by `scope.kind=ZONE_ROAMING` or `ZONE_ON_SITE_QR`, `rules=[]`, and exactly one immutable `ZoneTariffPayload` whose full plan is validated before publication. Other pricing rules still use the existing Go-parity pricing engine unchanged.
- `ZoneGSTAssignment` stores **operator-recorded** channel-specific legal decisions, not an inference from GPS. Recording a reference is not independent legal approval. Finance/commercial review is required before entering actual statutory rates.
- No customer quote acceptance, job dispatch, wallet posting, payment, charger command or physical HAL operation is activated by these endpoints.
- VPS2 was decommissioned; old `docs/VPS2_CURRENT_OPERATIONS.md` and operator scripts remain historical only. Git commits do not deploy anything.

## Admin API

All endpoints are under `/api/v1/admin`, authenticated using the existing Admin principal plane. All writes are JSON with `Idempotency-Key` (16-128 visible characters); tariff lifecycle mutations also require `If-Match: "<state_version>"`. All writes use transactions, server-side RBAC refresh/locks, audit events and PostgreSQL constraints. Existing URL/identity/Go-equivalence contracts remain unchanged.

| Method and path | Result | Required permission |
| --- | --- | --- |
| `GET /zones` | List Zone revisions | `pricing.read` |
| `POST /zones` | New draft Zone; revision is next consecutive version | `pricing.manage` |
| `GET /zones/{zone_id}` | Read Zone revision | `pricing.read` |
| `POST /zones/{zone_id}/publish` | Draft -> published; one published revision per key | `pricing.manage` |
| `POST /zones/{zone_id}/retire` | Published -> retired, after assignments, tax bindings and published tariffs are closed/withdrawn | `pricing.manage` |
| `GET /zones/{zone_id}/assignments` | List Zone duty/charger assignments | `pricing.read` |
| `POST /zones/assignments/duty` | Assign duty to published Zone in a valid interval | `dispatch.manage` |
| `POST /zones/assignments/charger` | Assign charger to published Zone in a valid interval | `fleet.assign` |
| `POST /zones/assignments/{kind}/{id}/close` | Close open assignment at current server time | `dispatch.manage` or `fleet.assign` |
| `GET /zones/{zone_id}/gst` | List tax-binding revisions | `pricing.read` |
| `POST /zones/gst` | Record draft tax-binding revision with legal refs | `pricing.manage` and `invoices.manage` |
| `POST /zones/gst/{id}/approve` | Record an authenticated operator approval and audit reference | `pricing.manage` and `invoices.manage` |
| `POST /zones/gst/{id}/close` | Close an approved open GST window at the database statement timestamp | `pricing.manage` and `invoices.manage` |
| `GET /zones/{zone_id}/tariffs` | List composite tariffs | `pricing.read` |
| `POST /zones/tariffs` | Draft composite tariff; same policy ledger, immutable payload | `pricing.manage` |
| `GET /zones/tariffs/{id}` | Read composite tariff | `pricing.read` |
| `POST /zones/tariffs/{id}/publish` | Publish after Zone, GST, payload and topology checks | `pricing.manage` |
| `POST /zones/tariffs/{id}/withdraw` | Withdraw without destroying history | `pricing.manage` |
| `POST /zones/preview` | Simulation of effective persisted tariff and GST | `pricing.read` |

All mutations require a reviewed human-readable `reason` (8-500 chars); assignment/GST mutations also record `authorization_ref`. Use generated `/openapi.yaml` for exact request/response schemas. Responses contain the standard `{ "data": ... }` envelope.

## Correct workflow

1. Create a Zone draft with `zone_key`, `version=1`, location, radius, restriction mode, effective dates; publish it.
2. Assign a charger or driver duty, with `starts_at`, optional `ends_at`, exact resource UUID and authorization reference. The DB rejects time overlaps, including concurrent writes.
3. Record channel-specific GST (seller, legal decision, exact signed-off tax profile, effective dates), then record operator approval. The DB blocks overlapping approved GST decisions.
4. Submit a composite tariff plan with `policy_id`, `policy_version`, `zone_id=zone_key`, `channel`, `currency=INR`, and `profiles.INSIDE` (optional `OUTSIDE` only where allowed). Use a fresh `policy_id` or the next sequential policy version. The plan is stored immutably in `ZoneTariffPayload`.
5. Publish the tariff using the current policy `ETag` (`"1"` for fresh draft). Both the service and PostgreSQL guard reject a missing payload; the service additionally checks approved GST and full tax class coverage.
6. Simulate from a stored duty/charger assignment with Admin-entered WGS84 coordinates. This is **never** a trusted charging quote or payment instruction; the result explicitly reports `simulation_only`, `position_trusted=false`, and `may_charge_customer=false`.

## Migration and verification

The Aiven **development** database was already reset and migrated through 0008. On a machine where the existing `DATABASE_URL`, `PASSWORD_PEPPER`, `OTP_HMAC_PEPPER`, `PUBLIC_BASE_URL`, `HTTP_LISTEN_ADDR` and `SMTP_TLS_MODE` are configured, `manage.py migrate cow 0009_zone_admin_operations --noinput` is the next *forward* migration. Do **not** reset it again unless you deliberately choose to destroy all dev data.

Use `python scripts/migrate_aiven_dev_0009.py` from the repository root to apply migration 0009 to the exact previously verified Aiven dev target with identity checks. No destructive reset is performed. `./scripts/db.ps1 check` must show `MODEL_MIGRATION_PARITY=PASS`. The marker-protected PostgreSQL acceptance runner now understands 0009 for fresh and existing approved test databases; the legacy `--upgrade-0007-to-0008` mode remains scoped to 0008. A locally installed PostgreSQL 17 can also run `scripts/db_isolated_pg.ps1` to build an isolated throwaway cluster, migrate through 0009, run the whole `tests/integration` suite and replay migration history.

Acceptance must cover:

- Admin writes and read authorization, revocation, idempotency, audit, state changes and status errors.
- PostgreSQL concurrent assignment overlap protection, dual Zone publication conflict, GST approval overlap, and missing tariff payload SQL rejection.
- Exactly one selected published tariff and approved tax binding for a trusted effective context; proper minute and money rounding through the existing pricing engine.
- Simulation cannot become a stored customer acceptance, ledger debit or HAL command, regardless of caller input.
- No regression in ordinary pricing, user/driver module imports, existing OpenAPI endpoints, Django schema parity or old migrations.

## Development limitations

This package does **not** independently authenticate legal tax decisions, verify QR tokens, authenticate live GPS or route-optimization provenance, settle actual money, or execute physical charging. Those require downstream evidence providers and customer/job workflows, not spoofed Admin request fields. Do not present the simulation endpoint as a customer API.

Existing fixture marker policies and isolated-cluster checks must remain in place. Never point the accepted disposable harness at Aiven's `defaultdb` by overriding the marker, and never force a production deployment.
