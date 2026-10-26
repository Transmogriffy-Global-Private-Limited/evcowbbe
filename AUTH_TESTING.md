# Charger on Wheels: authentication test gate

This add-on is a **test-only change** to the Go authentication foundation.
It does not change the production handlers, schema, migrations, Go dependencies,
the secret environment file, or any data already held by the application.

## Install the add-on

1. Save the test add-on ZIP into Downloads.
2. From your existing repository root, check that the destination paths
   listed inside the ZIP do not already exist or contain personal work.
3. Extract the ZIP **into that existing repository directory**. It adds only
   new test files, the test runner, and this document. Keep the existing Git
   metadata and secret environment file; never replace either.
4. Run the safe local gate shown below. Do not rerun setup or migrations.

## Requirements

- PowerShell 7 and Go 1.25+ on the Windows development machine.
- The repository you supplied, with the Go module and checksum files.
- Internet access only if the project's Go modules are not already cached.
- **No Docker, Mailpit, local PostgreSQL, or local SMTP installation.**

## Run the automated local test gate

From the repository root:

    pwsh -NoProfile -File .\scripts\test-all.ps1

This runs dependency integrity verification, all Go unit tests and HTTP
contract tests with caching disabled, static analysis via Go vet, and a build
of all packages. It reports each stage's pass/fail status and exits nonzero
on failure.

This default operation **does not access the application's PostgreSQL
database or send an email**. Any inherited test database URL is temporarily
removed while the normal test and build stages run.

## Run end-to-end PostgreSQL tests safely

1. Provision a **separate, disposable PostgreSQL database** using your
   existing PostgreSQL provider. Give it a name ending in _test, for
   example evcowbbe_auth_test. It must be dedicated to this suite. Do not
   point this at defaultdb, a production database, or a database containing
   unrelated application tables.
2. Configure the test database credentials in the **current PowerShell
   process only**, ideally via a hidden prompt or another approved secure
   credentials mechanism. Do not place a secret-bearing connection URL in
   command history, a committed file, or a chat message.
3. Ensure TEST_DATABASE_URL resolves to that isolated database, then run:

    pwsh -NoProfile -File .\scripts\test-all.ps1 -Integration

The integration runner verifies the _test suffix, compares the test
destination against the application's database URL, and refuses matching
targets. The Go tests independently verify the name, refuse databases with
unrelated tables, and use test-only password/OTP peppers and an in-process
fake mailer.

**Explicit integration mode writes to the test database.** It applies auth
migrations if not already applied and creates test users, sessions,
challenges, tokens, and rate-limit records. It does **not** automatically
delete these records. A dedicated disposable database is therefore mandatory;
delete or recreate it later only after separately confirming its exact scope.

Integration mode does not send real email or connect to Hostinger. The email
tests use a temporary SMTP receiver inside the test process; it starts and
stops automatically and does not install a mail service.

The test runner does not start or stop the running auth API and does not run
the production database migration command.

## Controlled tests against the existing application database

If an operator deliberately chooses to use the currently configured
application database for this development round, the following narrower
command is available:

    pwsh -NoProfile -File .\scripts\test-all.ps1 -ExistingDatabase

The runner displays the app DB hostname, port and database name without its
credentials and demands an explicit TEST_ONLY confirmation. This mode:

- Verifies the **already applied** auth migration checksum; it **never**
  performs a migration or schema change.
- Runs only the HTTP-level integration test functions in the API package,
  using newly generated unique example.test customer and driver accounts.
- Uses test-only Argon2/OTP peppers, a distinct synthetic IP for limiter
  keys, and an in-process fake mailer. **It does not use Hostinger SMTP.**
- Does **not** invoke admin bootstrap, register an Admin, change or reset
  any existing Admin password, toggle existing Admin 2FA, revoke an existing
  Admin session, or delete any pre-existing account.
- Takes a read-only fingerprint of all existing Admin accounts' identity,
  password-hash and 2FA/account-state attributes before each test and checks
  that the fingerprint is unchanged afterward. This detects unintended
  changes, but is not a substitute for a database backup.
- **Retains** the newly created test users and their session/audit records
  for traceability. There is no automated cleanup and no broad DELETE.

The operator must accept that test records and test-rate-limit records will
exist alongside application data. This mode is intended for the current
development environment, **not** for a populated production service. The
dedicated _test database remains the preferred long-term acceptance path,
and its separate service-level tests are not run in ExistingDatabase mode.

## Automated coverage

| Layer | Examples |
| --- | --- |
| Public HTTP contract | liveness/version, headers, unknown routes, invalid body/media, missing bearer, admin registration forbidden |
| Password/security primitives | existing Argon2id, email validation, token/OTP tests |
| Email/MIME | all four templates, multipart formatting, HTML escaping, missing template variables, injection rejection |
| Customer and driver | register, verify, duplicate register behavior, unverified login blocked, PENDING driver state |
| Role isolation | cross-plane token and login denial |
| Token/session lifecycle | access and refresh token rotation, reuse revocation, logout, logout-all |
| Recovery | invalid and valid password change, reset delivery capture, single-use reset, prior-session revocation |
| Email OTP | off-by-default, enable, challenge, wrong code, correct code, replay denial, disable |
| Database limiter | allowance and over-limit behavior |
| Refresh concurrency | simultaneous refresh requests, one winner and family revocation |

Go prints individual subtests and integration test names. **A test not run
is not a pass**; the summary explicitly distinguishes local-only success
from success including PostgreSQL.

## Existing security and operational gaps

These tests increase coverage but do not mean the product is production
security-accepted. The original source still requires independent security
review and additional evidence for:

- Transactional outbound-email outbox and retry delivery after failures.
- Backup/restore and database migration recovery drills.
- Real TLS reverse-proxy integration and trusted-client-IP behavior.
- Browser/client credential storage and frontend CSRF/CORS policies.
- Multi-instance concurrency, high-volume load, distributed rate limiting,
  and expiry-boundary tests with controllable time.
- Full negative-matrix coverage for every rate-limit, OTP resend, and DB
  fault-injection path.
- Admin business authorization capabilities and driver approval rules,
  which do not yet exist in this auth-only foundation.
- Real mail provider deliverability monitoring (your one delivery test is
  a useful manual sample, not an automated delivery guarantee).

Do not deploy to VPS2 merely because this suite runs; application acceptance
and manual deployment readiness remain distinct.

## If a test fails

Keep the sanitized output from the test runner, including the failing test
name and HTTP status/error code. **Do not share** the secret environment
file, passwords, tokens, full database connection strings, or SMTP credentials.
Fix and rerun the test gate. Do not rerun production migrations or rebuild
the first Admin as part of test troubleshooting.
