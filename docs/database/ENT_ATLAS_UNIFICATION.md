# Ent + Atlas convergence — implementation and release gate

Date: 28 October 2026. Base revision: `1e966a2ce29bc2d1ef3e25500ee474c1ab380853`.

**Target: exactly one developer-facing database paradigm.** PostgreSQL is the
database; Ent schemas define application models; Ent codegen builds typed APIs;
Atlas generates and applies reviewed, versioned SQL migrations. pgx is the
transport/escape hatch, not a second application-modeling system. No Prisma,
runtime auto-DDL or parallel migration tools after cutover.

## What this staging change actually accomplishes

- Expands `ent/schema/` from 6 to **25 application tables** across Auth, RBAC,
  Outbox, Fleet, Customer and Pricing. `schema_migrations` remains migration
  history, not a business entity.
- Checks all 25 entities' column names and PostgreSQL types against the
  historical 001–005 schema **plus the proposed bridge 006**. The check runs
  with both LF and CRLF inputs.
- Produces/generated typed Ent clients for the complete table inventory.
- Proposes migration `006_ent_identity_bridge.sql`, adding surrogate BIGSERIAL
  IDs for seven legacy composite/bytea primary keys while retaining the
  original natural uniqueness and the four composite principal FKs.
- Leaves original migration files 001–005 untouched. Does not touch data or
  run schema changes on VPS2.

**Stage 2 status: pre-cutover; not authorized for VPS2 deployment.** The
follow-up convergence work adds historical CHECK constraints, ordinary FK
edges with delete policies, named/partial/descending indexes, parity tests,
an immutable Atlas 001–006 baseline with `atlas.sum`, and `atlas.hcl` for
isolated development. Passing local parity **does not mean an Atlas diff is
safe**: PostgreSQL collation/default/index/constraint naming and the four
composite principal FKs still require real PostgreSQL verification. Existing
pgx services have **not** been rewritten to Ent. The operational junior
guide is **[SINGLE_PARADIGM.md](SINGLE_PARADIGM.md)**.

## Why migration 006 is needed

The existing schema includes byte-array token digests as primary keys and
non-edge tables with composite primary keys. Ent v0.14.6 does not provide a
general-purpose composite-ID entity model (the `field.ID` annotation documents
edge schemas). The bridge gives these entities conventional Ent IDs without
changing the externally relied-upon keys:

| Table | Natural key remains enforced |
| --- | --- |
| `principals` | `(account_id, plane)`; dependent composite FKs rebuilt |
| `auth_rate_limits` | `(bucket, key_digest)` |
| `consumed_refresh_tokens` | `(digest)` |
| `auth_action_tokens` | `(digest)` |
| `admin_role_assignments` | `(account_id, role_key)` |
| `admin_capability_overrides` | `(account_id, capability)` |
| `mutation_idempotency_receipts` | `(actor_id, request_key)` |

All seven receive a BIGSERIAL PK; existing INSERTs that specify original
columns can continue working. Do not interpret this as authorization to alter
VPS2. The bridge temporarily removes and restores four FKs inside the existing
transactional migration runner. A real PostgreSQL upgrade test with preserved
accounts, role assignments, sessions and idempotency records is required
before any deployment. PostgreSQL-compatible engine tests alone are not enough.

## Mandatory work to complete the **single** paradigm

1. **Complete Ent schema fidelity:** ordinary FK edges, required/optional
   semantics, uniqueness, partial indexes, PostgreSQL CHECKs, decimal/date/
   text[] types and deletion policies now have source-level regression checks.
   **Still gated:** verify the model against a real database's full index,
   collation, default, named constraint and composite-FK catalog; preserve
   any feature Ent cannot represent in reviewed Atlas SQL and regression tests.
   No weaker DB behavior is accepted merely to fit ORM generation.
2. **Verify migration 006 on a disposable real PostgreSQL clone** restored
   from the verified pre-Foundation-3 backup and migrated to 001–005. Confirm
   every existing row/key/FK survives; exercise Auth, RBAC, Fleet, Customer,
   Pricing and concurrency. Confirm PostgreSQL version and lock behavior.
3. **Validate one Atlas versioned migration directory:** the audited 001–006
   source snapshot, checksum validation and single `atlas.hcl` are now staged.
   Still demonstrate clean-database replay and existing-database baseline
   adoption, including `schema_migrations` preservation, against real PG18.
   Reject all unapproved DROP/constraint weakening proposals; never run
   `atlas schema apply` against VPS2.
4. **Retire the old runner and duplicate migration folders** only after Atlas
   baseline/status/verification agrees in both fresh and restored databases.
   Leave 001–006's original checksums and history intact in Git and in the
   database. Future changes have a single reviewed versioned Atlas migration
   path and a single apply mechanism. No automatic migration at API startup.
5. **Move existing repositories/services to Ent** in a reviewable sequence,
   preserving transaction atomicity across auth, outbox, audit and idempotency.
   A pgx transport does not imply two independent transaction commits are safe.
6. **Cut over VPS2 separately:** exact commit preflight, backup and verified
   restore, explicit database-target authorization, migration verification,
   independent binary activation and protected FE/API acceptance. Binary
   rollback never silently reverses database migration.

## Junior-team instruction

**Until cutover, no one should build on the new 001–003 Ent models as if
VPS2 tables already had surrogate IDs.** Customer 004/005 code retains its
previously verified behavior. New schema work is centralized in Ent, but no
developer may create an incompatible `006` or fork legacy/Atlas histories.

After cutover, the only daily workflow will be:

1. Update `ent/schema/*.go` and domain invariants.
2. Run `go generate ./ent` and review generated changes.
3. Generate a versioned Atlas migration from the Ent target using an isolated
   development PostgreSQL instance. Review the SQL for data loss, locks,
   constraints, indexes, drift and special migrations.
4. Test clean replay plus upgrading an existing disposable database.
5. Merge only after checks and a human review. Apply to VPS2 only through a
   separately approved migration operation.

Reference documentation:

- Ent versioned migrations: https://entgo.io/docs/versioned/intro/
- Planning an Ent/Atlas migration: https://entgo.io/docs/versioned/new-migration/
- Atlas baseline/import: https://atlasgo.io/versioned/import
- Atlas notes on PostgreSQL custom indexes and Composite Schema tiering:
  https://entgo.io/docs/migration/functional-indexes/

## Local proof for this change

Source-only tests: `go generate ./ent`, `go test -p 1 -count=1 ./...`,
`go vet ./...`, `git diff --check`, and `go mod verify`. The migration bridge
was exercised against a disposable PostgreSQL-compatible engine with 001–005
applied and existing principal/customer/Admin role rows preserved. These are
preliminary checks; the real PostgreSQL/Atlas upgrade and FE acceptance gates
above remain mandatory.
