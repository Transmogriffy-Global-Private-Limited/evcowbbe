# Charger on Wheels: Python backend replacement candidate

**Status: source foundation, NOT production-ready.** This package is intended to be staged under `python-backend/` on a review branch of the **existing** `Transmogriffy-Global-Private-Limited/evcowbbe` Git repository. On cutover, Python takes over the existing CMS identity (same VPS2, same PostgreSQL `evcowbbe` database, same hostname/listener, same systemd identity, and **the existing protected environment files/values**). No permanent parallel CMS is intended.

## What exists and what remains

Current source contains Django 5.2, Django Ninja, PostgreSQL models/migrations, opaque-token auth, partial Admin RBAC, customer/fleet/quotes/jobs foundations, exact money rules, durable evidence and a protocol-neutral `cow-hal.v1` interface plus simulator for a **future separate Go HAL**. It is **not** equivalent to the existing Go API yet. Driver recruitment, dispatch, full pricing, invoice/payment/refund, compensation, ratings/support and numerous FE operations are incomplete; see `docs/BLUEPRINT_IMPLEMENTATION_STATUS.md`. HAL connection is disabled until independently configured and accepted.

## Same environment contract

`config/settings.py` reads the legacy keys directly: `APP_ENV`, `HTTP_LISTEN_ADDR`, `PUBLIC_BASE_URL`, `DATABASE_URL`, `DB_MAX_CONNS`, `PASSWORD_PEPPER`, `OTP_HMAC_PEPPER`, TTL/Argon2/OTP/rate-limit knobs, SMTP and sender keys. The existing Hostinger TLS mode is used. Django signing and outbox encryption keys are deterministically purpose-derived from the existing *high-entropy* peppers; passwords use a versioned peppered Argon2 hasher. **No modification to `/etc/evcowbbe/host.env`, `secrets.env`, or `managed.env` is required for these existing features.** New HAL credentials must be separately provisioned when HAL is introduced; no default fake secret. This source is not a drop-in replacement for the current Go systemd/rehost binaries yet.

## Local staging (Windows PowerShell)

```powershell
# Run in your existing evcowbbe clone, after saving any uncommitted work.
git status --short
git fetch origin
git switch main
git pull --ff-only origin main
# Only proceed if clean and HEAD matches the pinned October 29 source revision.
git rev-parse HEAD
# Review branch, same repository, not another CMS repository.
git switch -c python-django-replacement-20261029
# Extract this ZIP elsewhere, then from its root:
py -3 scripts/stage_in_existing_repo.py 'C:\path\to\evcowbbe'
# Back in existing evcowbbe clone:
git status --short
git diff --check
```

This stages `python-backend/` without deleting Go, changing the default branch, or modifying local secrets. The staging script fails closed on a dirty checkout, unexpected revision or destination collision. Git writes/commits/pushes are left for manual review. **Do not put this unfinished candidate on `main`.**

## Local Python tests

```powershell
cd C:\path\to\evcowbbe\python-backend
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\.venv\Scripts\python.exe -m compileall -q cow config scripts manage.py
```

Use only an independently provisioned disposable `_test` PostgreSQL database for migrations and integration tests. The `scripts/dev.py migrate` command **refuses the live database `evcowbbe`**, even though Django settings can read its URL for later authorized cutover. This safety gate is intentional; the reset requires its own backup/restore-verified, maintenance-window procedure. Source-only tests are not proof of Django startup, PostgreSQL constraints, SMTP, or frontend compatibility.

## PostgreSQL acceptance (pre-provisioned test database only)

`scripts/run_pg_tests.py` is the sole supported integration-test entry point.
It does not create a database and replaces pytest-django's normal
`test_<database>` setup with a no-op. It first verifies all of the following:

- `TEST_DATABASE_URL` and `DATABASE_URL` resolve to different host/port/database
  targets, not merely different URL strings;
- the actual connected database and role match the supplied target contract;
- the URL user exactly matches the declared least-privilege test role;
- an operator-owned `cow_test_harness.database_identity` marker matches;
- the runner role is not superuser, `CREATEDB`, `CREATEROLE`, or `BYPASSRLS`;
- normal acceptance starts with no application tables in `public`;
- only migrations `cow.0001_initial`, `cow.0002_business_invariants`, and the
  additive `cow.0003_admin_fleet_vehicle_parity` are applied, then replayed
  without ledger change.

An authorized PostgreSQL operator must provision a new database, marker schema,
and least-privilege role first. The runner needs `CONNECT` on the test database,
`USAGE, CREATE` on `public`, and `USAGE, SELECT` on the marker schema. It must
not own the database or marker schema and must not have PostgreSQL administrative
capabilities. The protected live `evcowbbe` database, its role, and its backups
are never test targets.

