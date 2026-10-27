# 27 October 2026: outbox + Admin RBAC delivery and acceptance

## Identity and limits

This update is based on the exact `evcowbbe` Git revision `bf6ef5cdf5591eddb734387be65e0af4dab53ea6` (Swagger-enriched baseline). It does not replace the unrelated EVCPO Portal or the shared CMS/HAL. Only this auth foundation is in scope. No GitHub push, VPS2 command, Aiven write, SMTP message, or migration was performed by preparing this update.

The update adds an **immutable, additive migration 002**. The live VPS2 PostgreSQL database `evcowbbe` at `127.0.0.1:5432` is **not** the earlier Aiven `defaultdb`. The operator must choose and verify the exact environment for each action; do not assume one Admin or migration automatically exists in both.

## A. Windows workstation acceptance before Git publication

1. Apply the update package to the clean `evcowbbe` checkout at the stated revision. Its installer verifies revision, cleanliness and patch integrity, checks applicability before changing files, and never touches `.git`, `.env`, provider credentials, or Docker/Mailpit.
2. Review all changed and newly added files. Verify `internal/database/migrations/001_auth.sql` and `go.mod`/`go.sum` remain unchanged; compare the two copies of `002_outbox_admin_rbac.sql` byte-for-byte.
3. Run `pwsh -NoProfile -File .\scripts\test-all.ps1`. This checks Go module integrity, unit/contract tests, vet and all-package build **without database writes or real mail**. The test gate must show each stage passed, not only a zero exit from an individual command.
4. Provision a **separate, disposable PostgreSQL database** whose name ends in `_test`; never reuse application `defaultdb` or VPS2 `evcowbbe`. Supply `TEST_DATABASE_URL` privately in the current PowerShell process. Run `pwsh -NoProfile -File .\scripts\test-all.ps1 -Integration`. Its suite applies migrations 001/002 to that dedicated test database, exercises auth, encrypted outbox retry/stale-code suppression and RBAC allow/DENY/audit; it creates synthetic records and does not automatically delete them.
5. Verify all integration test names actually ran and passed; a skipped `_test` suite is **not acceptance**. Keep sanitized logs only, with no environment files, passwords, OTPs, bearer tokens or database URL.

If any stage fails, stop. Fix the code and rerun the entire affected gate; do not compensate with live production tests.

## B. Explicit Git, VPS and schema sequence (operator only)

1. Review the exact diff, secrets exclusion and Git status. Commit and push manually to the intended `main` only after the tests and review. The patch installer does not stage, commit or push.
2. Take and verify a recoverable **backup of the exact destination database**. Practice restoration to a separate disposable destination before calling recovery accepted. Never restore directly over a live DB as a casual test.
3. On VPS2, manually `git pull --ff-only origin main` in `/srv/evcowbbe/source`; verify revision, working-tree cleanliness, migration SQL and DB endpoint. Do **not** run `rehost-evcowbbe` yet.
4. Run `go run ./cmd/migrate status` and, after explicit authorization, `go run ./cmd/migrate up` using that environment's securely provided configuration. The migration command does **not** automatically obtain systemd's `/etc/evcowbbe/{host,secrets}.env`; use an approved protected environment-loading method. Do not copy, print, or `source` unvalidated secret files into an interactive shell. Check `schema_migrations` version 2 and checksums. Never edit migration 001.
5. Explicitly initialize the first existing, verified Admin's RBAC role on **each database that requires it**. For the intended database, a trusted operator runs `go run ./cmd/admin-rbac grant-initial-full-admin ADMIN_EMAIL` with the same protected configuration, reviews the displayed email, and types `GRANT INITIAL FULL_ADMIN`. This succeeds **only if there are no role assignments at all**. For already-initialized RBAC, do not rerun or bypass its one-time guard; use audited, authorized RBAC APIs.
6. Only then run `sudo rehost-evcowbbe deploy` followed by `sudo rehost-evcowbbe status`, the localhost readiness/version probes, the public HTTPS readiness check, and an authenticated `GET /api/v1/admin/rbac/me`. Verify the running binary's release path and version `auth-foundation-2`. **No Caddy or Cloudflare change is required by this patch.**

Migrations are forward-only. A binary rollback via `sudo rehost-evcowbbe rollback` may restore the previous API binary, but it **never** reverses migration 002 or undoes RBAC/outbox data. Do not treat binary rollback as a database restore.

## C. Functional acceptance matrix

| Gate | Evidence to collect | Expected result |
| --- | --- | --- |
| Schema | Migration status/checksum for 001 and 002 | Both applied, no drift |
| Registration | Test user and real inbox, no OTP in logs | Account and encrypted verification intent commit together; mail eventually arrives |
| Retry | Isolated `_test` suite with failing then recovered fake sender | Same queued intent retries and acknowledges; no plaintext persisted |
| Supersession/expiry | Request replacement token or OTP resend before older intent is sent | Old intent expires without delivery; new code works |
| Restart | Controlled restart with pending messages | Worker resumes from durable pending or expired lease |
| Duplicate boundary | Crash/restart around provider acceptance | At-least-once duplicate is tolerated; credential validation remains single-use |
| Admin least privilege | Verified Admin without grants | Auth succeeds; RBAC self snapshot is empty; capability-gated operations are 403 |
| Grants and denies | READ_ONLY plus direct DENY for `system.settings.read` | Read allowed before DENY, forbidden under DENY, restored after removal |
| Mutation safety | Role/override edits with reason | `system.settings.manage` required; self-edit denied; final administrator manager guarded; audits contain actor/target/reason |
| Backup and restore | Recovery to isolated database with checksum checks | Data and schema recover coherently without touching the live database |
| Operational mail | Explicit test from intended environment to controlled inbox | SMTP submission and actual inbox arrival both evidenced |

`/health/ready` tests database connectivity, **not** SMTP/outbox delivery. A good health response does not close the email acceptance gate. Operational monitoring should alert on pending age, lease accumulation, DEAD rows, and SMTP failures; logs contain only error classes and counts, never plaintext email contents or credentials.

## D. Safe read-only diagnostics

Run these with the intended database already chosen through protected configuration:

```sql
SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version;
SELECT state, count(*) FROM auth_mail_outbox GROUP BY state ORDER BY state;
SELECT role_key, count(*) FROM admin_role_assignments GROUP BY role_key ORDER BY role_key;
SELECT operation, count(*) FROM admin_rbac_audit GROUP BY operation ORDER BY operation;
```

Do not query `payload_ciphertext`, OTP digest tables, credentials, or recipient addresses merely for routine monitoring. The maintenance command `go run ./cmd/maintenance cleanup` is **mutating** and should be operator-scheduled deliberately; it retires expired pending mail and purges terminal outbox rows after 30 days while retaining RBAC/security audit history.

## E. Known boundaries

- `TEST_DATABASE_URL` acceptance, live email inbox confirmation, restore drill, and VPS deployment are **not proved by a local ZIP**. Record their actual outcome before declaring production readiness.
- At-least-once SMTP may duplicate an already accepted notification if DB acknowledgement fails. It cannot retract a message already handed to SMTP. Tokens remain server-side single-use/expiring, and workers recheck the live token/challenge before handoff.
- The existing random `OTP_HMAC_PEPPER` derives a separate AES-256-GCM key. **Do not rotate this pepper** while unsent mail exists without a planned decrypt/re-encrypt or explicit retirement procedure; rotating it also affects OTP verification.
- No business-domain Admin actions, driver approval, payment workflows, production notification analytics, or new permissions outside Annex 44 are introduced. Additional domain endpoints must independently check exact capabilities when implemented.
