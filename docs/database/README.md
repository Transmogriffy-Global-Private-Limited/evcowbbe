# Charger on Wheels database model: authoritative SQL + typed Ent access

Status: **local source integration candidate** (2026-10-28). This is not a VPS
schema change or deployment. The currently deployed PostgreSQL 18 database has
applied 001-003; local branches have prospective 004 customer and 005 Admin
pricing. The existing migration checksums are immutable.

## What is canonical (and what is not)

| Layer | Source | Purpose |
| --- | --- | --- |
| Applied schema | `migrations/001_*.sql` to `003_*.sql` | Immutable historical PostgreSQL changes |
| Next executable deltas | `migrations/004_customer_foundation.sql`, then `005_admin_pricing_rules.sql` | Reviewed, ordered, additive SQL; both copied byte-identically into `internal/database/migrations/` |
| Go entity model | `ent/schema/` | Prisma-like model definitions and relationships for 004 + 005 |
| Generated Go client | `ent/` (`go generate ./ent`) | Typed reads, writes, predicates, and traversal for those six tables |
| Connection adapter | `internal/database/ent.go` | Uses existing `pgxpool.Pool`, no second connection pool and **no DDL** |
| Future CMS and HAL contracts | `docs/database/reference/sql/` | 82 further CMS candidate tables and 14 separate HAL tables; **not migrations** |

**There is not yet a single full-app Ent model for every future table.** Models
for deployed Auth, RBAC and Fleet remain SQL-first and use existing pgx services.
The six new Ent entities are the fully generated **current customer/pricing
slice**, with explicit SQL column/type parity tests. Planned 82 CMS tables and
14 HAL tables are kept as an inspectable relational target, not dishonestly
presented as live tables or released migrations. The HAL has its own database.

## Junior backend: Prisma to Ent translation

| Prisma idiom | Go/Ent equivalent |
| --- | --- |
| `schema.prisma` | Go structs under `ent/schema/` (`Fields`, `Edges`, validators) |
| `prisma generate` | `go generate ./ent` |
| `prisma.customerProfile.findUnique(...)` | `client.CustomerProfile.Query().Where(customerprofile.AccountIDEQ(accountID)).Only(ctx)` |
| `include: { vehicles: true }` | `.WithVehicles()` on `CustomerProfile.Query()` |
| `prisma.customerVehicle.create(...)` | `client.CustomerVehicle.Create().SetCustomerID(id).SetMake(...).SetModel(...).Save(ctx)` |
| Transaction | `tx, err := client.Tx(ctx)` then `tx.Customer...`, `tx.Commit()` or `tx.Rollback()` |
| `prisma migrate` | **Not enabled**. Continue explicitly reviewed numeric SQL files through `cmd/migrate up` |

Use a normal Go dependency on `github.com/Transmogriffy-Global-Private-Limited/evcowbbe/ent`.
Share the existing pool without needing a new `DATABASE_URL`:

```go
models, err := database.OpenEntClient(pool)
if err != nil { return err }
defer models.Close() // does not close the pgxpool

profile, err := models.CustomerProfile.Query().
    Where(customerprofile.AccountIDEQ(accountID)).
    WithVehicles().WithAddresses().Only(ctx)
```

Required imports for that example are `internal/database` and
`ent/customerprofile` (alongside the caller's usual context and DB imports).

### Important model rules

1. `accounts` = authentication identity. `principals` = `(account_id, plane)`;
   `customer_profiles.id` is a **different** UUID. The PostgreSQL composite FK
   enforces that a customer belongs to an `app` principal, not to `driver` or
   `admin`. The Ent model deliberately does not fake a simple FK to `accounts`.
2. The service layer **must** authorize every data access from a validated
   app-plane principal. Ent's generated CRUD API does **not** enforce ownership,
   RBAC, signed-in sessions or row-level security.
3. `customer_vehicles` represent saved customer-owned EVs, never the operating
   fleet's `service_vehicles`. Addresses may be inactive; at most one active
   default address per customer is enforced by a PostgreSQL partial index.
4. Money is exact integer INR paise; energy is integer Wh; times are UTC
   instants; latitude/longitude are exact decimal (not binary float); vehicle
   connectors and audit changed-fields use PostgreSQL **`text[]`**, not JSONB.
5. Reads can use Ent immediately after 004/005 have actually been migrated.
   Business writes that must atomically update audit, idempotency, outbox,
   accepted quotes, jobs or driver dispatch must use **one transaction**.
   A `pgx.Tx` and an `ent.Tx` cannot silently share a transaction; keep such
   workflows in existing pgx services or deliberately adapt one transaction.
6. PostgreSQL CHECKs, FK delete restrictions, composite FK, partial/expression
   indexes, event history and legal/commercial gates remain in the reviewed
   SQL DDL and service logic. Ent is a typed client, **not a substitute for
   those guarantees**. In particular, do not call `models.Schema.Create(ctx)`.
7. Do not edit migrations that have been applied anywhere. Each subsequent
   change needs a new reviewed `006_...sql`, `007_...sql`, etc., a byte-identical
   embedded mirror, new Ent schema definitions, regenerated client and tests.

## Local verification, no running services required

```powershell
go generate ./ent
go test -p 1 -count=1 ./...
go vet ./...
go build ./cmd/server
git diff --check
```

`ent/migrate/contract_test.go` compares all six generated Ent models to the
existing SQL 004/005 column names and PostgreSQL types. `ent/customer_contract_test.go`
checks generated typed queries and PostgreSQL array/decimal scanning. These
are compilation/driver-mock tests, **not** a substitute for PostgreSQL 18
integration, transactional-concurrency, or negative-authorization tests.

For an externally provisioned **disposable** database, run the repository's
supported migration command with the target DB confirmed manually, verify
`schema_migrations` versions and checksums, then run integration tests with
`TEST_DATABASE_URL`. Never point a disposable test at VPS2's application DB.
The migration runner intentionally never starts PostgreSQL, opens a Docker
daemon, or applies changes at server startup. No `atlas migrate apply`, Ent
automatic schema migration or database reset is part of this package.

## The full target schema and delta sequence

The reference SQL in this directory is copied from the 2026-10-28 canonical
database contract package, based on the 2026-09-24 Charger on Wheels blueprint.
CMS `sql/cms/10` through `60` form proposed **future** migration packs; `60`
is conditional on a validated mobile-energy business model. The HAL file
`sql/hal/10` is for a **separate** service/database. These files must never be
placed in `internal/database/migrations` or executed wholesale during normal
application startup. Validate each future change against the complete blueprint,
ownership/state machine, current schema, exact data migration, rollout order,
foreign keys, concurrency, audit/recovery, and rollback before promotion.

Next priorities after 004/005 release: customer requests and immutable quotes;
driver applications, approvals and scheduling; service zones, jobs and dispatch;
HAL command/evidence interfaces; financial settlement/support. Each is a
separate contract-backed delta; no hub-dependent tariff shortcut.
