# Same VPS2, old FE integration stays live: operator playbook

This is a **planning/acceptance playbook**, not a deployment log. Do not
execute it as if any command already ran or as a blanket permission to modify
VPS2. The currently running Go API (`dev-evcowbbe.transev.site`, historical
`evcowbbe-dev.service` and DB `evcowbbe`) is reserved for FE developers.

## Phase A: Safe evidence only

On VPS2, independently confirm:

```bash
systemctl is-active evcowbbe-dev.service
systemctl show evcowbbe-dev.service -p MainPID -p NRestarts
free -h
nproc
df -h /srv /var
systemctl is-active postgresql caddy
caddy validate --config /etc/caddy/Caddyfile
```

Inspect actual service unit, Go source/revision, Go live OpenAPI, DB name,
PostgreSQL cluster/connection capacity and current FE needs **without printing
secrets**. If VPS2 lacks safe spare capacity, stop the candidate plan rather
than destabilize live FE development. No automatic resize/restart.

## Phase B: Candidate only

Provision independent paths and identities on the **same VPS2**:

```text
/srv/evcowbbe-python/current/  versioned Python candidate source
/etc/evcowbbe-python.env       private env, readable by service identity
PostgreSQL DB: cow_v2_isolated (separate credentials and owner)
Loopback listener: 127.0.0.1:18081
Service candidate: evcowbbe-python.service
```

These are target examples, not evidence of installed resources. Use the supplied
systemd template only after reviewing the actual VPS2 environment and service
constraints. Do not edit `/etc/caddy/Caddyfile` or the Go unit just to test
candidate health. The Python source must never connect to `evcowbbe`.

From an isolated project checkout/venv with controlled secrets and an
independently verified DB target:

```bash
python scripts/dev.py doctor
python scripts/dev.py check
python scripts/dev.py schema-check
COW_ALLOW_MIGRATE_TARGET=cow_v2_isolated python scripts/dev.py migrate --confirm cow_v2_isolated
python scripts/dev.py run
```

Only after explicit validation of each gate should the service be installed
with systemd and inspected via loopback readiness/OpenAPI/negative auth tests.
Do not create a public Caddy route prematurely. A successful process start
is NOT complete FE parity, real SMTP, dispatch, payment, or HAL acceptance.

## Phase C: Existing FE contract proof

Export current Go server `/openapi.yaml` and candidate `/openapi.yaml`
into reviewed snapshots and run:

```bash
python scripts/compare_openapi.py old-go.yaml new-python.yaml
```

This conservative detector deliberately fails on missing and changed old
operations. Then execute behavioral parity tests with representative FE flows
on isolated datasets. Do NOT switch the public FE host until all existing
used routes, security/error behavior and state transitions are accepted.

## Phase D: HAL readiness

Keep `COW_HAL_WORKER_ENABLED=false` and `COW_HAL_BASE_URL` empty until the
Go HAL implementation exists and passes `cow-hal.v1` simulator + PostgreSQL
+ hardware acceptance. HAL deployment uses a separate unit/database. Never
flip the physical command worker on based on green source tests alone.

## Phase E: Actual cutover (separate authorization)

Before any Caddy/public endpoint switch, obtain: protected Go runtime/DB
backup and an actual successful restore test; exact Python candidate source
hash and migrations; verified PostgreSQL state, SMTP, route parity, all role
isolation and business lifecycle acceptance; a data-transition decision
(import/dual-run/maintenance), FE agreement and an explicit release window.

Switch only through a reviewed rollback-safe change. Check public health and
all FE flows afterward; never treat application rollback as database rollback.

No such release authorization, migration, change or VPS2 runtime inspection
was executed during the creation of this project package.
