# Work Package 3: PostgreSQL 18 Ent/Atlas convergence rehearsal

**Status: code prepared; actual PostgreSQL/Atlas execution pending.** This is
not a release, cutover, VPS2 migration, or approval to modify a live database.
The running backend remains at `1e966a2ce29bc2d1ef3e25500ee474c1ab380853`.
The proposed immutable `006_ent_identity_bridge.sql` is not applied to VPS2.

## Purpose and boundaries

Prove one development paradigm against PostgreSQL 18, not just by checking Go
types: (a) existing 001–003 data survives applying 004–006, (b) a clean Atlas
replay of the 001–006 baseline produces the same PostgreSQL DDL as an upgraded
restored database, (c) Atlas can adopt that populated database without executing
historical DDL, and (d) Ent's desired state does not silently weaken/drop
historical constraints. Check Auth/RBAC/Fleet/Pricing/Customer integration.

**Never** delete or modify the protected pre-Foundation-3 backup, original
`evcowbbe_restore_f3_20261028082255_31c7b2e2`, or live `evcowbbe` database.
All named databases below are *new*, independently provisioned, disposable
PostgreSQL 18 test databases, not aliases of the application or previous
restores. No project script provisions, starts, stops, or deletes PostgreSQL.
No Docker, CI/CD, VPS2 rehost, deployment, or SMTP setup is performed here.

## What this package adds

- `cmd/atlas-rehearsal`: a read-only `snapshot` command; fail-closed
  `compare-data`, `compare-schema`; and an **explicitly consented** `legacy-up`
  for exactly `evcowbbe_atlas_*_test` databases. It never receives a live
  DATABASE_URL fallback. It does not print credentials, row bodies, secrets,
  or connection strings.
- Snapshots record SHA-256 of length-framed, sorted canonical JSONB rows. The
  bridge's seven newly added surrogate `id` fields are excluded **only from
  these data fingerprints**; all original row attributes are preserved in
  the comparison. Catalog fingerprints separately inspect IDs, columns,
  collation, defaults, constraints, indexes, sequences, triggers, functions,
  views, policies, enums, extensions, and table properties.
- PostgreSQL 18 and the exact legacy 001–006 SHA-256 ledger are verified.
  After 006 the checker also enforces 25 application tables, seven primary
  and retained unique keys, plus the four original two-column principal-plane
  FKs and their deletion actions. Thus two equally broken schemas cannot
  simply be declared equivalent.
- `atlas.rehearsal.hcl` resolves PostgreSQL URLs **only** from explicit
  operator environment variables, keeping passwords off command arguments.
  Existing `atlas.hcl` remains the developer's schema-authoring configuration.

### Limitations that must be accounted for

The catalog comparator excludes PostgreSQL object ownership, GRANTs, role
membership, and volatile sequence **values**. They are deployment-privilege
and runtime-state concerns and require a separate operator permissions review.
Fingerprint agreement is not an exhaustive semantic equivalence proof for
arbitrary SQL functions or planner behavior. PostgreSQL collation names,
definitions, defaults, index/constraint names and identities **are** included.
An Atlas schema diff on a free license may omit database objects that need
Pro; therefore never treat an empty Atlas diff as sufficient evidence without
the catalog comparison, bridge checks, and domain integration results.

## Operator preflight (before any DB write)

1. Review and obtain authority for *creating/restoring and changing only new
   disposable test databases*. Make a dedicated no-production-access credential
   set. The existing verified backup is a **source**, never a target.
2. Independently provision these three PostgreSQL 18 databases (names must
   match the `evcowbbe_atlas_*_test` boundary):

   - `evcowbbe_atlas_populated_20261029_test`: restore the verified historical
     001–003 backup into this **new** empty DB, with original data.
   - `evcowbbe_atlas_fresh_20261029_test`: start truly empty for Atlas replay.
   - `evcowbbe_atlas_dev_20261029_test`: empty, disposable **Atlas scratch**
     database that Atlas may modify while calculating desired-state diffs.

3. On VPS2, separately verify the source revision, clean tree, backup hash,
   actual database names, roles, host binding, PostgreSQL version, toolchain
   and Atlas executable. Do not rely on a database-name string alone when
   selecting a restore target. Provision with an operator-approved process;
   this repository deliberately provides **no** `createdb`, `pg_restore`,
   `DROP DATABASE`, or global role-management automation.
