# Admin Fleet inventory and asset-management slice (source continuation)

Status: source implementation for Foundation 3, **not deployed or migrated**.

## Scope and evidence boundaries

Implements canonical Charger on Wheels Sections 12, 30.3, 33.4-33.6,
Annexes 11, 12, 15, 22, 39, 41, 42, 44 and relevant Program 4 burners.

This slice owns physical *master identities*: ServiceVehicle, Charger,
ChargerConnector, effective-dated vehicle/charger pairings, and recorded
maintenance windows. It exposes historical audit snapshots of Admin changes.
It does **not** implement DriverAssetAssignment, DutySession claims, active
reservations, HAL runtime capability/connectivity, dispatch certification,
asset-mismatch resolution or the conditional mobile energy-store model.
Those require Driver/Workforce, HAL and hardware/business decision gates.

The public `/openapi.yaml` and `/swagger/` are updated with the implemented
endpoints, schemas, capability requirements and mutation headers. No testing
through `Try it out` should be done on a live database without explicit
operator authorization.

## Admin HTTP API

Prefix `/api/v1/admin/`, valid Admin access bearer required:

| Route | Method | Capability | Notes |
| --- | --- | --- | --- |
| `vehicles`, `chargers` | GET | `fleet.read` | Filter `limit` (1-100), `after`, `q`, `status`, `active`; `next_cursor` |
| `vehicles`, `chargers` | POST | `fleet.manage` | Create master asset, 201 |
| `vehicles/{id}`, `chargers/{id}` | GET | `fleet.read` | Resource with `ETag: "<version>"` |
| `vehicles/{id}`, `chargers/{id}` | PATCH | `fleet.manage` | Partial metadata, `If-Match` required |
| `vehicles/{id}/history`, `chargers/{id}/history` | GET | `fleet.read` **and** `audit.read` | Latest 100 audit entries incl. pairing/maintenance |
| `vehicles/{id}/availability`, `chargers/{id}/availability` | GET | `fleet.read` | *Inventory evidence only*; HAL/dispatch `NOT_EVALUATED` |
| `vehicles/{id}/deactivate`, `chargers/{id}/deactivate` | POST | `fleet.manage` | `reason`, `If-Match`; reject when actively paired |
| `vehicles/{id}/reactivate`, `chargers/{id}/reactivate` | POST | `fleet.manage` | `reason`, `If-Match`; reject active maintenance/retired |
| `vehicles/{id}/maintenance`, `chargers/{id}/maintenance` | POST | `fleet.maintenance` | `starts_at`, `ends_at` RFC3339; reason, no overlap |
| `chargers/{id}/connectors` | GET, POST | `fleet.read`, `fleet.manage` | List or register connector |
| `chargers/{id}/connectors/{connector_id}` | PATCH | `fleet.manage` | Partial connector update with `If-Match` |
| `pairings` | GET, POST | `fleet.read`, `fleet.manage` | Filter `vehicle_id`, `charger_id`, `status`, `limit`; create active pairing |
| `pairings/{id}/end` | POST | `fleet.manage` | End active pairing with reason |

Every POST/PATCH requires JSON and a unique ASCII `Idempotency-Key` (16-128
characters without whitespace). Receipts are stored transactionally with the
resource mutation. Reuse of a key by the same actor and same method/path/body
returns the original successful response; different use is rejected with 409.
PATCH and lifecycle commands additionally require strong version `If-Match`.
`412 RESOURCE_VERSION_MISMATCH` means re-read the resource before retrying.

Master metadata changes are append-only audited with prior and resulting
snapshots. Pairings never overwrite historical relationships; a charger has
at most one ACTIVE exclusive pairing, enforced by a PostgreSQL partial index.
Maintenance windows are recorded and projected at read time; the reserved
`maintenance_status` metadata field is **not** an authoritative live sensor
reading and is not automatically updated when a window starts or ends.
Master `operational_status` and scheduled maintenance windows **do not**
represent live Driver claims, vehicle physical observations or HAL telemetry.
No synthetic operational availability should be shown as dispatch readiness.

Business/hardware decisions still needed before physical operation:
vehicle type/inspection requirements, charger models/protocols/connector
evidence, permanently mounted versus swappable configurations, safety
certification, HAL identity binding, mobile energy model activation.

## Migration and branch coordination

`003_fleet_inventory.sql` is reserved for this Fleet domain. Files in
`migrations/` and `internal/database/migrations/` must remain byte-identical.
`001` and deployed `002` are immutable. Coordinate subsequent version numbers
with the junior's Customer master-data branch before merging; do not let
parallel work create two different `003` migrations.

Migration `003` is additive, but schema activation is irreversible by the
binary rollback mechanism. Before applying to shared development or VPS2,
take and **verify an isolated restore** of a current backup, validate the
exact Git commit and target DB, inspect all new SQL, and explicitly authorize
`go run ./cmd/migrate up`. No startup migration is executed automatically.

## Verification matrix

From the Go 1.25+ development checkout:

```sh
go test -count=1 ./internal/fleet ./internal/api ./docs/contracts
go test -count=1 ./...
go vet ./...
go build ./cmd/server
git diff --check
```

The default suite validates pure domain/API/OpenAPI logic without external
services. **Real PostgreSQL acceptance is separate** and requires an isolated
pre-provisioned database named `*_test`, supplied through `TEST_DATABASE_URL`.
Run the fleet and HTTP integration tests with this variable only after checking
they cannot target the app database. These tests write and remove disposable
records. They cover duplicate/replayed idempotency keys, conflicting keys,
concurrent same-key requests, connector creation, exclusive pairing,
deactivation guard, overlapping maintenance rejection, version mismatch,
history, and capability denial.

When using one shared disposable test database for the whole Go suite, run
`go test -p 1 -count=1 ./...` (optionally with `-race`). Existing auth/outbox
integration tests from different packages can otherwise consume each other's
pending test emails, creating a cross-package test interference failure. The
source-only unit suite without `TEST_DATABASE_URL` has no such shared DB issue.

Remaining evidence before a shared-environment release:

- Run the real PostgreSQL integration tests and race suite against disposable DB.
- Test endpoint matrix with FE using an explicitly authorized test Admin and
  test assets, including simultaneous maintenance and pairing attempts.
- Confirm actual hardware vocabulary and safety policy with the operator.
- Extend future duty/asset claim integration before claiming dispatch-safe
  availability; do not promote this inventory view to a live availability feed.
- Validate staging backup, migration, deployment, authorization and rollback
  using the existing VPS2 manual runbook. **No deploy/push was performed.**

Recovery: a binary rollback never undoes `003`; incompatible schema must be
addressed via reviewed forward migration. Preserve backups and the original
environment secrets. Until rollout, the running Foundation 2 binary and VPS2
database are unchanged by this source-only work.