The operator performs this once through an approved privileged PostgreSQL
session, never through the application role or this repository script. Replace
only the three reviewed identifiers and supply the marker through psql's secure
variable mechanism; do not put a password or marker value in source control,
shell history, or evidence output.

```sql
CREATE ROLE cow_pg_acceptance_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE ROLE cow_pg_acceptance_runner LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS NOINHERIT;
CREATE DATABASE cow_acceptance_20261029_test OWNER cow_pg_acceptance_owner TEMPLATE template0;

\connect cow_acceptance_20261029_test
REVOKE ALL ON DATABASE cow_acceptance_20261029_test FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE cow_acceptance_20261029_test TO cow_pg_acceptance_runner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO cow_pg_acceptance_runner;
CREATE SCHEMA cow_test_harness AUTHORIZATION cow_pg_acceptance_owner;
CREATE TABLE cow_test_harness.database_identity (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    identity text NOT NULL CHECK (length(identity) >= 32)
);
INSERT INTO cow_test_harness.database_identity (singleton, identity) VALUES (true, :'identity');
REVOKE ALL ON SCHEMA cow_test_harness FROM PUBLIC;
REVOKE ALL ON cow_test_harness.database_identity FROM PUBLIC;
GRANT USAGE ON SCHEMA cow_test_harness TO cow_pg_acceptance_runner;
GRANT SELECT ON cow_test_harness.database_identity TO cow_pg_acceptance_runner;
```

The operator must separately set a strong login password for
`cow_pg_acceptance_runner`, retain the marker only in the approved secret
store, and confirm the runner cannot alter `cow_test_harness`. A failed or
interrupted acceptance run is evidence to preserve. Provision a new marked DB
for a fresh migration acceptance; use `--reset` only for the explicit
marker-verified retry described below.

From PowerShell 7, after an approved secret loader has placed the real
application URL in the current process environment, load the test URL without
echoing it and run the fresh acceptance once:

```powershell
Set-Location C:\path\to\evcowbbe
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) { throw 'DATABASE_URL must be present only for target comparison' }
$testUrl = Read-Host -AsSecureString 'Disposable PostgreSQL TEST_DATABASE_URL'
$env:TEST_DATABASE_URL = [System.Net.NetworkCredential]::new('', $testUrl).Password
Remove-Variable testUrl
$env:COW_PG_TEST_DATABASE_IDENTITY = Read-Host 'Operator-provisioned non-secret marker identity'
$env:COW_PG_TEST_ROLE = Read-Host 'Least-privilege PostgreSQL test role'
$env:COW_PG_TEST_AUTHORIZATION = 'RUN_COW_POSTGRES_ACCEPTANCE_ONLY'
py -3 scripts/run_pg_tests.py
Remove-Item Env:TEST_DATABASE_URL, Env:COW_PG_TEST_DATABASE_IDENTITY, Env:COW_PG_TEST_ROLE, Env:COW_PG_TEST_AUTHORIZATION
```

Do not run the command again against a partially used database in normal mode.
After an interrupted test run, inspect and preserve evidence first. Only for the
same marker-verified disposable database, an operator may explicitly authorize a
flush and safe migration replay:

```powershell
$env:COW_PG_TEST_RESET_AUTHORIZATION = 'RESET_COW_POSTGRES_ACCEPTANCE_TEST_ONLY'
py -3 scripts/run_pg_tests.py --reset
Remove-Item Env:COW_PG_TEST_RESET_AUTHORIZATION
```

The `--reset` flag never creates, drops, or changes database identity; it only
flushes Django application data after all target checks pass. It must never be
used with production, a protected restore, or an unverified target.

## Deployment acceptance gate

1. Reconcile actual VPS2 systemd unit (`evcowbbe.service` in tracked Git versus `evcowbbe-dev.service` in one handoff), listener, rehost program and FE calls.
2. Complete old 74-path/82-operation OpenAPI behavioral parity, then the remaining project domains, with negative/security/concurrency/integration tests on real PostgreSQL 18.
3. Rewrite the existing `rehost-evcowbbe` workflow for an immutable Python release and Gunicorn process while keeping same operator command, rollback journal, host checks, service identity, listener, and shared Caddy isolation.
4. Agree FE maintenance interval and data-loss scope. Verify an actual restored pre-reset backup against an isolated target before performing an explicitly authorized reset of **only** CoW objects in the existing `evcowbbe` DB.
5. Apply reviewed Django migrations, reactivate Python, check full FE acceptance; roll back Go code **with its paired Go DB snapshot** if necessary. Never claim binary-only rollback is sufficient after schema reset.

The Go HAL remains independent and connects later through the stable signed command/fact adapter. Simulator coverage reduces regression risk, but real Go HAL/network/charger acceptance is still required.
