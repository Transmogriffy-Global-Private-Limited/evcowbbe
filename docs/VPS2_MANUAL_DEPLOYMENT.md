# EVCOWBBE VPS2 manual hosting runbook

**Scope:** only `evcowbbe`. Never alter EVCPO Portal, other Caddy site blocks,
other PostgreSQL databases, or the Cloudflare zone-wide SSL setting without
separate impact review. This add-on supplies no CI/CD, webhooks or deployment
timers. These instructions assume `dev-evcowbbe.transev.site` uses a Cloudflare
Free orange-cloud DNS record and VPS2 already hosts Caddy and PostgreSQL 18.

## Contract and stages

1. **Manually** change/pull Git source (the rehost command never contacts Git).
2. **Manually** approve and run migrations separately (the rehost command does
   not modify the database schema).
3. Run `sudo rehost-evcowbbe`. It validates the host, downloads Go module
   dependencies when absent, checks integrity, executes tests/vet, builds with
   a restricted builder account, probes actual PostgreSQL/SMTP read-only,
   atomically selects a versioned binary, restarts its systemd service and
   checks readiness plus the running executable path.
4. If tests/build/preflight fail, no binary switch happens. If activation or
   health fails, the previous release is restored and checked. The **first**
   deployment has no rollback target. A per-host deployment journal allows
   reconciliation of interrupted operations on the next invocation.
5. systemd only executes and supervises `/srv/evcowbbe/current/server`. It
   does not fetch, build or migrate at service startup. `Restart=on-failure`
   restarts the same selected release. Normal binary switch briefly restarts
   the application; it is not zero-downtime or an atomic database rollback.

## Already completed by the operator (2026-10-26)

- VPS2 PostgreSQL: DB `evcowbbe`, login `evcowbbe_app`, TCP `127.0.0.1:5432`,
  SCRAM password; no changes to other databases.
- Auth migration 001: 9 tables, checksum recorded and verified.
- Admin: `pujadas@tgplin.com` provisioned and email-verified in the **VPS DB**;
  the distinct Aiven DB/admin remain untouched.
- Git repository at `/srv/evcowbbe/source`, original revision
  `fa56a487e45f73c6bbd46cf591829c4e1f23732d`.
- `/etc/evcowbbe/host.env` and `/etc/evcowbbe/secrets.env` created root-only,
  permissions 0600, directory 0700. Live PostgreSQL and SMTP preflight passed.

## 1. Apply the code add-on on the Windows development machine

Do this only from the `evcowbbe` repository, on a clean `main` branch. The
distribution ZIP contains:

- a revised `internal/api/server.go` with a scoped proxy IP trust boundary;
- `internal/api/remote_ip_test.go` containing spoofing and proxy tests;
- `ops/vps/` containing EVCOWBBE-only scripts, unit and Caddy snippet;
- this runbook.

Before applying, inspect the ZIP entries. The add-on's installation command
must verify the original `internal/api/server.go` SHA-256 against the hash
published with the package and refuse overwriting existing `ops/vps` files.
Run `go test ./...`, `go vet ./...`, `go build ./...`, review the diff, commit
and push to `main` explicitly. The add-on does not contain `.env`, database
dumps, passwords or tokens. Do not push any real credentials.

## 2. Update VPS source manually

On VPS2 as root:

```bash
cd /srv/evcowbbe/source
git status --short
git pull --ff-only origin main
git log -1 --oneline
git status --short
```

Only proceed with a clean checkout and a reviewed expected revision. Verify
that source has `ops/vps/install-vps.sh` and `ops/vps/rehost-evcowbbe`. The
`rehost-evcowbbe` command itself **never** runs Git operations.

## 3. Install the manual deployment service and command

Check the source and compare its scripts to this runbook. Then:

```bash
sudo bash /srv/evcowbbe/source/ops/vps/install-vps.sh
sudo rehost-evcowbbe check-env
sudo rehost-evcowbbe status
```

The installer creates only dedicated `evcowbbe` and `evcowbbe-build` Linux
service identities and EVCOWBBE-owned directories, installs
`/etc/systemd/system/evcowbbe.service` and
`/usr/local/sbin/rehost-evcowbbe`, and runs `systemctl daemon-reload`.
It does **not** start the server, touch Caddy, clone Git or run migrations.
Re-running the installer with unchanged files is safe. Use `--upgrade` only
after reviewing differences in an existing unit/script. This explicit step
is necessary if a later Git pull changes the installer or rehost command.

