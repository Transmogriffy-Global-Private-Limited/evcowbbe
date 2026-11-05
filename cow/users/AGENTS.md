# User feature boundary

Read `cow/users/README.md` and `docs/ROLE_MODULE_ARCHITECTURE_20261105.md` first.
Own user routes/contracts and role-specific orchestration here. Shared schema, auth, zone,
GST, price, payment, dispatch, HAL and migrations remain core-owned. Preserve
existing OpenAPI paths, operation IDs and security unless centrally approved.
Do not directly edit core or driver code without an reviewed integration change.
