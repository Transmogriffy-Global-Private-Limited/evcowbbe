# Common module: shared HTTP primitives and cross-cutting contracts

- `contracts.py`: canonical shared API response DTOs and error envelope schema.
- `errors.py`: canonical `DomainError` / `reject` application error boundary.
- `cow/services/`: canonical shared stateful application services (including
  authentication, RBAC, pricing, idempotency, dispatch/job workflows).
- `cow/domain/`: shared pure rules and composite commercial contracts.
- `cow/models.py`, `cow/migrations/`: **central schema**.
- `cow/hal/`: centrally controlled charging hardware integration.

`common` is *not* a second Django app, not a second business ledger, and not a
place to duplicate role-specific handlers. Shared identity route wiring
currently lives in `cow/api.py` and is common/core-owned until a separately
validated move. These locations are intentional, not undocumented exceptions.
New cross-cutting request/response interfaces belong in this package; shared
business services remain in `cow/services/`, where callers already depend on
those established interfaces. Any future relocation requires an import/API
compatibility migration rather than blind copying.
