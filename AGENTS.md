# EVCOWBBE operating contract (Django development backend, 2026-11-02)

## Authoritative state

Repository: `Transmogriffy-Global-Private-Limited/evcowbbe`; active development branch `main`.
The canonical backend is Django/Gunicorn, `evcowbbe.service`, at
`https://dev-evcowbbe.transev.site`, proxied to `127.0.0.1:18280` on shared VPS2.
The authoritative application DB is PostgreSQL `evcowbbe_django_next` (role
`evcowbbe_app`). The old Go DB `evcowbbe` and binary are historical emergency
recovery resources, **not** a second active service, and Go data is deliberately
not imported. The Django staging service is disabled. HAL/physical charging is
not certified; do not enable physical commands or expose an unverified gateway.

Production and development are different acceptance scopes. This is a
**development** service; unfinished FE/business/HAL work does not prevent safe
feature development. Do not describe this as a commissioned charging service.

## Source, runtime and change ownership

- `main` is authoritative for Django source; never write migration artifacts or
  secrets directly into the Git tree. Never force-push or delete another person's
  branches as part of cleanup. Make isolated tested commits for changes.
- `/srv/evcowbbe/source` is the VPS checkout, not the running working directory.
- `/srv/evcowbbe/python-releases/<commit>/` is a sealed immutable release.
- `/srv/evcowbbe/current-python` is the canonical runtime release pointer after
  the guarded adoption. Do not use `/srv/evcowbbe/current` to launch Go.
- `/etc/evcowbbe` holds protected runtime configuration. Do not print or commit
  environment values, passwords, tokens, or SMTP credentials.
- `/var/lib/evcowbbe-rehost/runtime.json` records the managed Django revision after
  adoption; `/var/lib/evcowbbe-cutover/state.json` retains historic Go-to-Django
  cutover evidence. Retain original Go rollback resources offline.
- `/usr/local/sbin/rehost-evcowbbe` is the single Django-aware operator after
  adoption, with matching tracked source under `ops/vps2/`.
- Caddy, PostgreSQL cluster, shared DNS and unrelated projects are shared. Never
  alter them wholesale. Clean only explicitly identified EVCOWBBE test resources.

## Required change workflow

Read `docs/VPS2_CURRENT_OPERATIONS.md` before operational changes. Distinguish
`status` and dry-run audits from mutating commands. Require positively verified
Git revision, clean worktree, immutable release, bounded resource use, service
identity and DB identity; use controlled systemd operations and health checks.
No automatic DB schema DDL at startup. The current rehost workflow deliberately
blocks revisions that change Django migration files until a reviewed migration
and database recovery workflow is provided. Do not bypass that gate.

Use Django migrations as the sole schema ledger. Preserve identity-plane
separation (Customer/Driver/Admin), authorization DENY precedence, idempotency,
audit history, money precision, safe failure, and encrypted outbox behavior.
Use `docs/BLUEPRINT_IMPLEMENTATION_STATUS.md` and the retained normative
blueprint for unfinished business areas. API path-level parity is not proof of
business behavior parity. OpenAPI is public at `/openapi.yaml` and
`/api/openapi.json`.

## Role modules and change ownership

This repository has one Django app and one centrally managed ORM schema. Feature modules
`cow/users/`, `cow/drivers/` and `cow/admin/` own role-specific HTTP routes while `cow/api.py` composes
the single NinjaAPI. See `docs/ROLE_MODULE_ARCHITECTURE_20261105.md` for the
ownership map, contracts, branch workflow, tests and non-activation boundaries.
`cow/common/` owns shared HTTP contracts/errors while `cow/services/`,
`cow/domain/`, central models/migrations and HAL own their specific shared
business concerns. Do not duplicate shared auth, wallet, GST, tariff,
dispatch or HAL logic.
The `main` branch remains the baseline for the deployed development service;
feature branches are isolated source-development checkpoints and require review
before any merge or runtime adoption.

## Cleanup and rollback

Clean only explicitly enumerated former EVCOWBBE scratch DBs. Confirm service,
DB owners, absence of DB sessions, per-DB backup and `pg_restore --list` before
`DROP DATABASE`. Keep `evcowbbe_django_next` and `evcowbbe`. Never terminate
arbitrary PostgreSQL sessions. Archive old test files only after checking
systemd and runtime dependencies. Do not delete recovery backups/journal just
because a new deployment tool exists. Reverting to Go requires explicit
acknowledgment of Django/Go write divergence and a reviewed recovery plan.
