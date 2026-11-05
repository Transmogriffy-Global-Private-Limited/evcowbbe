# Admin module: operational controls and review

- `router.py`: one Admin Router, mounted under `/api/v1/admin` by `cow/api.py`.
- `jobs.py`: existing Admin job listing and guarded charging command admission.
- `controls.py`: Admin RBAC and pricing policy authoring routes.
- `fleet.py`: existing independently authenticated fleet router. It remains a
  second `api.add_router('/v1/admin', ...)` at the same prefix **deliberately**,
  preserving the fleet security and OpenAPI contract. Its future consolidation
  requires explicit contract-equivalence tests.
- **NOT owned here:** underlying `cow/services/` rules, core domain, schema,
  migration ledger, wallet settlement, HAL physical state.

Do not bypass `rbac.require`, audited mutations, idempotency or concurrent
transaction boundaries. The moved endpoints retain the old import paths
through shims; new code imports `cow.admin.*`.
