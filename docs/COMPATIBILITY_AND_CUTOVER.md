# Existing frontend contract and protected VPS2 cutover

**The Python project does NOT replace the running Go API yet.** The FE developers
remain on the current Go release/DB. Read-only GitHub inspection on 2026-10-29
identified Go `main` commit `070cd06514b4d63b29aaa12ff115d08c9551d0c8`.
The VPS2 runtime and previous Go deployed binary SHA were NOT freshly queried.

## FE contract status

The published Go OpenAPI `docs/contracts/auth.openapi.yaml` has 74 path
entries including 3-plane auth/2FA, RBAC, vehicle/charger inventory, pairing,
maintenance, and admin pricing policies. The replacement implements a SUBSET
of those paths. A matching path name alone is NOT proof of response, status,
error or side-effect compatibility.

| Surface | Python status | Compatibility evidence |
| --- | --- | --- |
| `/health/live`, `/health/ready`, `/version` | source | no runtime acceptance |
| `/openapi.yaml`, Swagger | source | new generated contract; not old parity |
| App/Driver/Admin register/login/refresh/logout/me | source | contract shape needs comparison |
| 2FA, verify, reset, resend | source | real SMTP/DB not verified |
| RBAC | partial source | catalog and assignment behavior not equivalent to all Go routes |
| Fleet | partial source | maintenance/pairing/endpoints not yet ported |
| Pricing | partial source | full exact tariff engine/time rules not ported |
| Customer master data | new source | old Go contract not implemented on those paths |
| Service request/quote/job | partial source | staging-only design |
| Driver KYC/scheduling/dispatch | model only | not implemented |
| Payment/refund/GST/invoices | not implemented | do not expose as functional |
| Compensation, rating, support, notifications | not implemented | do not expose as functional |
| Go HAL physical acceptance | not implemented | conformance required |

## Hard compatibility gate

1. Fetch BOTH running Go live OpenAPI and candidate Python generated OpenAPI.
   Compare operation, methods, request/response types, status/error codes,
   nullable/required fields, identities, pagination, ETags and idempotency.
2. Run recorded FE integration requests on both systems against independently
   seeded and isolated databases. Diff meaningful results, not UUIDs/timestamps.
3. Exercise negative privilege and cross-plane cases, refresh races, SMTP,
   maintenance/pairing/history, monetary rounding, cursor pagination and audit.
4. Keep the same public Go endpoint until all FE-used operations pass.
5. Preserve a verified Go binary/DB rollback path; never perform a DB reset
   as if it were a code rollback. If data moves, obtain full dual-system
   consistency or a coordinated maintenance window and explicit approval.

There is **no** claim of zero post-HAL regression simply from the adapter.
The contract and simulator reduce the risks; hardware facts, Go implementation,
network failures, and all domain end-to-end scenarios must still be proven.
