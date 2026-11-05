# Admin feature boundary

Canonical route entry `cow/admin/router.py`; fleet registered separately to preserve auth.
See `cow/admin/README.md` and central architecture guide. All writes require shared RBAC, transactions, idempotency, audit. Do not duplicate pricing, wallet, HAL or DB policy here.
