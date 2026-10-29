# One database development paradigm — Ent + Atlas

**Decision (2026-10-28):** All new application database modeling goes through
**Ent**; all forward DDL is planned, reviewed, versioned and executed through
**Atlas** after cutover. PostgreSQL is the database. This is deliberately
similar to Prisma's schema/client/migration workflow, implemented natively in
Go. SQL remains the execution language and the immutable audit record; having
SQL migration files does **not** mean we have a second modeling paradigm.

**Release state:** This repository change stages the convergence tools and
model fidelity. It is **not** permission to run migration 006, baseline Atlas,
or replace the running VPS2 migrator. The real-PostgreSQL cutover, legacy
runner retirement, and domain-service Ent adoption are separate release gates
and are not claimed complete by passing local compilation.

## Where the junior actually works

| What changes | Canonical edit location | Generated/output location | Never do |
| --- | --- | --- | --- |
| Table, column, data type, validation, relationship | `ent/schema/*.go`; shared historical constraints in `ent/schema/sql_integrity.go` | `ent/` via `go generate ./ent` | Edit generated client or `ent/migrate/schema.go` by hand |
| New database version | Ent target first, then a reviewed **new** SQL migration via Atlas | `ent/migrate/migrations/<timestamp>_<name>.sql`, `atlas.sum` | Rewrite old SQL or run Ent's `Schema.Create()` |
| Business invariants that Ent cannot represent fully | Explicitly reviewed SQL for the new Atlas migration and a PostgreSQL invariant test | Review and tests in Git | Omit composite FKs, collations, CHECKs or partial index semantics |
| Business operations | `internal/*` service code; prefer typed Ent queries | Compiled Go server | Make an uncoordinated `pgx` transaction and Ent transaction that should be atomic |
| DB connection | `internal/database/ent.go` shared `pgxpool` adapter | One pool per service | Create an independent ORM connection pool or hide DDL in server startup |
| Existing schema history | Read-only `migrations/001..006` and embedded copies | `cmd/atlas-baseline` snapshots 001..006 into **one** Atlas baseline | Change applied migration bytes or start `migrations/007_*.sql` in old runner |
| Proposed CMS/HAL | `docs/database/reference/sql` for review only | Future authorized migration packs | Treat reference files as live DDL; merge HAL into CMS database |

## Migration authority at each release state

1. **Today, before cutover:** VPS2 has immutable migration history 001–005;
   its running API uses the verified release. `006_ent_identity_bridge.sql` is
   proposed, not applied. The old runner exists to handle those historical
   releases. New forward migrations must wait until the Atlas cutover is
   approved; do not fork two live migration histories.
2. **On a restored, isolated PostgreSQL database:** apply 006 with the existing
   verified runner, assert historical rows and constraints, then adopt Atlas at
   baseline version `20261028000100`. The Atlas baseline contains *exactly*
   historical 001–006 SQL and reproduces `schema_migrations` on fresh replay;
   an existing DB **skips** re-execution using Atlas's explicit baseline mode.
   Atlas tracks subsequent versions in `atlas_schema_revisions`.
3. **After two independent clean trials (fresh DB + restored DB), approved
   Atlas diff review, and a protected VPS2 release:** Atlas becomes the **only
   forward migration writer and executor**. Freeze and remove the duplicate
   legacy migration path from active execution; retain old files and checksums
   in Git as historical evidence. No startup auto-migrate.

## The recurring development workflow (after the cutover)

1. From an up-to-date branch, change the relevant `ent/schema/*.go` file.
   Relationship fields, validation and relevant DB-level indexes/CHECKs must
   be designed together. If a PostgreSQL requirement cannot be modeled by Ent,
   state that fact and include an explicitly reviewed additive SQL delta plus
   an invariant test. **Never delete a constraint merely to make a diff pass.**
2. Regenerate with `go generate ./ent` and review the generated changes. Typed
   queries are the intended developer API. Never edit generated Go directly.
3. Provision a **disposable**, empty PostgreSQL database for Atlas replay and
   a second disposable target database for integration. Infrastructure must
   be provided by the operator; project scripts will never start containers or
   services automatically. Database URLs belong in environment variables,
   never committed files.
4. Run `atlas migrate validate --env local` and
   `atlas migrate diff <meaningful_name> --env local` from repository root,
   with the Atlas CLI installed and `EVCOWBBE_ATLAS_DEV_URL` pointing only at
   the disposable replay database. The schema source, migration directory and
   excluded historical ledger are fixed in `atlas.hcl`. **No target URL is
   configured**: later apply commands must explicitly specify `--url` after
   separate, independently verified database-target authorization.
5. Review the generated SQL line-by-line. Automatic proposals to drop or
   rebuild columns, tables, uniqueness, CHECKs, FKs, indexes or collations
   require explicit contract analysis and operator approval; `atlas.sum` must
   match the accepted history. There is no blind auto-accept of diff output.
