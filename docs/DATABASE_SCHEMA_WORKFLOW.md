# EVCOWBBE database lifecycle: one ORM schema, Django-generated migrations

**State:** source-development integration contract (2026-11-05). This is NOT approval to run schema changes on VPS2. The authoritative application's PostgreSQL database has not been migrated through 0008 by installing this package.

## Single source and ownership

- `cow/models.py` is the **only declarative Django ORM model registry**. All User, Driver, Admin, identity, charging, fleet, price, and Zone entities are here. No role-specific `models.py`, `migrations/`, independent schema, or Prisma layer is permitted.
- `cow/migrations/` is the **append-only, generated-and-reviewed change ledger**. Django reconstructs historical states from this ledger, but it is not the business-model authoring surface.
- `cow/services/` owns DB transactions and business invariants; `cow/domain/` owns pure business rules; `cow/common/` owns shared API types/errors. `cow/api.py` composes HTTP routers; none of these may create or update tables at application startup.
- Only the core owner may change the model schema or generate migrations; User/Driver/Admin contributors request changes through reviewed contracts and tests.

**Core rule:** edit `cow/models.py` first, run `makemigrations`, inspect the resulting migration, then apply it only to an explicitly approved target. Do **not** hand-edit previously applied migrations or edit `django_migrations` to simulate acceptance. A new migration file is not proof that live schema matches it.

## Requirements

Python 3.12-3.14, the repository `.venv` with `pip install -e '.[dev]'`, and the `anubhab-work` branch for generation. No database is needed for `status` (without `--test-db`), `check`, or `generate`. They set an offline loopback database target in a child process to avoid accidental live access. No network PostgreSQL service is required for those operations.

PowerShell 7+ (from repo root):

```powershell
.\scripts\db.ps1 status
.\scripts\db.ps1 check
# After modifying cow/models.py:
.\scripts\db.ps1 generate --name add_customer_charging_history
.\scripts\db.ps1 check
# Review what Django generated BEFORE applying:
git diff -- cow/models.py cow/migrations/
```

`generate` calls **native Django** `manage.py makemigrations cow --noinput --name <label>`, not a custom migration generator. `check` invokes `manage.py makemigrations cow --check --dry-run --noinput` and fails on model/ledger divergence. Generated `0009_...py` must be reviewed, tested, and committed. Future migration versions require extending the verified acceptance catalog and test workflow before application.

### Unexpressible DB-side protections

`makemigrations` autodetects Django model/field, constraint and index changes, but it does not infer arbitrary custom SQL triggers, functions or RLS policies. `0008` contains **five database triggers** backed by **four PL/pgSQL functions**, installed via `migrations.RunSQL(FORWARD, reverse_sql=REVERSE)`. Retain these DB protections. For similar new protections, author a new reviewed migration containing explicit reversible `RunSQL` or `RunPython` (historical `apps.get_model()` only), with lock/concurrency/rollback tests. Never silently convert a PostgreSQL invariant into only Python-side validation.

## Existing and proposed migration state

- Current reviewed migration files: `0001` through `0008_zone_commercial_registry`.
- The schema consolidation into `cow/models.py` is **source-only**: it must NOT generate `0009`; validate using `makemigrations --check --dry-run`.
- `scripts/run_pg_tests.py` can verify fresh `0001..0008`, previously reviewed upgrade stages, `--upgrade-0007-to-0008`, and existing marker-verified `0008`; its catalog checks include new Zone tables, triggers and functions.
- `scripts/reset_pg_acceptance_schema.sql` remains a **separate operator-only destructive reset**, now able to recognize a known `0008` ledger. It has strong identity, role, marker, dependency and table-allowlist checks. Do not invoke it from the new CLI, do not substitute Django `flush` for schema reversal, and do not run it against the authoritative database.

## Approved disposable PostgreSQL only

All `plan`, `up`, `down`, `verify`, `rehearse` and `status --test-db` actions use the existing `config/pg_test_database.py` marker-based identity check. Required environment settings are `TEST_DATABASE_URL`, a **different** `DATABASE_URL` identifying the actual application target, `COW_PG_TEST_AUTHORIZATION=RUN_COW_POSTGRES_ACCEPTANCE_ONLY`, `COW_PG_TEST_DATABASE_IDENTITY`, and `COW_PG_TEST_ROLE`. The allowed disposable database name is **exactly** `cow_wp31a_20261030_test`. The runner verifies the connection's actual database, role, marker row, server port, ownership, permissions, schemas and allowed migration state before mutation.

Do not paste production credentials in chat, logs or Git. Do not use the shared VPS2 application's DB as a substitute for a disposable acceptance DB. These commands neither create nor reset a PostgreSQL database.

```powershell
.\scripts\db.ps1 status --test-db
.\scripts\db.ps1 plan
.\scripts\db.ps1 verify
# Only after a marker-verified 0007 schema with empty cow_ tables:
.\scripts\db.ps1 up --to 0008_zone_commercial_registry
# Reversal is intentionally explicit, and only on the empty marked test database:
.\scripts\db.ps1 down --to 0007_rbac_pricing_consequential_parity --ack-data-loss
# On the empty marked test database at 0008: reverse, verify, reapply, verify:
.\scripts\db.ps1 rehearse --ack-data-loss
```

The `up/down/rehearse` CLI is **review-version bounded** to `0007 <-> 0008`. New migrations cannot be applied merely because they were generated; the acceptance code and allowlisted database catalog must be updated. `--ack-data-loss` does not override the test-DB identity guard or the empty-table protection. It is not production rollback approval.

