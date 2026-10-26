# Charger on Wheels — Go + PostgreSQL authentication foundation

**Scope:** initial human authentication only; not the EV service-job, fleet, billing, HAL, or dispatch implementation. No CI/CD, no VPS scripts, no production rollout. The existing Git repository can be retained while replacing its working tree with this package.

Built against the project's 24 September 2026 sole-source blueprint: human auth and payload standard (annex 11), security (annex 15), configuration (annex 17), repository discipline (annex 22), and Admin RBAC boundary (annex 44).

## Quick start — existing PostgreSQL + real SMTP (Windows 11 / PowerShell 7)

**No Docker, Mailpit, local PostgreSQL, or local SMTP installation.** Requirements: **Go 1.25+** and internet access to fetch Go dependencies the first time; connectivity and credentials for your **already-provisioned, dedicated PostgreSQL database** and real SMTP service. Do not point this new schema at the existing VPS2 production database or another application. No GitHub/VPS changes are performed.

**First-time setup is deliberately staged.** This also repairs an extraction of the earlier Docker-based v1 bundle: preserve `.git`, existing local source and `.env` secrets; apply the v2 upgrade ZIP as described alongside the download.

1. `pwsh -NoProfile -File .\scripts\configure.ps1` — prompts for PostgreSQL host/port/database/username/password/SSL and SMTP host/port/TLS/credentials/sender. Secrets are hidden while typing. Writes **ignored `.env`** (previous `.env` is backed up, existing password/OTP peppers preserved). **No database or SMTP access occurs at this stage.** The old `scripts/bootstrap.ps1` entrypoint now invokes configuration only.
2. `pwsh -NoProfile -File .\scripts\continue-setup.ps1` — downloads Go modules, runs tests, validates live PostgreSQL and SMTP handshakes *without sending mail*, and then asks you to type `MIGRATE` to apply the schema to the displayed database. **No database provisioning. No server start.** If you cancel, the schema is not applied. Stop if the selected database contains unrelated application objects.
3. `pwsh -NoProfile -File .\scripts\send-test-email.ps1` (optional, recommended) — explicitly sends **one** real email to a recipient you enter, then verify delivery in your normal inbox. A successful SMTP handshake alone does not prove delivery.
4. `pwsh -NoProfile -File .\scripts\run.ps1` — starts the API at `http://127.0.0.1:18080` (loopback only). Check `/health/ready`.
5. In another terminal, `pwsh -NoProfile -File .\scripts\bootstrap-admin.ps1` to create the first admin using a hidden password prompt. Admin email is marked verified, but there is no public admin-registration API.

An existing `.env` from the failed Docker setup probably contains a placeholder local database DSN and Mailpit defaults. **Run `configure.ps1` again** rather than using those values. `.env` and `.env.backup-*` are gitignored. Never send or commit them.

An optional, independently provisioned *disposable* PostgreSQL database with a `_test` suffix allows the real integration suite. In that PowerShell session set `$env:TEST_DATABASE_URL` to its URL before running `continue-setup.ps1`; the script never creates it, rejects an app-DB match and deletes the variable from its own child scope after testing. Without a test database, the integration test is skipped **explicitly**. Application migrations are always an explicit approval action and should be backed up / reviewed for any existing data.

Subsequent runs: `pwsh -NoProfile -File .\scripts\run.ps1`. Schema inspection: `go run ./cmd/migrate status` **after** the schema_migrations bookkeeping has been created. Expired data cleanup: `go run ./cmd/maintenance cleanup` (mutates your configured DB; run intentionally).

## Identity, planes, sessions

