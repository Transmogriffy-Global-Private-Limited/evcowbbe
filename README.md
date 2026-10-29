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

## Deployment acceptance gate

1. Reconcile actual VPS2 systemd unit (`evcowbbe.service` in tracked Git versus `evcowbbe-dev.service` in one handoff), listener, rehost program and FE calls.
2. Complete old 74-path/82-operation OpenAPI behavioral parity, then the remaining project domains, with negative/security/concurrency/integration tests on real PostgreSQL 18.
3. Rewrite the existing `rehost-evcowbbe` workflow for an immutable Python release and Gunicorn process while keeping same operator command, rollback journal, host checks, service identity, listener, and shared Caddy isolation.
4. Agree FE maintenance interval and data-loss scope. Verify an actual restored pre-reset backup against an isolated target before performing an explicitly authorized reset of **only** CoW objects in the existing `evcowbbe` DB.
5. Apply reviewed Django migrations, reactivate Python, check full FE acceptance; roll back Go code **with its paired Go DB snapshot** if necessary. Never claim binary-only rollback is sufficient after schema reset.

The Go HAL remains independent and connects later through the stable signed command/fact adapter. Simulator coverage reduces regression risk, but real Go HAL/network/charger acceptance is still required.
