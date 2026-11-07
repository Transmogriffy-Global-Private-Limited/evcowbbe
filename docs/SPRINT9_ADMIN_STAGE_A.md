# EVCOWBBE Sprint 9 - Admin directories and pricing group lifecycle (Stage A)

**Status:** source patch, not live until installed, verified and rehosted. Based on reviewed `main`/`anubhab-work` commit `6a5c176936e091700e1a6f5d8d3f4096d9981923`.

## Domain boundary

- Core/Admin owns **Admin views** over Customer/Driver identity and existing commercial pricing-group membership. No Customer-owned form logic is implemented here.
- Customer and Driver IDs in the new Admin directories are **Principal UUIDs** (plane-specific actor identities). Their `profile_id` is a *separate* UUID and may be null for an enrolled user with no profile yet.
- `/commercial/groups/members/{customer_id}` was already shipped in Sprint 8. Its `customer_id` parameter is a **CustomerProfile UUID**, not the Principal UUID. Stage A's new removal route names this explicitly.
- Group controls are **pricing Groups**. They are not Admin roles, customer CRM segments, driver fleets or notification audiences.
- No model, migration or database schema change is introduced by Stage A.

## Implemented Stage A endpoints (pending local verification)

| Method | Full path | RBAC | Function |
| --- | --- | --- | --- |
| GET | `/api/v1/admin/customers` | `users.read` | Bounded directory, status/email exact filters and UUID keyset cursor. Includes principals without CustomerProfile. |
| GET | `/api/v1/admin/customers/{customer_id}` | `users.read` | Customer identity, optional profile and saved EV/address counts. |
| GET | `/api/v1/admin/customers/{customer_id}/jobs` | `users.read` + `jobs.read` | Bounded jobs summary, not invoice or payment detail. |
| GET | `/api/v1/admin/drivers` | `drivers.read` | Bounded driver principal directory, including pending applicants without DriverProfile. |
| GET | `/api/v1/admin/drivers/{driver_id}` | `drivers.read` | Driver identity, operational profile when present, active-duty count (not a dispatchability guarantee). |
| GET | `/api/v1/admin/commercial/groups/{group_id}` | `pricing.read` | Pricing Group details and active-member count. |
| GET | `/api/v1/admin/commercial/groups/{group_id}/members` | `pricing.read` + `users.read` | Active/current or history records with Group-scoped keyset cursor. |
| POST | `/api/v1/admin/commercial/groups/members/{customer_profile_id}/remove` | `pricing.manage` | Close effective membership, preserve ledger and audit. Idempotency required. |
| POST | `/api/v1/admin/commercial/groups/{group_id}/deactivate` | `pricing.manage` | Block while Group has active members or open tariff assignments. Audit. |
| POST | `/api/v1/admin/commercial/groups/{group_id}/reactivate` | `pricing.manage` | Restore Group active flag without modifying past memberships. Audit. |

Writes require `Content-Type: application/json`, `Idempotency-Key` (16-128 printable ASCII chars), and JSON `{"reason":"Verified operational reason"}`. No arbitrary status PATCH or force deletion is supported. Error envelopes, existing auth, and audit are reused.

## Pagination and identity

- `limit` 1..100, default 50, stable UUID-keyset `before` pagination for Customer, Driver and Jobs (return `next_cursor`).
- Group membership keyset uses `(starts_at DESC, id DESC)`; `before` is an opaque membership UUID belonging to the queried Group, not a separate timestamp.
- An email filter is exact and lowercased; no broad search or unbounded PII export. Membership history needs both `pricing.read` and `users.read`.

## Not yet implemented (explicitly not live)

- Admin Customer suspension/reinstatement: needs policy, sessions revocation and active-job safety guards.
- DriverApplication reviews, KYC, onboarding and suspension/offboarding: needs new core state/evidence models and release gates. A Driver's `eligibility` string alone is not KYC.
- Shared support domain, notifications inbox and replayable SSE: require the new append-only schema, transactional events, and authenticated audience-safe delivery from Sprint 9 Stage B; do not ask junior to invent them.

## Junior-facing ports and no-crossing rule

Junior owns all Customer-only routes/models/validation/orchestration in `cow/users/` (or under reviewed core-managed shared models when central schema changes are required). Junior must not edit `cow/admin/`, `cow/drivers/`, core shared services, canonical schema, migration ledger, pricing/GST, auth, ticket engine or SSE transport. Ports will be published from our core as tested functions plus `/api/v1/app/*` contracts; the junior implements ordinary Customer behavior within his domain and consumes cross-domain capabilities. A proposed port is **not** a live API.

## Release gates

1. On `anubhab-work`, clean baseline at `6a5c176936e091700e1a6f5d8d3f4096d9981923`, run installer; inspect diff.
2. Python syntax + schema parity + Django check + Admin/Commercial/Zone Swagger, static and pure tests.
3. Isolated PG17 integration including `test_admin_management_pg.py`, RBAC deny, idempotency, group lifecycle, historical data retained.
4. Commit/push `anubhab-work`; reviewed fast-forward `main` and rehost locally. **No migration needed for Stage A.**
5. Probe local Swagger/JSON/YAML and all ten new paths. Do not deploy by copy-pasting to a historical VPS.

## Next slice (Stage B, our ownership)

A single new central `0011` migration plus Admin DriverApplication/KYC, Admin/Driver support and shared notification/SSE core. Follow normative annexes 10, 13, 25, 26, 38, 39, 41-44. Support ticket ownership is by `Principal` *and plane*, immutable message/history, audited Admin actions, no sensitive bodies in notification payloads. Realtime ticket is single-use/short-lived and bound to session; stream replay is durable and resync-aware. Stage B must have isolated PostgreSQL and stream lifecycle tests before promotion; no empty stubs should be presented to junior as functional APIs.

### Suggested local commands

From the repository root after installation:

```powershell
& .\.venv\Scripts\python.exe -B "$HOME\Downloads\evcowbbe-sprint9-stage-a\tools\verify_offline.py"
```

For PostgreSQL acceptance, use the bundled `tools/verify_pg_stage_a.ps1` only
inside a new PowerShell 7 child with inherited `PG*` keys stripped, as in the
conversation command; do not run `manage.py migrate` on persistent Aiven. The
bundled script derives from the previously reviewed isolated-PG17 harness,
with its branch preflight updated to `6a5c176...` and its verified test actor
fixture corrected. It executes the full integration suite, including the three
Stage A tests, before any source promotion.
