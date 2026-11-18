# CoW V3.4: Django UUID primary-key migration parity and honest offline check

**Parent**: `2094e55a8763215f138e9ab710da6e9db1891bad`, branch `anubhab-work` only. **Status**: source-only repair candidate, not Django/PostgreSQL acceptance.

## Actual observed failure

On the operator's Windows checkout, `scripts/db.ps1 status` reported migration 0019, then `scripts/db.ps1 check` returned Django system check PASS but `makemigrations --check --dry-run` failed, proposing six `AlterField(id)` operations. The command also logged a database-connection timeout warning, despite its advertised no-DB claim. A subsequent unconditional `Write-Host '...PASS'` was not evidence: the earlier command and explicit guard failed.

The six new UUID primary-key fields in migration 0019 omit `serialize=False` whereas Django's model representation for these primary keys expects it. All six are bookkeeping state mismatches; no physical schema parity is established here.

## Chosen correction

- Preserve published migration `0019` unchanged (append-only history). Add migration `0020_cow_uuid_pk_serialization_state`, consisting of **exactly six** `AlterField` operations on the named `id` UUID fields with the same default, editability, primary-key and serializer metadata. No models, field types, identities, constraints, existing data or service behavior are intentionally changed. SQL effects still require an independently approved isolated PostgreSQL check.
- Make `scripts/db.ps1 check` call `scripts/check_migration_parity_offline.py` through the existing test-only, loopback-configured child environment. The new script uses Django's own `MigrationLoader(None)` and `MigrationAutodetector`, deliberately overriding DB connection establishment to raise an error if ever attempted. This removes reliance on `makemigrations`' database history probe and makes `DATABASE_CONTACTED=NO` true for **this parity operation**. A real database history audit remains a separate acceptance gate.
- Document that `db.ps1 generate` still invokes Django's normal `makemigrations`, which *may attempt* a connection to the isolated, deliberately unreachable loopback target; the stronger offline guarantee applies only to `status` and `check`.
- Keep all feature gates OFF; never alter a real PostgreSQL database, deploy, or send HAL commands for this source repair.

## Verification and downstream

After installing, `db.ps1 status` should report 20 migration files, last `0020...`. `db.ps1 check` must report `System check identified no issues (0 silenced).` and `MODEL_MIGRATION_PARITY=PASS; DATABASE_CONTACTED=NO`. If it reports another migration difference, preserve full output and stop. These checks do NOT validate physical PostgreSQL schema, live API/OpenAPI, real payment, OTP, energy or OCPP.

A separate *positively identified disposable* PostgreSQL 17 environment must verify: replay 0001..0020, catalog constraints/indexes, no unintended SQL/DDL on 0020, consistent Django migration history, reversal with correct explicit approval, and the relevant concurrency and integration suites. Older isolated acceptance runner primarily covers migration 0009 and is not sufficient to accept 0017..0020.

Before committing, verify the branch, pinned base, clean prior worktree, exact staged paths, syntax checks and two runner outputs. Commit/push ONLY to `anubhab-work` and verify remote SHA; never force push, merge to `main`, or touch another developer's branch.
