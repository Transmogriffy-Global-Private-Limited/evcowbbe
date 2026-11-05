# EVCOWBBE four-boundary architecture and ownership (2026-11-05)

## Source of truth

**One Django application:** `cow`; **one PostgreSQL schema ledger:**
`cow/migrations/`; **one API instance:** `cow/api.py`; **one authoritative**
identity, authorization, idempotency, pricing, wallet and HAL domain.

| Capability | Canonical file or directory | Public route prefix | Ownership |
| --- | --- | --- | --- |
| Customer/user | `cow/users/router.py`, `master.py`, `charging.py`, `contracts.py` | `/api/v1/app` | junior / user contributor |
| Driver | `cow/drivers/router.py` | `/api/v1/driver` | driver contributor |
| Admin controls | `cow/admin/router.py`, `controls.py`, `jobs.py` | `/api/v1/admin` | backend/core owner |
| Admin fleet | `cow/admin/fleet.py` | `/api/v1/admin` (separate authenticated router) | backend/core owner |
| Common HTTP types and errors | `cow/common/contracts.py`, `cow/common/errors.py` | internal only | backend/core owner |
| Shared stateful services | `cow/services/` | internal only | backend/core owner |
| Shared pure business rules | `cow/domain/` | internal only | backend/core owner |
| Central models/migrations | `cow/models.py`, `cow/models_zone.py`, `cow/migrations/` | internal only | backend/core owner |
| Auth/plane/OTP route assembly | `cow/api.py`, `cow/services/auth.py` | `/api/v1/{app,driver,admin}` | backend/core owner |
| HAL | `cow/hal/` | internal signed facts only | backend/core owner |
| Public contract | `cow/api.py`, `cow/fe_openapi.py`, `config/urls.py` | `/openapi.yaml`, `/api/openapi.json` | backend/core owner |

### Where a change belongs

- Customer form, address/EV/profile, user endpoint, app-side orchestration -> `cow/users/`.
- Driver availability/acceptance UI endpoint -> `cow/drivers/`. Actual eligibility,
  dispatch admission and service/job transitions -> `cow/services/`.
- Admin review/approvals, control-plane endpoint -> `cow/admin/`.
- Shared response/error type -> `cow/common/`; shared business policy ->
  `cow/domain/`; shared transactional use case -> `cow/services/`.
- A new table/column/index -> central `cow/models*.py` and `cow/migrations/`.
- A physical charging action -> `cow/hal/` plus authorized shared service,
  never directly from an untrusted role route.

No role may introduce its own `models.py`, `migrations/`, NinjaAPI,
pricing engine, wallet ledger, tax authority or authentication bypass.
Existing imports `cow/customer_api.py`, `cow/customer_contract.py`,
`cow/admin_controls.py`, `cow/fleet_api.py`, `cow/api_contracts.py`,
`cow/errors.py` are compatibility shims only, not development entrypoints.

### Known boundary decisions

- A feature branch and package are **not security containers**. Repository
  authorization and branch review controls must be separately enforced.
- The existing fleet API uses a distinct authentication backend; do not
  silently collapse or reauthenticate it while rearranging source.
- Four legacy customer quote/request/job routes preserve prior behavior for
  compatibility. They are NOT authorized to issue a binding zone-priced quote
  or debit a wallet without the separately reviewed commitment workflow.
- All existing public path/method/security/status/error/idempotency and
  OpenAPI operation IDs must remain identical. A source move only is not
  permission to change business semantics.
- Zone tariff/GST is **not activated** by this change. Migration `0008` stays
  committed in the feature base and remains undeployed.
- No `main` merge, VPS2 migration or HAL physical-charge command follows from
  applying this refactor.

### Contributor routine

1. Start from reviewed `integration/django-core`; work on a role-specific
   branch, with changed files inside that role directory and tests.
2. For a core requirement, propose a typed API/service contract and tests;
   core owner implements centrally; never fork the business rule inside a role.
3. Keep positive role/authorization, request validation, lifecycle, retries,
   money precision, audit, replay and time-window checks in shared services.
4. Run role and shared test suites and capture before/after OpenAPI JSON parity
   for structural changes.
5. PR to integration, core review, rebase only after integration changes;
   keep `main` untouched until separately approved for release.
6. Do not migrate, deploy, rehost, contact real chargers or process customer
   funds as part of local source restructuring.

Read `cow/{users,drivers,admin,common}/README.md` and corresponding AGENTS.md
before editing any module. `README.md` and root `AGENTS.md` link here.