`/etc/machine-id` is stored in `/etc/evcowbbe/host.id` once and checked on
every rehost. This prevents silently reusing a host's environment on a
different machine. `/etc/evcowbbe/managed.env` is automatically rewritten
with that host identity and the active build revision on deployment and
rollback; its content contains no secret. The **manual** `host.env` defines
the public domain and private listener, while `secrets.env` contains the
permanent DB/SMTP credentials and two distinct authentication peppers. Neither
is automatically regenerated or changed. Env files are read by systemd as
root and passed to the dedicated service process; other Linux users should
not be granted the service account identity. The current Go app uses process
environment variables for secrets, so further tightening with systemd
credentials would require a separate code change.

Expected host values:

```ini
APP_ENV=staging
HTTP_LISTEN_ADDR=127.0.0.1:18280
PUBLIC_BASE_URL=https://dev-evcowbbe.transev.site
MAIL_PROVIDER=SMTP
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_TLS_MODE=tls
MAIL_FROM_ADDRESS=team@transev.in
MAIL_FROM_NAME=TransEV
```

Expected sensitive keys in the separate root-only `secrets.env`: `DATABASE_URL`
for `evcowbbe_app` at `127.0.0.1:5432/evcowbbe?sslmode=disable`,
`PASSWORD_PEPPER`, `OTP_HMAC_PEPPER`, `SMTP_USERNAME`, and `SMTP_PASSWORD`.
Do not show, print, commit or automatically rewrite the secret values.

## 4. First launch and repeat rehosting

```bash
sudo rehost-evcowbbe
sudo rehost-evcowbbe status
systemctl show evcowbbe.service -p ActiveState -p MainPID --no-pager
curl --noproxy '*' -fsS http://127.0.0.1:18280/health/ready
```

On first success, it enables `evcowbbe.service` for automatic restart on
reboot. Check `EVCOWBBE_REHOST=PASS`, `ActiveState=active`, and a ready health
response. If the first run fails, there is no previous release: inspect the
printed failing stage, `journalctl -u evcowbbe.service`, and retry after
fixing the issue. Do not manually delete or rename release directories.

After a **manual** new Git pull, run `sudo rehost-evcowbbe` again. For manual
rollback to the previous successful binary, use:

```bash
sudo rehost-evcowbbe rollback
sudo rehost-evcowbbe status
```

Explicit rollback swaps current/previous releases and restarts the service.
It never undoes schema migrations, deletes test accounts or changes auth
peppers. A failed deployment's new binary is retained as a non-active
release for inspection. Old releases are not automatically deleted.

Interrupted switch/rollback operations use an EVCOWBBE-only journal at
`/srv/evcowbbe/.pending-deploy`. `rehost-evcowbbe status` exposes the warning;
the next deploy/rollback invocation acquires a lock and attempts to restore
the previous working binary before proceeding. A sudden host power loss can
temporarily leave a candidate selected until the next manual reconciliation;
do not claim uninterrupted availability across power failures.

## 5. Caddy integration: separate hostname only

Do this **only after localhost readiness passes** and confirm Cloudflare
currently has the DNS record orange-clouded, pointing to the VPS2 public
origin. On the Cloudflare Free plan, HTTPS via orange cloud and Full (strict)
is supported. **Inspect the current Cloudflare SSL mode first**; do not
change a zone-wide mode if that could affect EVCPO Portal. A scoped
configuration rule, if needed, requires separate review.

The repository supplies `ops/vps/dev-evcowbbe.transev.site.caddy`, which
contains only the new hostname. Its matcher uses official Cloudflare source
IPv4/IPv6 ranges and only forwards requests from those ranges that have a
`CF-Connecting-IP` header. It **overwrites** `X-Real-IP` and removes the
original CF header before proxying to Go's loopback listener. The Go API
accepts this forwarded address only from its immediate loopback peer.
This fixes the otherwise shared-127.0.0.1 rate-limit problem without adding
global Caddy `trusted_proxies` settings that could affect other sites.

Before modifying shared Caddy, verify the new hostname is absent and save
a recoverable copy of `/etc/caddy/Caddyfile`. Copy the standalone snippet to
`/etc/caddy/evcowbbe-sites/dev-evcowbbe.transev.site.caddy`. Since the old
Caddyfile contains no import directive (observed 2026-10-26), add exactly
one independent final import line to `/etc/caddy/Caddyfile`:

```caddyfile
import /etc/caddy/evcowbbe-sites/*.caddy
```

Do not reformat, rewrite, delete or restart the existing virtual hosts.
Validate first:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
```

Then reload Caddy (not restart) if validation succeeds:

```bash
sudo systemctl reload caddy
curl -i https://dev-evcowbbe.transev.site/health/ready
```

If validation fails, do **not** reload; remove only the newly added import
line and EVCOWBBE snippet, then validate again. Avoid restoring an old full
Caddyfile blindly if anyone else may have edited it in the meantime.

Automatic Caddy ACME certificates require the origin reachable for the
selected challenge. If Cloudflare proxying interferes during initial issuance,
temporarily greying only the EVCOWBBE DNS record can help certificate
issuance, but **the provided snippet intentionally blocks direct non-Cloudflare
traffic**. Do not toggle to grey while that snippet is active unless the
route has been separately adjusted for the temporary certificate test.
Always return to a validated Cloudflare-only state before public auth testing.

Cloudflare IP ranges can change. Recheck official `https://www.cloudflare.com/ips-v4`
and `https://www.cloudflare.com/ips-v6` when maintaining the site.