4. Work from an immutable copy of the verified Work Packages 1–3 candidate,
   not by editing `/srv/evcowbbe/source` under an active deploy. Ensure both
   historical migration directories and Atlas `atlas.sum` match the reviewed
   candidate. Prior to remote deployment this code is uncommitted; install
   only after the separately verified Git review and publication step.
5. Load these environment variables securely (example names only; no values
   belong in the repository, shell history or chat):

   ```text
   EVCOWBBE_ATLAS_POPULATED_URL=postgres://.../evcowbbe_atlas_populated_20261029_test
   EVCOWBBE_ATLAS_FRESH_URL=postgres://.../evcowbbe_atlas_fresh_20261029_test
   EVCOWBBE_ATLAS_DEV_URL=postgres://.../evcowbbe_atlas_dev_20261029_test
   ```

   Never point these variables at the live `evcowbbe` DB or the retained
   isolated restoration. Store proofs in an operator-owned `0700` directory;
   `umask 077` before writing them. Fingerprints are confidential metadata.

## Rehearsal commands (Bash on isolated VPS2 host)

The commands below assume the verified candidate and three isolated databases
have already been provisioned. Stop on the first failure; no automatic
rollback or retry is performed. All commands are from repository root.

```bash
set -euo pipefail
umask 077
proof_dir=$(mktemp -d /tmp/evcowbbe-atlas-proof.XXXXXXXX)
command -v atlas >/dev/null
go version
atlas version
go run ./cmd/atlas-baseline
go test -p 1 -count=1 ./...
go vet ./...
atlas migrate validate --dir file://ent/migrate/migrations
go run ./cmd/atlas-rehearsal check-empty fresh
go run ./cmd/atlas-rehearsal check-empty dev

# Read-only pre-upgrade fingerprint of the populated, 001-003 restoration.
export EVCOWBBE_ATLAS_REHEARSAL_URL="$EVCOWBBE_ATLAS_POPULATED_URL"
go run ./cmd/atlas-rehearsal snapshot > "$proof_dir/01-before.json"

# Explicitly authorize migration of THIS NEW DISPOSABLE TEST DATABASE ONLY.
export EVCOWBBE_ATLAS_REHEARSAL_CONFIRM=MIGRATE_ATLAS_REHEARSAL_TEST_DB_ONLY
go run ./cmd/atlas-rehearsal legacy-up
unset EVCOWBBE_ATLAS_REHEARSAL_CONFIRM

# Verify the legacy data survived 004, 005, 006 (new IDs ignored for data only).
go run ./cmd/atlas-rehearsal snapshot > "$proof_dir/02-upgraded.json"
go run ./cmd/atlas-rehearsal compare-data "$proof_dir/01-before.json" "$proof_dir/02-upgraded.json"

# Atlas baseline: fresh DB executes 001-006, existing DB marks it applied.
atlas migrate apply --config file://atlas.rehearsal.hcl --env rehearsal_fresh
atlas migrate status --config file://atlas.rehearsal.hcl --env rehearsal_fresh

atlas migrate apply --config file://atlas.rehearsal.hcl --env rehearsal_populated --baseline 20261028000100
atlas migrate status --config file://atlas.rehearsal.hcl --env rehearsal_populated

# Verify adopting Atlas added only its revision tracking, not changed app data.
go run ./cmd/atlas-rehearsal snapshot > "$proof_dir/03-adopted.json"
go run ./cmd/atlas-rehearsal compare-data "$proof_dir/02-upgraded.json" "$proof_dir/03-adopted.json"
go run ./cmd/atlas-rehearsal compare-schema "$proof_dir/02-upgraded.json" "$proof_dir/03-adopted.json"

export EVCOWBBE_ATLAS_REHEARSAL_URL="$EVCOWBBE_ATLAS_FRESH_URL"
go run ./cmd/atlas-rehearsal snapshot > "$proof_dir/04-fresh.json"
go run ./cmd/atlas-rehearsal compare-schema "$proof_dir/03-adopted.json" "$proof_dir/04-fresh.json"

# Diff is *read-only* against the populated schema; Atlas may use only the
# separate disposable dev database for scratch calculations.
atlas schema diff --config file://atlas.rehearsal.hcl --env rehearsal_diff \
  --from env://url --to env://src \
  --exclude schema_migrations --exclude atlas_schema_revisions \
  > "$proof_dir/05-ent-desired-state-diff.sql"

# The actual diff MUST be reviewed; do not pipe it to psql or migrate apply.
# A nonempty SQL diff is a reconciliation task, never automatic permission.
printf 'REHEARSAL_EVIDENCE_DIR=%s\n' "$proof_dir"
```

