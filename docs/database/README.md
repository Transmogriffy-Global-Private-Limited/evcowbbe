# Charger on Wheels database model: Ent + Atlas convergence

Status: **Atlas convergence candidate** (2026-10-28), not a VPS schema change
or deployment. VPS2 already has immutable migrations 001–005 and runs commit
`1e966a2ce29bc2d1ef3e25500ee474c1ab380853`. Proposed migration 006 and
Atlas adoption have **not** run on VPS2. Start with
**[SINGLE_PARADIGM.md](SINGLE_PARADIGM.md)** for the one-authority developer
workflow and the mandatory PostgreSQL cutover gates.

## What is canonical (and what is not)

| Layer | Source | Purpose |
| --- | --- | --- |
| Applied schema | `migrations/001_*.sql` through `005_*.sql` | Historical, immutable PostgreSQL changes already on VPS2 |
| Transitional key migration | `migrations/006_ent_identity_bridge.sql` and byte-identical embedded copy | Proposed bridge for Ent-compatible primary keys; **not deployed** |
| Go entity model | `ent/schema/` | Prisma-like definitions for all **25 current application tables**, including historical constraints/relations |
| Generated Go client | `ent/` (`go generate ./ent`) | Typed operations across those 25 models |
| Single forward migration path **after cutover** | `atlas.hcl`, `ent/migrate/migrations/`, `atlas.sum` | Versioned Atlas SQL from the Ent target; baseline generated from immutable 001–006 |
| Connection adapter | `internal/database/ent.go` | Uses existing `pgxpool.Pool`, no second connection pool and **no DDL** |
| Future CMS and HAL contracts | `docs/database/reference/sql/` | 82 further CMS candidate tables and 14 separate HAL tables; **not migrations** |

Current application tables are represented in Ent, but existing Auth, RBAC and
Fleet services still execute proven pgx business logic. Future 82 CMS and 14
HAL reference tables are **not** implemented/live Ent models. The HAL has its
own database. Do not equate "all current table models" with "all runtime
services converted" or "Atlas cutover finished".

## Junior backend: Prisma to Ent translation

| Prisma idiom | Go/Ent equivalent |
| --- | --- |
| `schema.prisma` | Go structs under `ent/schema/` (`Fields`, `Edges`, validators) |
| `prisma generate` | `go generate ./ent` |
| `prisma.customerProfile.findUnique(...)` | `client.CustomerProfile.Query().Where(customerprofile.AccountIDEQ(accountID)).Only(ctx)` |
| `include: { vehicles: true }` | `.WithVehicles()` on `CustomerProfile.Query()` |
| `prisma.customerVehicle.create(...)` | `client.CustomerVehicle.Create().SetCustomerID(id).SetMake(...).SetModel(...).Save(ctx)` |
| Transaction | `tx, err := client.Tx(ctx)` then `tx.Customer...`, `tx.Commit()` or `tx.Rollback()` |
| `prisma migrate dev` | After cutover: `atlas migrate diff` against disposable PostgreSQL, with human-reviewed versioned SQL |
| `prisma migrate deploy` | After protected cutover only: separately authorized Atlas apply |

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

1. `accounts` = authentication identity. `principals` has natural key `(account_id, plane)`;
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
6. The Ent model now includes the historical CHECKs, FK delete restrictions,
   partial/descending indexes and named indexes that fit its model. Four
   composite principal FKs remain enforced in PostgreSQL and independently
   tested. Ent code generation does **not** grant authorization or prove
   complete PostgreSQL equivalence. Never call `models.Schema.Create(ctx)`.
7. Existing SQL 001–006 (006 is proposed) is historical and never edited.
   Once Atlas cutover is accepted, future changes must be new versioned Atlas
   migrations and source Ent edits, **not** new legacy `007_*.sql` mirrors.

## Local verification, no running services required

```powershell
go generate ./ent
go test -p 1 -count=1 ./...
go vet ./...
go build ./cmd/server
git diff --check
```

`ent/migrate/contract_test.go` compares all 25 generated Ent tables to the
immutable schema 001–006. Additional tests verify the historical CHECK,
index, foreign-key and Atlas-baseline contracts. `ent/customer_contract_test.go`
checks generated queries and PostgreSQL array/decimal scanning. These are
compilation/driver-mock tests, **not** a substitute for PostgreSQL 18
integration, transactional-concurrency, or negative-authorization tests.

For an externally provisioned **disposable** database, run the repository's
supported migration command with the target DB confirmed manually, verify
`schema_migrations` versions and checksums, then run integration tests with
`TEST_DATABASE_URL`. Never point a disposable test at VPS2's application DB.
The project never starts PostgreSQL or Docker or applies DDL at server startup.
Atlas is configured only for explicitly provisioned databases until formal
cutover; no production apply or database reset is part of this patch.

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