## 6. Production-facing acceptance

- Existing EVCPO Portal service/Caddy route remains healthy, with identical
  service unit, listener and Caddy route before/after.
- Caddy config validates; Cloudflare origin TLS is trusted; no redirect loops;
  correct public hostname and HTTPS; `/health/ready` returns HTTP 200.
- Go security tests pass, including spoofed/non-loopback `X-Real-IP` cases.
- Confirm remote visitor IP remains individual across two client networks and
  rate limits are not globally shared; do not trust user-supplied forwarding
  headers.
- Verify a real Hostinger test email reaches the intended inbox. SMTP
  handshake success alone is **not** delivery acceptance.
- Perform a login test with response tokens suppressed from logs/terminal;
  verify wrong credentials, expired/rotated tokens, and logout behavior.
- After another safe revision is deployed, test `rollback` and subsequent
  rehost against both ready and failed-health cases; inspect live `/proc/PID/exe`.
- Review security posture, mail retry/outbox, backup/restore, monitoring and
  external vulnerability assessment before treating auth as production-accepted.

## 7. Embedded Swagger UI and OpenAPI contract

Since the Swagger UI add-on, the versioned Go binary itself serves:

- `GET /swagger/` (also `/swagger/index.html`) - public interactive UI;
- `GET /openapi.yaml` - the exact committed `docs/contracts/auth.openapi.yaml`;
- `/swagger/swagger-ui-bundle.js`, `/swagger/swagger-ui.css`, and
  `/swagger/init.js` - pinned, locally embedded frontend assets.

The UI uses Swagger UI 5.33.3, vendored under `internal/api/swagger/`, with its
Apache-2.0 license and bundled third-party notices. There is **no CDN** and no
extra Caddy site configuration. The OpenAPI server URL is `/` (same origin),
so browser requests work both on loopback development and through the existing
Cloudflare/Caddy endpoint. The JavaScript prevents selecting a different spec
through URL query parameters and disables persistent authorization. The docs
have a restrictive CSP and `noindex` response header. They are publicly
accessible and are **not** an admin dashboard or authentication bypass.

On the Windows development checkout, review the add-on and run:

```powershell
go test -count=1 ./...
go vet ./...
go build ./...
git diff --check
```

After manually committing and pushing to GitHub, pull on VPS2 yourself, and
run `sudo rehost-evcowbbe deploy`. As usual, rehost **does not pull Git** and
does **not** run migrations. It builds, tests and embeds the UI and spec into
the new release. Rollback selects the prior binary together with its prior
documentation; neither an old spec file nor CDN may drift independently.

Verify only EVCOWBBE endpoints after deployment:

```bash
curl -I --max-time 10 https://dev-evcowbbe.transev.site/swagger/
curl -I --max-time 10 https://dev-evcowbbe.transev.site/openapi.yaml
curl -I --max-time 10 https://dev-evcowbbe.transev.site/swagger/swagger-ui-bundle.js
```

All three should return HTTP 200 with appropriate `Content-Type` values.
Open `https://dev-evcowbbe.transev.site/swagger/` in a normal browser to verify
that the operations render and load the YAML without console errors.
Do not run state-changing operations against real accounts merely to validate
the page; targeted authentication acceptance testing is a separate workflow.

If documentation fails after deployment, inspect EVCOWBBE's own Go service
logs and return to the last working binary using the manual rollback command
(when a previous release exists). Do not edit Caddy or EVCPO Portal for a
documentation-route issue.

## Maintenance and recovery

- `sudo rehost-evcowbbe status` prints release paths and service state only.
- `sudo rehost-evcowbbe check-env` checks host pin, the fixed HTTPS hostname,
  listener, local DB target and file permissions without showing secrets.
- `journalctl -u evcowbbe.service --since '30 minutes ago' --no-pager`
  diagnoses server failures. Keep any logs with private details restricted.
- For secrets changes, use a root editor on `secrets.env` and keep an external
  encrypted backup. Existing password/OTP peppers must remain unchanged.
  Trigger `sudo rehost-evcowbbe` to validate and restart with amended values.
- For schema changes, take a verified backup and manually run the reviewed
  migration tool before a compatible build. Binary rollback cannot undo
  incompatible schema changes.
- To fix a failed Caddy edit, inspect and revert only EVCOWBBE-specific
  additions; do not reset the shared site file or touch EVCPO Portal.
