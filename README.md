# TransEV Charger on Wheels backend (EVCOWBBE)

**Canonical backend:** Django (Python), one Django app (`cow`), one canonical ORM schema (`cow/models.py`) and append-only migrations (`cow/migrations/`). `main` is the reviewed source baseline; `anubhab-work` is core/backend development; `abhranil-pal-evcowbbe` owns reviewed User module changes.

**Development environment (2026-11-06):** The former shared VPS2 deployment and its databases/services/backups were permanently decommissioned. Do **not** use old `dev-evcowbbe.transev.site` URLs, VPS2 deployment scripts or the former PostgreSQL DB. Work locally against an explicitly configured development PostgreSQL database. Source commits are not runtime deployments.

## Implementation state

- Django identity planes: Customer, Driver, Admin; Admin RBAC/deny, auditable writes, idempotency and session security foundations.
- Fleet/charger/customer master and charging request/job foundations.
- Pricing engine, versioned policies, Zone radius rules, duty/charger Zone assignments, Zone-specific GST decision registry and composite tariff evaluator.
- **Sprint 7:** Admin Zone/commercial operations, migration `0009_zone_admin_operations`, and an explicitly non-authoritative scenario preview. See [Sprint 7 operations](docs/SPRINT7_ZONE_ADMIN_OPERATIONS.md).
- **Commercial machine v1 (Sprint 8):** New reusable tariff/GST library and target assignments at `/api/v1/admin/commercial/*`, with nonbinding itemized preview and additive migration `0010`. Isolated PostgreSQL acceptance passed on 2026-11-07; developer DB migration and local `main` rehost are separate actions. Older `/api/v1/admin/zones/*` configuration remains for compatibility.
- Not complete/activated: real route/GPS evidence, QR proof, driver dispatch authority, binding customer Zone quotes, payment/ledger settlement, and physical HAL acceptance. Business correctness is not established by Go path parity alone.

## Structure and ownership

- `cow/users/` owns Customer HTTP routes, `cow/drivers/` Driver routes, `cow/admin/` Admin routes; `cow/api.py` composes them.
- `cow/services/` owns stateful transactions/invariants, `cow/domain/` pure rules, `cow/common/` shared response/error contracts, `cow/hal/` hardware integration.
- `cow/models.py` is the only ORM model registry; `cow/migrations/` the migration ledger. Do not fork schema in role modules.
- Go implementation/history and older VPS2 runbooks are historical reference only; do not reactivate them without a separately approved decision.

## Local development

Use Python 3.12-3.14, PowerShell 7+, your private gitignored `.env` (or a securely configured process environment), and a **separate** development PostgreSQL database. Do not print, paste or commit credentials or authentic customer data. All Django commands needing settings require `DJANGO_SETTINGS_MODULE=config.settings` and the env values checked by `config/legacy_env.py` (including both protected peppers).

```powershell
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
.\scripts\db.ps1 check
```

The migration baseline through `0008_zone_commercial_registry` was reset and installed on the developer's existing PostgreSQL development database. Sprint 7 adds the **forward** migration `0009_zone_admin_operations`; it is not a reason to reset again. After establishing/validating the target from your existing environment, apply via `python scripts/migrate_aiven_dev_0009.py` (which validates the reviewed development database identity before calling Django migrate) under explicit developer authorization. Do not run against an uncontrolled or production connection.

For an independent integration acceptance environment, `scripts/db_isolated_pg.ps1` provisions an isolated local PostgreSQL 17 cluster, migrates and tests without mutating other server instances. `scripts/run_pg_tests.py` also supports an operator-prepared, positively marked disposable PostgreSQL test database; its safety marker must never be bypassed merely to point tests at an application database.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_*.py' -v
.\scripts\db_isolated_pg.ps1 -RepositoryRoot (Get-Location).Path
```

The first command requires a valid local Django test environment to import Django-powered modules; missing settings/secrets are setup failures, not a passed test suite. Unit and database suites are separate verification gates.

## API and frontend

Authenticated Admin Zone endpoints are under `/api/v1/admin/zones*`; use the API's generated `/openapi.yaml`, `/api/openapi.json` and Swagger for exact contracts when a development server is running. Do not advertise an offline or deleted VPS2 endpoint as publicly live. Preserve existing API operation IDs and auth semantics unless a reviewed API change explicitly updates them.

See [Role module architecture](docs/ROLE_MODULE_ARCHITECTURE_20261105.md), [schema lifecycle](docs/DATABASE_SCHEMA_WORKFLOW.md), and [Sprint 7 controls](docs/SPRINT7_ZONE_ADMIN_OPERATIONS.md). Historic `docs/VPS2_CURRENT_OPERATIONS.md` is not current operating instruction.
