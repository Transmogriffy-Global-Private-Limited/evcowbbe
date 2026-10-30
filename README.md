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

The reusable target is only the already operator-provisioned
`cow_wp31a_20261030_test` database. `scripts/run_pg_tests.py` never creates a
database and replaces pytest-django's normal `test_<database>` setup with a
no-op. It refuses any other database name, production `evcowbbe`, equivalent
application/test targets, unexpected schemas or public tables, cross-schema
dependencies, an unexpected Cow ledger, marker shape/identity mismatch, an
incorrect role, ownership by that role, missing least-privilege grants, or
administrative PostgreSQL capabilities.

The protected `cow_test_harness.database_identity` schema remains owned by the
operator. The runner requires only `CONNECT` on the test database; `USAGE,
CREATE` on `public`; and `USAGE, SELECT` on the marker. It must not own the
database, `public`, or the marker schema. Neither this harness nor Django uses
`flush`, drops a database, creates a database, or changes the marker.

Before any run, an approved secret loader must set the real `DATABASE_URL` only
for target comparison. Use PowerShell 7 to load the disposable target without
printing credentials:

```powershell
Set-Location C:\path\to\evcowbbe
if ([string]::IsNullOrWhiteSpace($env:DATABASE_URL)) { throw 'DATABASE_URL is required only for target comparison' }
$testUrl = Read-Host -AsSecureString 'TEST_DATABASE_URL for cow_wp31a_20261030_test'
$env:TEST_DATABASE_URL = [System.Net.NetworkCredential]::new('', $testUrl).Password
Remove-Variable testUrl
$env:COW_PG_TEST_DATABASE_IDENTITY = Read-Host 'Existing operator-owned marker identity'
$env:COW_PG_TEST_ROLE = Read-Host 'Existing least-privilege test role'
$env:COW_PG_TEST_AUTHORIZATION = 'RUN_COW_POSTGRES_ACCEPTANCE_ONLY'
```

The 0003-to-0004 rehearsal is deliberately distinct from fresh-schema
acceptance. After an operator-only clean reset (below), create a 0003 database
with an ACTIVE pairing and a historically closed pairing, then upgrade it
without flush. The harness confirms the ACTIVE row remains ACTIVE, the closed
row becomes ENDED, and IDs, vehicle/charger ownership, effective dates,
`expected`, and unknown provenance remain exact. It will fail rather than
invent a historical creation timestamp, reason, source, actor, or end actor.

```powershell
py -3 scripts/run_pg_tests.py --prepare-upgrade-fixture
py -3 scripts/run_pg_tests.py --upgrade-0003-to-0004
```

Preserve those logs and the database state as upgrade evidence. To perform the
separate fresh 0001-to-0004 acceptance, first obtain a new operator
authorization to reset only the disposable `public` schema, then run:

```powershell
py -3 scripts/run_pg_tests.py --fresh
Remove-Item Env:TEST_DATABASE_URL, Env:COW_PG_TEST_DATABASE_IDENTITY, Env:COW_PG_TEST_ROLE, Env:COW_PG_TEST_AUTHORIZATION
```

`--reset` is intentionally rejected. Django `flush` is not a schema reset.
Only the database operator may run the guarded reset below, using their
separately authorized connection to `cow_wp31a_20261030_test`; it keeps
`cow_test_harness` and its marker unchanged and refuses unexpected schemas,
dependencies, objects, identities, owners, roles, or Cow migration states.

```powershell
$operatorUrl = Read-Host -AsSecureString 'Operator PostgreSQL URL for cow_wp31a_20261030_test'
$marker = Read-Host 'Existing marker identity'
$owner = Read-Host 'Existing database and marker schema owner role'
$testRole = Read-Host 'Existing least-privilege test role'
$resetToken = Read-Host 'Operator reset authorization'
$plainOperatorUrl = [System.Net.NetworkCredential]::new('', $operatorUrl).Password
try {
    psql --dbname=$plainOperatorUrl --set=ON_ERROR_STOP=1 `
        --set=expected_database=cow_wp31a_20261030_test `
        --set=expected_marker_identity=$marker --set=expected_owner=$owner `
        --set=test_role=$testRole --set=operator_reset_authorization=$resetToken `
        --file=scripts/reset_pg_acceptance_schema.sql
} finally {
    Remove-Variable plainOperatorUrl, operatorUrl, marker, owner, testRole, resetToken -ErrorAction SilentlyContinue
}
```

The reset command cannot establish authorization by itself. The operator must
first verify their connection and authority, preserve failed-run logs, and use
only the exact disposable database. Retain the marker schema and its identity
for the life of this reusable acceptance database. Cleanup means an
operator-reviewed public-schema reset after evidence retention, never deletion
of the marker, production `evcowbbe`, protected restores, or backups.

## Deployment acceptance gate

1. Reconcile actual VPS2 systemd unit (`evcowbbe.service` in tracked Git versus `evcowbbe-dev.service` in one handoff), listener, rehost program and FE calls.
2. Complete old 74-path/82-operation OpenAPI behavioral parity, then the remaining project domains, with negative/security/concurrency/integration tests on real PostgreSQL 18.
3. Rewrite the existing `rehost-evcowbbe` workflow for an immutable Python release and Gunicorn process while keeping same operator command, rollback journal, host checks, service identity, listener, and shared Caddy isolation.
4. Agree FE maintenance interval and data-loss scope. Verify an actual restored pre-reset backup against an isolated target before performing an explicitly authorized reset of **only** CoW objects in the existing `evcowbbe` DB.
5. Apply reviewed Django migrations, reactivate Python, check full FE acceptance; roll back Go code **with its paired Go DB snapshot** if necessary. Never claim binary-only rollback is sufficient after schema reset.

The Go HAL remains independent and connects later through the stable signed command/fact adapter. Simulator coverage reduces regression risk, but real Go HAL/network/charger acceptance is still required.