- Emails are normalized to lowercase. **One account per email.** The schema supports multiple plane principals per account, but this first slice does *not* implement automatic identity linking or promoting an existing customer to a driver/admin; those are sensitive business decisions. To test all three now, use separate emails.
- Public registration `/api/v1/app/auth/register` -> ACTIVE customer principal. `/api/v1/driver/auth/register` -> PENDING driver principal; auth is possible, but **PENDING is not approval for driving/dispatch**. Only `me` and auth routes exist in this slice.
- Admin is created using the local bootstrap command, with an ACTIVE `admin` principal. **Admin RBAC capabilities are NOT granted just from possession of an `admin` token**; administrative business API handlers must implement the capability registry before being introduced.
- Registration sends a single-use 24h email verification token; login is blocked until verified.
- Auth uses **opaque 256-bit bearer access tokens** (15 minutes) and **opaque refresh tokens** (30-day rolling, 90-day absolute limit). Only SHA-256 digests are persisted. No JWT private key or logout cache is required. Refresh rotates access and refresh in a PostgreSQL transaction; reuse of a rotated refresh token revokes that session family.
- Every access token is bound to exactly one plane. Wrong-plane use returns `403`; logout revokes the current session immediately at the database.
- Passwords are Argon2id (64 MiB, three passes, parallelism one) with a unique random 16-byte salt and independent environment pepper. Changing/resetting password revokes all existing sessions. Change password requires current password, reset requires a 20-minute email token.
- **2FA OFF by default.** Opting in requires an already-authenticated session, password confirmation, and a 6-digit email OTP. Subsequent logins require a fresh email OTP. OTP expires in 10 minutes, five attempts maximum; resend is restricted. Disable also requires password + OTP. Repeated old codes cannot be reused.
- The 2FA mechanism is explicitly **email OTP**. Since email is also used for password recovery, it is not phishing-resistant or independent of email-account compromise; consider passkeys/TOTP for a later security upgrade.

## HTTP endpoints

All mutations require `Content-Type: application/json`, reject unknown fields, have 32KiB body limit and return `{"data":...}` or `{"error":{"code":...,"message":...,"request_id":...}}`. Tokens must be held in the client secure credential store; do **not** place them in URLs/localStorage/logs.

Public global:

| Method | Route | JSON body |
|---|---|---|
| POST | `/api/v1/auth/verify-email` | `{"token":"..."}` |
| POST | `/api/v1/auth/resend-verification` | `{"email":"..."}` |
| POST | `/api/v1/auth/forgot-password` | `{"email":"..."}` |
| POST | `/api/v1/auth/reset-password` | `{"token":"...","new_password":"..."}` |

Use `PLANE` = `app`, `driver`, `admin`:

| Method | Route | JSON body / auth |
|---|---|---|
| POST | `/api/v1/PLANE/auth/register` | email/password; only app/driver |
| POST | `/api/v1/PLANE/auth/login` | `{"email":"...","password":"..."}` |
| POST | `/api/v1/PLANE/auth/verify-otp` | `{"challenge_id":"uuid","code":"123456"}` |
| POST | `/api/v1/PLANE/auth/2fa/resend` | `{"challenge_id":"uuid"}`; only login OTP |
| POST | `/api/v1/PLANE/auth/refresh` | `{"refresh_token":"..."}` |
| GET | `/api/v1/PLANE/me` | `Authorization: Bearer ACCESS_TOKEN` |
| POST | `/api/v1/PLANE/auth/logout` | bearer token |
| POST | `/api/v1/PLANE/auth/logout-all` | bearer token |
| POST | `/api/v1/PLANE/auth/password/change` | bearer; `{"current_password":"...","new_password":"..."}` |
| POST | `/api/v1/PLANE/auth/2fa/enable/begin` | bearer; `{"password":"..."}` |
| POST | `/api/v1/PLANE/auth/2fa/enable/confirm` | bearer; `{"challenge_id":"...","code":"..."}` |
| POST | `/api/v1/PLANE/auth/2fa/disable/begin` | bearer; `{"password":"..."}` |
| POST | `/api/v1/PLANE/auth/2fa/disable/confirm` | bearer; `{"challenge_id":"...","code":"..."}` |
| GET | `/health/live`, `/health/ready`, `/version` | public diagnostics |

Login response with disabled 2FA: `{"data":{"tokens":{"access_token":"...","refresh_token":"..."},"two_factor_required":false}}`. With 2FA: `{"data":{"two_factor_required":true,"challenge_id":"..."}}`, then verify the code using `auth/verify-otp` to obtain tokens.

