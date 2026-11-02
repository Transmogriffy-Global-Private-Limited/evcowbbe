# Go-to-Python HTTP behavior evidence

Generated 2026-11-02 from the review worktree. This is isolated, in-process
evidence only. It did not open a listener, access a PostgreSQL server, call
SMTP, or contact the running Go service.

## Pinned reference examined

- Go implementation: `1e966a2ce29bc2d1ef3e25500ee474c1ab380853`.
- Published Go OpenAPI: `070cd06514b4d63b29aaa12ff115d08c9551d0c8`.
- The implementation's `internal/api/server.go` was used for the JSON body,
  opaque-token, envelope, and CORS rules. Published OpenAPI was treated as a
  declaration, not proof of runtime behavior.

## Executed behavior cases

`tests.test_go_wire_http` executed four in-process HTTP cases. The companion
catalog and checker self-tests brought the focused run total to 16 tests.

| Surface | Positive evidence | Negative/boundary evidence | Result |
| --- | --- | --- | --- |
| `GET /health/live`, `GET /version` | Exact `data` envelopes are returned. | N/A | PASS, isolated client |
| Standard response headers | `X-Request-ID`, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, and `X-Frame-Options` were asserted. | N/A | PASS, isolated client |
| Public Go JSON endpoints | `application/json` requests pass the middleware entry gate. | Non-JSON returns 415; decoded body over 32 KiB returns 400. | PASS, isolated client |
| Go CORS routes | Allowed preflight gives 204 and the exact CORS allowlist headers. | Unknown origin returns 403. | PASS, isolated client |
| Auth wire primitives | A 43-character URL-safe opaque token is accepted by the shape validator; valid UTF-8 byte-length password boundary is accepted. | Bad token shape and a password over the byte-length limit are rejected. | PASS, isolated client |

## What this does not establish

The following operations were not dynamically demonstrated against an
independently running pinned Go server and the Django service with equivalent
seeded state. They remain **UNVERIFIED**:

- All 82 endpoint success bodies and status codes as a differential suite.
- Login, verification, recovery, 2FA, refresh rotation/replay, logout, and
  logout-all persistence and error envelopes.
- Customer/driver/admin wrong-plane authorization, DENY precedence, role
  changes, and last-admin protection.
- Fleet, pairing, connector, maintenance, pricing, idempotency, ETag,
  `If-Match`, concurrency, audit, and historical-immutability behavior.
- Actual Django Ninja serialization of every response DTO under database-backed
  state.
- `/openapi.yaml` machine-client compatibility beyond the isolated route
  fixture.
- SMTP transport and any live service behavior.

The existing V2 middleware transformations and the focused tests are useful
regression evidence, but they are not a substitute for an isolated,
stateful Go-versus-Django differential HTTP rehearsal.

## Continuation evidence

The continuation added three source-backed cases and reran the focused suite:

- A Go-path method mismatch now produces the standard JSON error envelope
  (`405`, `METHOD_NOT_ALLOWED`) rather than Ninja's HTML response.
- A missing `verify-email` token reaches the domain layer and returns Go's
  `401 INVALID_CREDENTIALS`, rather than a generic request-schema error.
- Logout and logout-all no longer decode or validate ignored body bytes. This
  follows pinned `server.go`: `post()` validates method and JSON media type,
  while only `decode()` consumes and limits bodies. This exact authenticated
  body case still needs disposable-state bearer evidence.

`tests.test_go_wire_checker tests.test_go_wire_contract tests.test_go_wire_http`
ran 20 tests with `OK`. These are isolated behavior and checker tests, not a
complete 82-operation differential run.
