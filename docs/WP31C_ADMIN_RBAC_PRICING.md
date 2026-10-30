# WP3.1C: Admin RBAC and tariff revision parity (source candidate)

Baseline: `47db590194bef8d4af322350e378501d27ef2d29` on
`python-django-replacement-20261029`. Normative sources:
`docs/reference/CoW_normative_blueprint_2026-09-24.zip`, Annexes 36 and 44;
Go-facing `docs/contracts/auth.openapi.yaml` Admin RBAC/Pricing routes.

## Scope

- Exact Annex 44 capability namespace and all seven default role bundles.
  Explicit DENY supersedes direct ALLOW and role defaults. No implied capability
  inheritance. Existing historical `charging.manage` is replaced by the
  normative `charging.command` requirement at the charging command endpoint.
- Admin own RBAC snapshot, catalog, account lookup, cross-account role grant
  and revoke, and capability override set/remove, with reason, audit, self-change
  refusal and last-active-FULL_ADMIN protection. Role/override changes are
  transactional and serialized by a PostgreSQL advisory lock.
- Admin tariff draft creation with JSON-string decimal rates (max six decimal
  places), validated rule graph, auditable reason, unique key/version,
  UTC-aware effective windows and strict request shape. Every draft stays
  immutable; changes require creating a new revision.
- Admin tariff list, get, audit history, versioned publish/withdraw with ETag
  and Idempotency-Key, same-scope half-open interval overlap rejection, and
  a database trigger that protects lifecycle and immutable fields against
  direct SQL mutation. Concurrent publishes serialize by exact scope.
- New revisions use `DRAFT`, `PUBLISHED`, `WITHDRAWN`. Existing legacy `ACTIVE`
  policies remain untouched. New `PUBLISHED` policies are **not** consumed by
  the legacy quote engine. No billing activation, tax approval, invoice,
  payment, or real pricing evaluation is implied.

## Migration and acceptance

Django `0005_admin_rbac_pricing_parity` is additive. Legacy pricing rows retain
NULL `policy_key`, `policy_version`, and `created_by`, with default empty scope
and rules. No old migration is edited. A dedicated `--upgrade-0004-to-0005`
mode on the positively identified disposable PostgreSQL acceptance database
checks exact pre-upgrade ledger/schema and that application tables are empty;
then applies `0005` and runs the full integration suite. `--existing` checks
`0005` and reuses it without DDL. `--fresh` is exclusively for an operator-
approved, **empty** disposable schema. `--upgrade-0003-to-0004` still targets
only `0004`, preserving its historical-data test.

These commands must **never** be run against production `evcowbbe` before the
separate full cutover authorization. On failure, preserve logs and current
schema; do not reset or retry blindly.

## Evidence boundaries

Pure Pydantic contract tests, Python compilation and static gates can execute
on a Windows development workstation without PostgreSQL. Django system checks,
`makemigrations --check`, and all integration tests require the user's venv.
**Do not claim a PostgreSQL PASS before VPS2 reports it**. Even when the full
scope passes, Go frontend behavior, business tax decisions and production
readiness remain separately gated.
