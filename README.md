# TransEV Charger on Wheels backend (EVCOWBBE)

**Authoritative development backend: Django (Python) on GitHub `main`.**

The Go-to-Django development cutover completed on 2026-11-02 and was verified
through public HTTPS health/OpenAPI, real admin login/logout, the systemd
service, listener and release inventory. This is an **active development API**,
not a production-commissioned charging system. The Go implementation is retained
only in Git history and protected VPS backup/runtime resources for emergency
recovery. The old Go PostgreSQL database is not imported into Django.

## Development runtime

- **API:** `https://dev-evcowbbe.transev.site`
- **Public OpenAPI:** `/openapi.yaml` (YAML) and `/api/openapi.json` (JSON)
- **FE integration:** [frontend contract guide](docs/FRONTEND_INTEGRATION.md) and the `/api/swagger/` interactive explorer
- **Service:** `evcowbbe.service`, Gunicorn/Django, 127.0.0.1:18280 through Caddy
- **Database:** PostgreSQL `evcowbbe_django_next`, Django migrations, owner `evcowbbe_app`
- **Email:** guarded, timer-driven Django encrypted outbox worker
- **Source of truth:** this repository `main`
- **VPS source:** `/srv/evcowbbe/source`
- **Immutable releases:** `/srv/evcowbbe/python-releases/<40-hex-commit>/`
- **Canonical release pointer after adoption:** `/srv/evcowbbe/current-python`
- **One deployment operator after adoption:** `/usr/local/sbin/rehost-evcowbbe`

See [`docs/VPS2_CURRENT_OPERATIONS.md`](docs/VPS2_CURRENT_OPERATIONS.md)
for the project-only operating, deployment, cleanup and recovery procedures.
The operator is tracked under [`ops/vps2/`](ops/vps2/).

## Implementation state and safety

Implemented foundations include three authentication planes, admin RBAC,
fleet/vehicle/charger management, customer profile, pricing policy,
service requests/jobs, auditable operations, time-bound pricing logic,
encrypted outbox and versioned HAL command/fact boundaries. Some business
workflows, frontend integrations, pricing/payment and physical charger
interactions remain incomplete. Read
[`docs/BLUEPRINT_IMPLEMENTATION_STATUS.md`](docs/BLUEPRINT_IMPLEMENTATION_STATUS.md)
and the retained normative blueprint for exact feature status and requirements.
Do not enable physical HAL commands without separate acceptance.

The Go-to-Python wire-compatibility work is preserved under
`docs/verification/go-python-parity-20261102/`. Static path/method equivalence
is not a claim of completed business behavior parity.

## Repository structure and onboarding

Read [Role module architecture](docs/ROLE_MODULE_ARCHITECTURE_20261105.md) before changing user, driver or shared business code. The user, driver and Admin role packages, plus shared `cow/common/`,
share a single ORM schema, security implementation and Django Ninja API.

## Active branches and ownership

| Git branch | Purpose |
| --- | --- |
| `main` | Canonical reviewed Django source baseline |
| `anubhab-work` | Backend/core owner changes |
| `abhranil-pal-evcowbbe` | Junior developer user-module changes |

There are **no other active branch heads** after consolidation. Routes live in `cow/{users,drivers,admin}/`; common HTTP contracts/errors live in `cow/common/`; services/domain/models/migrations remain central. See `docs/ROLE_MODULE_ARCHITECTURE_20261105.md` for the sole ownership map. Old Go reports and static parity tests are historical evidence; do not treat them as a second runtime. Git source updates alone do not migrate or deploy VPS2.

## Local development

Install Python 3.12-3.14 in a fresh venv. The authoritative dependency ranges
are in `pyproject.toml`:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

Ruff is optional for individual developers, not a test or deployment gate.

The app requires correctly scoped environment variables; do not commit or
print real secrets. Never point a test command at either the authoritative
Django database or the preserved Go database. The currently configured
PostgreSQL acceptance harness uses the separately protected
`cow_wp31a_20261030_test` fixture and its operator-owned marker, until
that harness is migrated to a newly reviewed disposable-database design.

## Git and operations

Commit and push reviewed Django changes to `main`; fast-forward the VPS
checkout and deploy via the single rehost operator. Do not mutate frozen
releases, revive the Go-only rehost, or install a second Django API service.
The tracked rehost operator presently supports only schema-identical updates:
changed migration files require a separately reviewed PostgreSQL migration and
recovery workflow, not a bypass. The original Go database/binary and historic
cutover journal remain offline for explicitly approved emergency recovery.

Historical candidate-stage instructions remain in Git history; do not follow
those instructions as current VPS2 operating truth. See repository `AGENTS.md`.