**Important:** `atlas migrate apply` above has no `--url` on the command line;
the selected HCL environment resolves a different, explicitly provided test
URL. Independently verify the environment mapping first. Atlas's `--baseline`
flag marks the existing 001–006 schema as applied rather than executing its
SQL again; it is *not* the correct mode for the fresh database.

### Integration and negative-path verification

After the parity snapshots and only on the **disposable populated test DB**,
run the integration suites against that exact URL. Tests perform writes; they
must never receive the actual application DATABASE_URL. Some tests may retain
test-only artifacts; do not rely on them to preserve the pre-test data snapshot.

```bash
export TEST_DATABASE_URL="$EVCOWBBE_ATLAS_POPULATED_URL"
go test -p 1 -count=1 -run '^TestIntegration' ./internal/auth ./internal/fleet ./internal/pricing ./internal/database ./internal/api -v
unset TEST_DATABASE_URL
```

Review at least: account registration and principal-plane denial, role/capability
enforcement, refresh-token replay protection, outbox delivery/idempotency,
pricing temporal overlap and concurrency, fleet transaction/idempotency,
customer typed Ent CRUD, and missing/invalid FK/check/unique writes. If a
test suite doesn't explicitly cover a listed invariant, record that as a
remaining **failed acceptance gate**, not a pass by association.

## Evidence and acceptance

Required actual observed results:

- `ATLAS_REHEARSAL_LEGACY_001_006=PASS`
- `ATLAS_REHEARSAL_DATA=PASS` for both upgrade preservation **and** adoption.
- `ATLAS_REHEARSAL_SCHEMA=PASS` for adoption and independent fresh replay.
- The post-006 inspector enforces all 25 tables, seven new BIGINT identities,
  seven natural unique constraints, and four principal-plane composite FKs.
- Fresh Atlas migration status shows baseline applied, no pending SQL;
  populated adoption status also shows baseline applied, no pending SQL.
- **Zero unexplained SQL operations** in `05-ent-desired-state-diff.sql`.
  A diff that drops/relaxes columns, constraints, FKs or indexes is **not**
  permission to run anything. If naming, collation, defaults, expression
  index semantics or another difference occurs, reconcile Ent annotations
  and/or reviewed forward SQL first, then repeat the full rehearsal.
- PostgreSQL privilege/role/grant review, app-domain tests, and backup/restore
  controls are separately signed off.

If all tests pass, **do not automatically apply 006 to VPS2, switch migration
runners, commit/push, or deploy**. Those are later independently authorized
operations. A binary rollback never reverses database DDL. Preserve the
proof directory for review, protect it as confidential, and share only
sanitized PASS/FAIL summaries and any reviewed SQL diff. The original backup
and previously preserved isolated restore remain untouched.

## Troubleshooting and recoverability

- Missing Atlas binary/ent source support: stop and install/verify an approved
  pinned toolchain outside this work package. Never substitute SQL push.
- Snapshot rejects a target: check that it is an independently created DB
  named `evcowbbe_atlas_*_test` with explicit credentials; do not weaken
  safeguards for convenience.
- Restore or migration error: preserve the failed **test** database and logs
  for diagnosis. Re-provision a different disposable test database when
  necessary; do not reuse the protected original restore as a scratch target.
- Data hash mismatch: compare table name and count without dumping sensitive
  rows; investigate changed pre-existing values, rollback of partial DDL, or
  unexpected triggers before trying again.
- Fresh vs restored DDL mismatch: identify the first catalog entry, especially
  constraint/index names, defaults, collations and composite FKs. Do not
  hand-edit immutable 001–006 or relax a CHECK to silence a diff.
- Atlas status incomplete/uncertain: inspect `atlas_schema_revisions` in the
  disposable database; keep the original `schema_migrations` history intact.
  Never edit either ledger manually to force status PASS.

Atlas primary documentation: [existing database baseline](https://atlasgo.io/versioned/import),
[versioned apply](https://atlasgo.io/versioned/apply),
[schema diff](https://atlasgo.io/declarative/diff),
[CLI reference](https://atlasgo.io/cli-reference).
