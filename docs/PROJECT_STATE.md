# Project state — 26 October 2026

Scope: Initial human authentication foundation; three auth planes, email/password, email verification, rotating bearer token sessions, opt-in email OTP MFA, password change/reset, SMTP and templates, PostgreSQL schema. This replaces the previously emptied *local* Go repository tree, not the hosted GitHub repo or VPS2 runtime.

Source basis: Charger on Wheels sole-source blueprint 2026-09-24, normative annexes 11, 15, 17, 22, 44. No domain/business work implemented yet. Admin capabilities and driver operational approval are outside this slice.

Validation: Go source syntax and cross-package interfaces verified in the generation environment using isolated temporary API-compatible *stubs*. Real third-party modules cannot be downloaded here. Local Go 1.23 is insufficient for package's Go 1.25+ target, so real dependency build, PostgreSQL tests and SMTP acceptance must be completed on developer Windows as prescribed in scripts/continue-setup.ps1 after scripts/configure.ps1 (unit tests and optional separately provisioned live PostgreSQL integration acceptance). No release or Git commit/push executed by this package.

Risks/gates: No transactional mail outbox/retries, password reset notice best-effort, frontend CORS intentionally closed, no admin business privilege handlers yet, no production TLS proxy or operational deployment. Do not confuse compile-only stubs with true dependency tests.

## External-service setup change — 26 October 2026

At user request, removed all Docker and Mailpit dependencies. Configure existing PostgreSQL + genuine TLS-enabled SMTP through hidden prompts in `scripts/configure.ps1`, retaining previously generated peppers; follow with explicit `continue-setup.ps1` preflight/tests and interactive migration consent. No database or container is auto-provisioned. Added a read-only SMTP AUTH handshake probe and an opt-in real test-email sender; SMTP delivery and PostgreSQL integration remain unverified until run against user's services. Existing GitHub and VPS2 untouched.

## Current code continuation — 27 October 2026

Repository baseline `bf6ef5c` includes the live manual VPS2 deployment and Swagger/OpenAPI. This continuation adds encrypted transactional authentication-email outbox and first Admin capability-based RBAC (migration 002, seven API operations, explicit one-time offline role grant). API and SQL contract need fresh review against this patch. Compile/unit/contract tests are local checks; separately provisioned disposable PostgreSQL `_test`, real SMTP inbox delivery, recovery and backup/restore evidence are not implied by a green local suite. Do not automatically apply migration 002, push to GitHub or deploy to VPS2.

## Admin Fleet source sprint — 28 October 2026

Foundation 2 was reported successfully deployed to VPS2 from commit `58c36ba1986895edeb41dcb1f2fd267d66907efc`, with migration 002 and one-time full Admin grant. The sprint's scratch checkout began at `bf6ef5c` with pre-existing **uncommitted** Foundation 2 source edits, which must be preserved. The Fleet deliverable must be compared against published `58c36ba` so that only new Fleet changes are included; preserve separate publication and acceptance evidence.

New local-only source: migration `003_fleet_inventory.sql` (identical two-copy files), `internal/fleet` domain, Admin Fleet HTTP routes, embedded OpenAPI/contract tests and local verification. Scope covers master inventory and pairing/maintenance, **not** runtime HAL, Driver assignments/duty, dispatch, customer, payments, or production hardware certification. Verification and deployment status are maintained in `docs/FLEET_INVENTORY_AND_ASSET_MANAGEMENT.md`.

## Admin pricing policy source sprint — 28 October 2026

The operator reported Fleet `c0db398d106ae84cd01ceff7501fc4fb23d576ba` with schema 003 successfully active on VPS2. A new, isolated checkout at exactly that revision was used for Admin pricing work; the original local checkout and other developer changes were not touched. Extracted the **central design** of the New Go CMS tariff implementation: independently chosen tariff scope and effective dates, strictly nested temporal overrides, exact basis-based charging arithmetic, independent tax decisions and frozen terms. COW is **not hub based**, and its pricing consists of mobility/service and charging components with independent evidence.

Source-only addition: `internal/pricing` evaluation, scope/temporal resolution, immutable policy catalog, transactional publication, audit and Admin HTTP routes; two byte-identical `005_admin_pricing_rules.sql` migrations with an executable 004 prerequisite; six protected OpenAPI operations, tests and runbook. Existing RBAC `pricing.read/manage` capabilities are reused. No real tariffs, customer quotes, tax approvals, money movement, or HAL finalization were invented. The junior's User domain owns **migration 004** and it must be reconciled before pricing 005 is applied. Binary version marker becomes `admin-pricing-policy-1` **only when this source is subsequently deployed**.

Local Go 1.26.4 actual module-dependent unit/contract tests, `go vet` and server build succeeded in the isolated checkout. Disposable PostgreSQL lifecycle integration is coded but **not run** without a designated `_test` database containing 004. No Git commit, remote push, live database mutation or VPS2 deploy was authorized or performed for this pricing sprint. See `docs/ADMIN_PRICING_RULES_AND_TARIFF_ENGINE.md` for detailed behavior, approval gates, verification and recovery.