6. Demonstrate an **empty-DB replay** and **upgrade of a restored, populated
   database**, including migration ledger integrity, FKs, index predicates,
   account/session/RBAC protection, domain workflows, transaction concurrency,
   data retention and idempotency. Negative tests matter as much as CRUD.
7. Merge a fully tested, reviewable change. Deploy with the existing protected
   preflight, independently verified backup and explicit target authorization.
   An application binary rollback does not undo a database migration.

### Prisma mental model

| Prisma | Evcowbbe (Go) |
| --- | --- |
| `schema.prisma` | Hand-edited `ent/schema/*.go` |
| `prisma generate` | `go generate ./ent` |
| `prisma migrate dev` | `atlas migrate diff` on an **isolated** PostgreSQL dev DB; review migration SQL |
| Prisma typed client | Generated `ent.Client`, queries, mutations, edges, transactions |
| `prisma migrate deploy` | Protected Atlas versioned apply **only after release authorization** |
| `prisma db push` | **No counterpart allowed**: no direct schema push to VPS2 |

## Historical fidelity and known limits

- There are 25 modeled application tables, excluding `schema_migrations`.
- `ent/schema/sql_integrity.go` models historical CHECKs, named/partial/
  descending indexes, and ordinary foreign keys with explicit `ON DELETE`.
  The contract tests parse the immutable SQL and compare exact expressions,
  index columns, predicates and delete actions to Ent's generated metadata.
- Four composite `(account_id,plane)` FKs to `principals(account_id,plane)`
  cannot be one ordinary Ent edge. They remain enforced in PostgreSQL and are
  explicitly regression-tested against 006. A typed single-column `account`
  edge is **not** a substitute for principal membership in the right plane.
- Index/constraint naming, PostgreSQL `COLLATE "C"`, expression details,
  historical defaults and live object metadata still require an actual
  PostgreSQL Atlas diff/rehearsal. An in-memory model check is **not** proof of
  database-level equivalence. Review any proposed naming reconciliation.
- **Windows EOL:** a checkout made before `.gitattributes` may still expose
  some historical SQL with CRLF. The Atlas baseline generator and its tests
  normalize **in memory** to canonical LF before comparing immutable SHA-256
  values. They never rewrite 001–006. The two **generated** Atlas artifacts
  (the baseline SQL and `atlas.sum`) may be atomically restored to canonical
  LF only after independently verifying both are otherwise byte-identical
  to the pinned result. A changed SQL token or checksum still fails closed;
  do not manually re-save old migrations to "fix" this.
- Existing pgx-based Auth/RBAC/Fleet/Customer/Pricing services are **not yet
  rewritten**. Use the same pool and transactional boundaries; service
  conversion is a separate, complete, regression-tested work package. Do not
  assume typed Ent entities alone have migrated all runtime call paths.

## Cutover acceptance gates — do not skip any

- [x] Source-only model inventory, SQL column type, CHECK, index and ordinary
  FK parity; 006 composite FK inventory; `atlas.sum`/baseline validation.
- [ ] Real PostgreSQL 18: snapshot restore + 006 upgrade with existing data,
  including migration locks, FK/unique/check/index catalog comparison.
- [ ] Fresh PostgreSQL 18: Atlas baseline full replay yields identical 001–006
  schema, history and migration checksums.
- [ ] Restored PostgreSQL 18: Atlas `--baseline` adoption without rerunning
  001–006 or losing history; status reads zero unexpected migrations.
- [ ] Atlas Ent desired-state diff against that baseline inspected and
  reconciled; no unexplained `DROP` or weaker constraint; generated later
  migration tested against populated DB.
- [ ] Domain integration (auth, RBAC, pricing, fleet, customer, mail/outbox,
  concurrency) and negative tests against the same real PostgreSQL version.
- [ ] Protected migration runner cutover, backup/restore and explicit operator
  signoff; VPS2 binary and database verified separately after deployment.
- [ ] Existing service DB paths progressively moved to Ent with explicit
  transaction preservation; no parallel forward migration writers remain.

Local verification: `go run ./cmd/atlas-baseline`, `go generate ./ent`,
`go test -p 1 -count=1 ./...`, `go vet ./...`, `go build ./cmd/server`,
and `go mod verify`. The baseline generator refuses to alter already
reviewed SQL or hash history. It is **one-time bootstrap tooling**, not a
replacement for planning new Atlas versions.

Work Package 3's disposable PostgreSQL 18 upgrade/replay/adoption instructions,
catalog and row-fingerprint verifier, and explicit remaining acceptance gates:
**[WORK_PACKAGE_3_REHEARSAL.md](WORK_PACKAGE_3_REHEARSAL.md)**. The presence
of this harness does not itself check off any live PostgreSQL acceptance gate.

Official references: [Ent versioned migrations](https://entgo.io/docs/versioned/intro/),
[Ent planning](https://entgo.io/docs/versioned/new-migration/),
[Atlas existing-schema baseline](https://atlasgo.io/versioned/import),
[Atlas migration diff](https://atlasgo.io/versioned/diff).