## Example: register + verify + login

```powershell
$base = 'http://127.0.0.1:18080'
$body = @{email='test.customer@example.test'; password='YourOwnLongUniquePassword2026!'} | ConvertTo-Json -Compress
Invoke-RestMethod "$base/api/v1/app/auth/register" -Method Post -ContentType 'application/json' -Body $body
# Open your real email inbox and read the verification message.
$token = Read-Host 'Verification token from your email inbox'
Invoke-RestMethod "$base/api/v1/auth/verify-email" -Method Post -ContentType 'application/json' -Body (@{token=$token} | ConvertTo-Json)
$login = Invoke-RestMethod "$base/api/v1/app/auth/login" -Method Post -ContentType 'application/json' -Body $body
$access = $login.data.tokens.access_token
Invoke-RestMethod "$base/api/v1/app/me" -Headers @{Authorization="Bearer $access"}
```

`SMTP` uses text and HTML templates under `internal/mailer/templates/`. Real SMTP is configured during `scripts/configure.ps1`; `SMTP_TLS_MODE=starttls` (commonly 587) or `tls` (commonly 465) validates the remote server certificate. `SMTP_TLS_MODE=none` is allowed only for loopback development. Email verification/security delivery needs a reliable transactional outbox and retries before unattended production use (see below).

## Database and security boundaries

- Migrations are explicit `go run ./cmd/migrate up`; server startup only **verifies** migration checksums, never applies migrations. All SQL is parameterized.
- PostgreSQL holds sessions, challenge state, rate limits, security audit, and used-refresh fingerprints. Login attempts are rate-limited per IP **and** identifier; OTP sends and OTP code attempts have additional limits. Unknown-email password checks perform Argon2 work to mitigate timing leaks.
- Local HTTP listens only on `127.0.0.1`. Deployment to an actual VPS requires an HTTPS reverse proxy, trusted proxy configuration, operational monitoring and backup/restore planning. CORS is deliberately closed (no cross-origin browser allowance) until the frontend origin is decided.
- `MAIL_FROM_ADDRESS`, Postgres and SMTP parameters, Argon2/TTL limits, and two independent peppers are set in `.env`. `.env` is gitignored; generated credentials are unique per local installation.
- Security audit entries record event types, times, account and plane—no passwords, OTPs, tokens or SMTP credentials.

## Status and acceptance limits

**Implemented:** complete first-slice REST flows listed above, schema and explicit migrations, session rotation/reuse invalidation, email verification, email OTP enable/disable, password reset/change, Admin CLI bootstrap, server health, local SMTP templates, SQL rate limits, no-credentials ZIP, and Windows bootstrap.

**Not yet production-accepted:** independent security audit, full integration suite against a dedicated real PostgreSQL test DB, real SMTP delivery acceptance, transactional retryable outbox, Admin business RBAC, driver recruitment/approval, multi-instance tests, secure production HTTPS reverse proxy, and backup/restore. `continue-setup.ps1` runs Go tests, live read-only connectivity checks and optional live test-database integration, but does not claim production readiness.

**Do not deploy this ZIP directly to VPS2 or treat it as a replacement for its recovered backend.** The VPS2 CI/CD retirement and switch to manual deployment remain separate, explicitly authorized work.

## Test acceptance gates

Unit tests run without PostgreSQL. The `TestIntegrationRegisterRefreshMFA` test runs **only** when `$env:TEST_DATABASE_URL` targets a separately provisioned disposable `_test` database; it writes auth tables and integration accounts **there**, never to your application DB. It tests registration, verification, plane separation, refresh rotation/reuse revocation and optional email-OTP login with a captured mock sender. `cmd/preflight` probes PostgreSQL and SMTP without mutations. `scripts/send-test-email.ps1` is an explicitly confirmed *real message*, not an emulator.

### Avoiding unintended side effects

This project uses `.gitignore` for `.env` and uses randomly generated credentials on first setup. A ZIP is not a Git commit and is not pushed to GitHub. Keep `.git/` intact when extracting into your existing empty local repository. Do not overwrite the working production backend or enable unattended deployment from this package.