The full test runner can be invoked through the CLI, after provisioning the approved test database and creating/inspecting its marker outside the application:

```powershell
.\scripts\db.ps1 test fresh
.\scripts\db.ps1 test upgrade-0007-to-0008
.\scripts\db.ps1 test existing
```

These modes are mutually alternative **starting states** of the database. Do not run all three sequentially against the same non-reset database. Fresh requires an empty schema, upgrade requires the reviewed `0007` state, existing requires the reviewed `0008` state; no mode resets tables.

## Acceptance gates before a merge

1. Cleanly parse all Python sources and migration files; new schema source has only `cow/models.py`.
2. `db.ps1 check` proves Django model-to-migration state parity, with **no generated migration for this consolidation**.
3. Entire applicable offline unit suite passes (including `test_migration_parity`, `test_static_integrity`, `test_db_schema_workflow`, and existing OpenAPI/security tests).
4. PostgreSQL: positively identify a **separate disposable database**, rehearse fresh migration to `0008`, ensure exactly five new Zone tables, five triggers, four functions and expected constraints; test concurrency and protected data behavior. The Zone integration tests now live under `tests/integration/test_zone_registry_pg.py` and use transactional rollback isolation to preserve immutable published records while inheriting the protected pytest database fixture. The isolated cluster runner exercises the whole `tests/integration` suite, not just the five Zone tests.
5. Independently verify reverse `0008 -> 0007`, catalogs/ledger, and reapply `0007 -> 0008` with empty Zone tables. Preserve 0001..0007 history. Verify migration replay is idempotent.
6. Preserve public OpenAPI (88 pre-refactor paths) and identity/permissions; consolidating models must not change HTTP behavior.
7. Review Git diff; no secret/env modifications, production DB writes, HAL commands, wallet actions or deployment. Commit on `anubhab-work`, PR into `main` only after all required gates.

The original isolated PostgreSQL 17 acceptance proof for `0008` is historical evidence, **not** proof the new consolidated model and combined runner were executed against PostgreSQL. If the local disposable DB is unavailable, stop at source-level verification and mark PG acceptance **pending**.

## VPS2 deployment and recovery contract

`ops/vps2/rehost-evcowbbe` currently **refuses migration-hash changes**, and that protection is intentional. This package does not replace it or add an implicit live `migrate` startup hook.

`ops/vps2/schema_review.py` is a **read-only** source-revision inspection utility. Invoke it with `--prior-source <prior-release/source>` and `--candidate-source <new-release/source>`; the target paths are sources, not live database URLs. It compares historical migration file hashes between two **operator-approved source trees**, rejects rewritten/removed migrations and prints the evidence checklist. It can optionally check the SHA256 and archive listing of a custom-format `pg_dump` backup. It does NOT verify that a backup was restored; does NOT quiesce customer traffic or the mail/charging pipeline; does NOT apply migrations; and does NOT enable schema-changing rehost.

Illustrative reviewed procedure, NOT an executable live change instruction:

1. Pin exact Git revision, prior live release and target; verify runtime state and DB identity without leaking credentials.
2. Take a physically valid database backup; record immutable digest; independently restore into a separate disposable database; verify row counts, constraints, catalog and access requirements.
3. Run reviewed forward migration and (where safe) reverse on the restored clone, including concurrent writers, long transactions, timeout and custom trigger checks.
4. Approve maintenance/quiescence boundaries (API, worker, outbox, HAL), lock/statement timeouts, transaction and retry behavior, restart constraints, fallback release and audit log.
5. Implement and separately accept a guarded **production database migration executor** with before/after identity, backups, migration ledger and postflight validation. Never bypass the existing code-only schema-identical rehost gate. Coordinating migration-bearing deployments is a separate acceptance step.
6. For failure after writes resume, code rollback and schema rollback are **not equivalent**. Recovery may require forward fixes or verified backup restoration with explicit data-loss/downtime authorization. Don't blindly reverse money, email or charging state.

Until these preconditions and a safe deploy operator exist, a migration-bearing release is **blocked on VPS2** even when the source and local test database pass. This is an intentional unfulfilled integration dependency, not a hidden deployment success.

## Troubleshooting

- `makemigrations` reports changes during this pure source consolidation: inspect model field definitions and 0008 migration state; stop before generating or applying an unexpected migration.
- Test DB refused: verify only nonsecret marker identity, role, port, application/target inequality and existing state; do not remove identity gates to make tests pass.
- Catalog mismatch: compare actual PostgreSQL `pg_constraint`, `pg_trigger`, `pg_proc`, FK/index catalogs, 0008 trigger definitions and Django migration ledger; don't fake-mark migrations applied.
- A migration fails midway: do **not** rerun blindly. Preserve logs and authoritative DB state; determine transaction status and which operations committed; use the reviewed restore/recovery procedure.
- Target version newer than `0008`: generated file requires review and acceptance-gate updates; intentionally blocked by the test CLI until incorporated.
- To abandon the structural overlay before commit, use the installation backup's guarded rollback command. Never use `git reset --hard` to discard unrelated work.

## Git branches

`main` is the canonical reviewed source; `anubhab-work` owns core/DB work; `abhranil-pal-evcowbbe` owns user module changes. New schema changes remain centralized and require core owner review. Migrations are committed to the shared `main` only through reviewed integration. Publishing Git source alone **does not** deploy or migrate VPS2.
