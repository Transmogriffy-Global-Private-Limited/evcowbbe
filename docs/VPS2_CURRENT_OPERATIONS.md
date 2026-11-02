# EVCOWBBE VPS2: authoritative Django runbook

**Scope:** development service, not commissioned charger operations. Current
verified baseline (2026-11-02): deployed Django commit
`2304fdfc092636e345cbef9423d3ae4044e80eee`, active `evcowbbe.service`,
public HTTPS `/health/ready`, `/openapi.yaml`, `/api/openapi.json`, admin login,
one active EVCOWBBE API listener. The original Go database and binary are
preserved offline. This document records the *target managed workflow*; installing
it is separate from publishing this document to Git.

## System boundaries and paths

| Purpose | Authoritative path / identity |
| --- | --- |
| Remote source | GitHub `Transmogriffy-Global-Private-Limited/evcowbbe`, branch `main` |
| VPS checkout | `/srv/evcowbbe/source` |
| Immutable releases | `/srv/evcowbbe/python-releases/<Git SHA>/` |
| Active Python pointer, after adoption | `/srv/evcowbbe/current-python` |
| systemd API | `evcowbbe.service`, loopback `127.0.0.1:18280` |
| Public origin | `https://dev-evcowbbe.transev.site` |
| Caddy | Existing shared VPS Caddy config; no project-only rewrite |
| Runtime DB | PostgreSQL `evcowbbe_django_next`, owner `evcowbbe_app` |
| Offline Go DB | PostgreSQL `evcowbbe` (keep for recovery; do not write) |
| Secrets | `/etc/evcowbbe/{host,secrets,managed}.env` (root protected) |
| Permanent runtime marker | `/var/lib/evcowbbe-rehost/runtime.json` |
| Historic cutover journal | `/var/lib/evcowbbe-cutover/state.json` |
| Legacy protected backups | `/var/backups/evcowbbe/` |
| Rehost command | `/usr/local/sbin/rehost-evcowbbe` after guarded adoption |
| Tracked operator sources | `ops/vps2/` |

## Initial adoption of the permanent operator

Prerequisites: GitHub `main` and VPS source must contain this operator code and
be clean; current runtime must match the exact November 2 cutover baseline.
First run `python3 -I ops/vps2/rehost-evcowbbe status` from the VPS source checkout,
then `python3 -I ops/vps2/rehost-evcowbbe adopt` as root. The operation:

1. Checks immutable release seal, active Django health, PostgreSQL DB identities,
   the protected cutover journal and original launcher/manager hashes.
2. Backs up affected exact systemd and operator files under
   `/var/backups/evcowbbe/runtime-adopt-<epoch>/` before modifying them.
3. Temporarily pauses the mail timer (never interrupts an actively running
   mail batch), creates `current-python`, installs the canonical Django launcher,
   mail guard and worker, and replaces the old Go-only guard with rehost manager.
4. Reloads systemd, restarts **only** EVCOWBBE backend, verifies public/internal
   readiness, switches the protected runtime marker to ACTIVE, resumes the mail
   timer and verifies the guard. On startup failure, restores the previous
   operator/systemd files and attempts to recover the prior live Django runtime;
   the operator must inspect service status, not assume recovery succeeded.

No Go DB import, PG global change, Caddy reload, or other-project service restart.
The historic Go cutover controller may no longer support an automatic Go return
after adoption. Emergency Go recovery is a separately reviewed manual procedure,
not an implicit feature of Django `rehost`.

## Daily operations

```bash
sudo rehost-evcowbbe status
sudo systemctl status evcowbbe.service --no-pager
sudo systemctl status evcowbbe-mail.timer --no-pager
curl -fsS https://dev-evcowbbe.transev.site/health/ready
curl -fsS https://dev-evcowbbe.transev.site/openapi.yaml -o /dev/null
```

For a new code revision: commit and push tested Django changes to `main`; update
VPS checkout using **fast-forward only**, verify it is clean and matches remote
`main`; then `sudo rehost-evcowbbe deploy <exact 40-hex commit>`. This path builds
and seals a fresh immutable release from the Git archive using pinned packages
from the previous release and runs offline unit tests/lint; it refuses changed
Django migration files. After seal verification it promotes the release pointer,
restarts only the API, probes health, re-enables the guarded mail timer, and
commits the runtime state. It makes no DB schema changes. If health fails after
promotion, it does not silently restore prior code because writes may have
already occurred. Inspect the current journal and database compatibility before
any deliberate rollback. A newly added dependency/version change requires
reviewing and updating the pinned-build procedure, not disabling verification.

**Code-only rollback:** after a successful Django-to-Django deployment, the
recorded immediately previous sealed revision can be selected using
`sudo rehost-evcowbbe rollback --approve-code-compatibility`, but only with
explicit approval that new writes remain compatible with old code. It verifies
schema identity, seals and runtime boundaries. It does **not** rewind database
rows, mail deliveries or third-party effects; a failed rollback is left for
operator reconciliation, never recursively retried.

**Schema migrations:** NOT automated by this code-only deploy procedure.
Do not manually bypass its migration hash comparison. Plan a project-specific
reviewed database backup, migration/restore, compatibility and rollback stage
before deploying a revision that changes Django migrations.

## Project-only PostgreSQL and scratch cleanup

Run `python3 -I ops/vps2/evcowbbe-cleanup.py plan`. Check that it reports exactly
which former scratch databases and root test artifacts it will process. Then,
with explicit operator action, `python3 -I ops/vps2/evcowbbe-cleanup.py apply`
performs a documented, backed-up *project-only* cleanup. It never drops or
alters `evcowbbe_django_next`, `evcowbbe`, roles, shared PostgreSQL cluster,
other service databases, or unrecognized names. The reusable `cow_wp31a_20261030_test` PostgreSQL acceptance database and its `/root/evcowbbe-pg-wp31a-20261030` test environment stay until the operator-owned test harness is safely replaced. Each retired scratch DB must have zero
connections, a known owner, verified custom-format dump and readable archive
before drop. The root test files are moved into a project-only protected archive
rather than permanently deleted. Protected original cutover/backup evidence is
retained until recovery is independently documented and tested.

## Verification and recovery

Verify GitHub `main` SHA, VPS checkout, current release, loaded systemd
ExecStart, listener 18280 (not 18282), public OpenAPI/health, admin login,
DB identities and mail guard. Review only `evcowbbe` systemd/journals and
PostgreSQL project DB names; shared services are not cleanup targets.

On deployment error, do not repeat a partially completed operation blindly.
Read `/var/lib/evcowbbe-rehost/runtime.json`, protected deployment logs/backups,
`systemctl status evcowbbe.service`, `journalctl -u evcowbbe.service`, and
read-only PG identities. The original Go DB stays separate: returning to Go
would hide writes made to Django, and requires explicit divergence signoff.
