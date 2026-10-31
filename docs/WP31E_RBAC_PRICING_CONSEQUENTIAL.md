# WP3.1E (targeted): consequential Go RBAC and pricing port

## Authority and scope
- Source reference: Go `main` `070cd06514b4d63b29aaa12ff115d08c9551d0c8`, `internal/adminrbac/service.go`, `internal/pricing/engine.go`, `internal/pricing/temporal.go`, and `internal/pricing/catalog.go`.
- Python predecessor: `5f6ba044e53a454492a435fe7dd197443d1f8f69`.
- This release is not an FE cutover or activation of commercial billing. It changes administrative RBAC authorization and published tariff-window topology; it adds a complete pure money evaluator and explicit trusted-policy resolution service.

## Behavior carried over
1. RBAC mutation actor's verified `ACTIVE` eligibility and `system.settings.manage` permission are re-read inside the advisory-locked transaction. Direct service calls cannot bypass it. Any remaining verified active manager is required after modification. The last active verified Full Admin is protected, as in the Go source. Malformed reasons containing control characters are rejected. Pricing writes also revalidate `pricing.manage` after the same global RBAC lock.
2. Financial policy HTTP mutations reject duplicate JSON keys recursively before Pydantic parsing, nonfinite JSON values, and payloads over the reviewed 32 KiB Go API request bound. Pricing rule validation forbids duplicate base components, multiple overrides for one base, invalid identifiers/conditions, negative rates, discounts above 100%, and invalid modifier targets.
3. Exact decimal paise arithmetic follows Go for BASE, override, multiplicative and percentage discounts, ordered rules, conditional facts, half-up rounding, tax class aggregation, and line-item sum. `FINAL` rejects estimates; both modes reject missing measurements, missing condition dimensions, unknown tax decisions, and int64 overflow.
4. Scope precedence is server-supplied only. Within a scope, deepest bounded window wins, then latest eligible open-ended, then root. Strictly nested bounded publication is allowed; crossing/identical bounded, duplicate root, and identical open-ended starts are rejected in both service and SQL trigger. Migration 0007 is additive and changes only the existing pricing trigger function body, not table shape.

## Hard separations and limits
- No tax rates, customer group memberships, route distances, charging measurements, electricity prices, or revenue policies are invented.
- The real `jobs.create_quote` still uses its existing, intentionally distinct legacy active pricing flow. This package does **not** call the new evaluator from the customer quote endpoint because approved tax and trusted scope/evidence providers do not exist yet. This separation is fail-safe, NOT proof of end-to-end billing parity.
- `pricing_engine.evaluate` is a domain calculation, NOT a payment authority, and accepts already trusted policy/evidence/tax snapshots. A caller must establish provenance and authorization separately. No public endpoint can supply arbitrary precedence or approved tax maps.
- Existing Go FE parity, separate production SQL schema/data, SMTP, and physical HAL conformance remain separate gates.
- Neither installer nor unit tests run DB migrations, reset data, deploy services, or alter `main`.

## Acceptance plan
1. Run the installer against the exact clean Python branch base (no cosmetic lint gate).
2. Execute unit tests, Django checks and `makemigrations --check --dry-run --noinput`, and inspect `git diff`.
3. On the marker-protected disposable VPS2 test database at ledger 0006, create and verify a fresh pre-0007 PostgreSQL backup and perform only `scripts/run_pg_tests.py --upgrade-0006-to-0007` using its limited role. Re-run `--existing` only after confirming 0007 applied.
4. Verify the new nested topology integration, transaction RBAC regression, existing PostgreSQL catalog, migration replay and all previous fleet, identity, customer tests. Reconcile source/acceptance documentation with observed results.
5. Do **not** merge to `main` or repoint Caddy yet.

## Recovery
If migration fails, keep the disposable DB unchanged and inspect actual ledger/status; do not blindly rerun or reset. The 0007 reverse migration restores the earlier stricter guard without dropping pricing data. Any new nested publications must be deliberately reconciled before a rollback to the old semantics. Preserve approved DB backup and retain Go rollback separately.
