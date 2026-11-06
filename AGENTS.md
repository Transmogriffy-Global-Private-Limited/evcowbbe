# EVCOWBBE engineering contract (local-first Django development, 2026-11-06)

## Current source and environment

Source of truth: `Transmogriffy-Global-Private-Limited/evcowbbe` GitHub `main`; core feature work on `anubhab-work`; customer module on `abhranil-pal-evcowbbe`. The shared VPS2 EVCOWBBE deployment, databases, backups and systemd/Caddy artifacts were deleted 2026-11-06. **Do not run historic VPS2 deployment/cutover, restore or rehost scripts** or revive Go as a runtime. Git branches were not deleted. Current developer DB is independently configured by its owner; never infer DB authority from a URL alone and never expose secrets.

## Code ownership

- `cow/models.py`: the only ORM model declarations. `cow/migrations/`: append-only, reviewed Django migration and trigger ledger. Never edit already-applied migrations or force a migration by falsifying `django_migrations`.
- `cow/services/`: shared transaction/invariant layer; `cow/domain/`: pure rules; `cow/common/`: shared response and error contracts; `cow/hal/`: separate hardware boundary.
- `cow/users/`, `cow/drivers/`, `cow/admin/`: role-specific HTTP controls, without duplicated business state or schema.
- Preserve admin RBAC DENY precedence, fresh transaction-level authorization, idempotency, concurrency, immutable financial policy snapshots, audit, exact decimal money, secure identity planes, and controlled error responses.
- New business features must be coherent vertical slices, not disconnected helpers. Proper database schema changes are permitted during development when source/migration/test/rollback implications are reviewed. Do not hardcode fictitious location, legal, route, payment or HAL authority.

## Sprint 7 architecture

Migration `0008` added ServiceZone, DutyZoneAssignment, ChargerZoneAssignment, ZoneGSTAssignment and ZoneTariffPayload. Migration `0009_zone_admin_operations` introduces single-published-zone protection and makes `PricingPolicy.rules=[]` valid **only** for composite Zone scopes, with a mandatory matching ZoneTariffPayload on publication. Generic pricing remains unchanged; Zone plans use the existing composite domain and pricing engine, never a duplicated ledger. Admin APIs are registered in `cow/admin/zones.py`, state mutations in `cow/services/zone_admin.py`, read-side resolution in `cow/services/zone_commercial_registry.py`. See `docs/SPRINT7_ZONE_ADMIN_OPERATIONS.md`.

## Verification and release

Read relevant README and architecture documentation before work. Keep changes on `anubhab-work` until reviewed; do not rewrite or force-push another developer's branch. Use `scripts/db.ps1 check` and the offline tests; use an approved isolated PostgreSQL acceptance harness for trigger/concurrency tests. Local dev PostgreSQL migration `0009` is a separate explicit action, not an application startup hook. Never run destructive tests against unverified shared applications or real customer data. Confirm schema, API docs, security, approval provenance, and legacy endpoints before merge.

No actual wallet debit/refund, customer-accepted commercial quote, physical charger command, legal tax inference or live GPS/QR authentication is authorized by the Admin Zone **scenario** preview. A manual Admin coordinate is not a verified location provider. Publish no claim of end-to-end commercial completion without the corresponding real evidence and transaction tests.
