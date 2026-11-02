# Go API parity gate execution handback

## 1. Objective and baseline

This work continued the existing V2 Go-to-Django parity candidate without
discarding any work. The authoritative behavior source examined was Go revision
`1e966a2ce29bc2d1ef3e25500ee474c1ab380853`; the published Go declaration was
examined at `070cd06514b4d63b29aaa12ff115d08c9551d0c8`. The historical V2
report (`docs/verification/go_python_contract_diff.json`) remains unchanged and
records 121 findings (45 request and 76 success-response findings).

## 2. Preserved repository state

- Root: `C:/Users/AnubhabDey/Programs/My_Programs/evcowbbe`
- Branch: `go-wire-parity-20261031`
- Starting and current HEAD: `760c64f17c3ecd196f95f6e344360ad22dc8c3a6`
- The existing V2 edits in middleware, auth/API/fleet/admin sources, V2 tests,
  `cow/go_wire.py`, and the historical report were retained.

No branch switch, reset, clean, stash, rebase, commit, push, production action,
VPS action, database action, Caddy/systemd change, Go service shutdown, or HAL
change was performed.

## 3. Files inspected

Repository and project authority documents, current Django routes/services/
models/migrations, V2 report and tests, the checker, generated OpenAPI, and the
pinned Go source were inspected. In particular, Go `internal/api/server.go`,
fleet/pricing/CORS/docs/auth/RBAC surfaces and the published auth OpenAPI were
used to distinguish source behavior from declaration-only evidence.

## 4. Files modified and purpose

| File | Purpose |
| --- | --- |
| `cow/api.py` | Declare and return concrete Go-compatible auth/profile/ack DTOs; publish opaque bearer format; enforce byte-oriented credential/token request boundaries. |
| `cow/services/auth.py` | Enforce fixed-width URL-safe opaque tokens and byte-length email/password limits; expose token metadata needed by the DTO. |
| `cow/customer_api.py` | Preserve the Python-only profile route while keeping the Go `/me` envelope narrow; use a reusable Ninja body marker. |
| `config/middleware.py`, `cow/admin_controls.py`, `tests/test_go_wire_contract.py` | Scoped lint/import corrections in existing V2 files. |
| `scripts/check_go_wire_contract.py` | Replace the route-name-only comparison with fail-closed recursive request/response/parameter/error/security/header comparison and historical-ledger output. Security schemes are compared by resolved semantics, not arbitrary class names. |
| `tests/test_go_wire_checker.py` | Controlled mismatch tests for nested constraints, parameters, error status, `$ref` siblings, nullability, mismatched security, and equivalent named bearer schemes. |
| `tests/test_go_wire_http.py` | Isolated HTTP regression evidence for diagnostics, headers, JSON limits/media type, CORS, token shape, and UTF-8 password boundaries. |
| `tests/test_wp31d_contract.py` | Correct its stale route count after the already-present V2 Python-only profile route. |

## 5. Historical 121-finding disposition

`RESOLUTION_LEDGER.csv` has one row for each historical finding, preserving the
original Go/Python declarations. It does **not** falsely close any item merely
because a declaration or middleware transform now looks closer.

- Historical discrepancies: **121**.
- Verified resolved historical discrepancies: **0/121**.
- 67 are identified as Python response-declaration deficiencies where V2
  supplied no response schema.
- 54 remain unresolved pending precise pinned-handler and dynamic evidence.

The current auth DTO corrections and checker normalization are implementation
progress, not a valid claim that the historic runtime discrepancies are closed.

## 6. Checker changes and self-tests

Checker version `2026-11-02.1` now compares recursively resolved JSON Schema,
including required fields, object/array/numeric/string constraints, nullable
composition, `$ref` siblings, parameters, all declared response statuses,
error envelopes, response headers, and effective security semantics. It also
records native diagnostic routes rather than pretending they are API operations.

The self-test suite deliberately injects differences and proves the checker
reports them. It also proves only semantically identical opaque HTTP bearer
schemes are normalized. The checker remains fail-closed and returns exit 1
while any current difference exists.

## 7. Static parity result

`PARITY_REPORT.json` was generated fresh by the checker. Results:

| Measure | Result |
| --- | ---: |
| Original Go operations | 82 |
| Django operations found for Go method/path pairs | 82/82 |
| Django total generated operations | 100 |
| Current static differences | 786 |
| Request schema differences | 39 |
| Success response/schema/header differences | 76 |
| Error response differences | 645 |
| Parameter differences | 23 |
| Native diagnostic declaration differences | 3 |

The earlier 79 bare security-scheme-name differences were removed only after
the checker compared the resolved scheme (`http`, `bearer`, `opaque`) and a
controlled test proved that genuine security differences still fail. The 786
remaining findings are retained; no broad exclusion/filter was applied.

`STATIC_SCHEMA_PARITY = FAIL`.

## 8. Dynamic HTTP result

Focused isolated HTTP evidence is in `HTTP_BEHAVIOR_RESULTS.md`. It covers
diagnostic envelopes, standard headers, media type, decoded 32 KiB limit,
allowed/rejected CORS preflight, opaque token shape, and UTF-8 byte boundaries.
It does not constitute an 82-operation differential run with matched state.

`HTTP_BEHAVIOR_PARITY = UNVERIFIED`.

## 9. Authentication and security result

The checked-in source and targeted tests establish only the listed wire
primitives and three-plane route catalog. Login/refresh rotation/replay,
wrong-plane behavior, authorization ordering, OTP, RBAC DENY precedence, and
durable audit outcomes were not run against a disposable PostgreSQL state.

`AUTH_SECURITY_PARITY = UNVERIFIED`.

## 10. PostgreSQL integration result

No positively identified and authorized disposable PostgreSQL database was
available. The local URL deliberately targeted an unreachable loopback port,
and `makemigrations --check` therefore emitted a history-check timeout warning
despite finding no model drift. No migration, flush, reset, test runner, or
database operation was run.

`DISPOSABLE_POSTGRES_INTEGRATION = UNVERIFIED`.

## 11. Python-only regression result

With documented Django settings and test-only values, `python -m unittest
discover -v` completed `79` tests with `OK`; focused parity tests completed
`16` tests with `OK`; `manage.py check` completed with no issues; and scoped
Ruff completed with no findings. A first discovery invocation missing
`DJANGO_SETTINGS_MODULE` failed one direct-Django test import; that failure and
the successful configured rerun are both recorded in `TEST_EXECUTION_REPORT.md`.

`PYTHON_ONLY_REGRESSIONS = PASS` for the executed isolated suite.

## 12. Documentation and frontend impact

The public OpenAPI remains materially divergent from the Go contract. The
machine-readable `/openapi.yaml` route exists but was not proven equivalent to
all frontend/machine-client expectations. Existing Python-only operations were
preserved and were not rewritten as Go operations.

`FRONTEND_CONTRACT_COMPATIBILITY = UNVERIFIED`.

## 13. Remaining problems

1. 786 static declaration differences remain, led by 645 undeclared/different
   error responses and 76 success response/header differences.
2. The historical 121 findings have no per-operation dynamic parity closure.
3. No isolated Go reference server run or stateful Go-versus-Django
   differential suite has been completed.
4. No disposable PostgreSQL rehearsal is available, so persistence,
   concurrency, migration, and authorization state cannot be accepted.
5. SMTP, live deployment, and physical HAL behavior remain outside this work.

## 14. Exact next safe action

Provision or identify an operator-authorized disposable PostgreSQL database
with its marker/least-privilege role, then run the repository’s fail-closed
PostgreSQL harness against that target only. Before mutating it, verify its
effective server/database/role/marker and migration history. In parallel,
bring up the pinned Go revision and Django against independently seeded
disposable databases and execute a stateful 82-operation differential suite;
do not apply any result to production.

## 15. Gate summary

```text
ORIGINAL_GO_OPERATIONS = 82
DJANGO_OPERATIONS_FOUND = 82/82

HISTORICAL_DISCREPANCIES = 121
HISTORICAL_DISCREPANCIES_RESOLVED = 0/121
CURRENT_SUBSTANTIVE_SCHEMA_MISMATCHES = UNRESOLVED (786 declaration-level findings)

STATIC_SCHEMA_PARITY = FAIL
HTTP_BEHAVIOR_PARITY = UNVERIFIED
AUTH_SECURITY_PARITY = UNVERIFIED
DISPOSABLE_POSTGRES_INTEGRATION = UNVERIFIED
PYTHON_ONLY_REGRESSIONS = PASS
FRONTEND_CONTRACT_COMPATIBILITY = UNVERIFIED
EXTERNAL_SMTP_ACCEPTANCE = UNVERIFIED
LIVE_DEPLOYMENT_ACCEPTANCE = NOT_PERFORMED
HAL_PHYSICAL_ACCEPTANCE = NOT_PERFORMED

CUTOVER_READY = NO
```

`EXTERNAL_SMTP_ACCEPTANCE` is unverified rather than failed because it was not
contacted. All prohibited operations remain not performed.

---

## Continuation correction and current result

This section supersedes the count/disposition assertions above where they
conflict. It preserves them as a dated checkpoint rather than rewriting the
earlier evidence.

### Evidence correction

The prior statement that `0/121` historical discrepancies were resolved was a
ledger-accounting defect. It used only the then-current checker IDs and marked
request-schema rows unresolved by rule, even where the original exact finding
had disappeared. The corrected `RESOLUTION_LEDGER.csv` reconciles all 121
historical records by operation, type, and status:

- **73/121 statically reconciled**: the exact current mismatch is absent.
- **48/121 unchanged or reclassified**: the exact difference remains.
- **0/121 dynamically verified as fully resolved**: no false runtime closure.

The previous 786 aggregate was recorded in the original handback, but its
machine-readable per-finding file was overwritten before this continuation.
It cannot honestly be mapped row-by-row from the remaining worktree. The
current, individually traceable report is
`PARITY_REPORT_CONTINUATION_20261102_FINAL.json`; its 723 rows are all present
in `CURRENT_DISCREPANCY_LEDGER.csv`. See `ROOT_CAUSE_GROUPING.md` for the
checkpoint identities and counting definitions.

### Corrections implemented

1. Declared middleware-owned `X-Request-ID` response headers and taught the
   checker to resolve reusable response-header references.
2. Added reusable strict acknowledgement DTOs, exact Go role/override enums,
   and retained the pricing `Draft` model at the HTTP boundary.
3. Corrected Go-path 405 responses into the standard error envelope.
4. Matched Go byte-oriented opaque-token/password behavior: input tokens are
   fixed 43-byte values without an invented alphabet restriction, and omitted
   action/refresh tokens reach the domain error path.
5. Corrected logout/logout-all after inspecting pinned `server.go`: they
   authenticate and require JSON media type but deliberately ignore bodies.
   Django no longer adds an invented body schema or decoded-body limit there.
6. Added checker self-tests for reusable headers and source-aware ignored
   logout body classification, in addition to the existing constraint,
   parameter, security, `$ref`, and nullable fixtures.

### Current static result

| Measure | Result |
| --- | ---: |
| Original Go operations | 82 |
| Django method/path matches | 82/82 |
| Total generated Django operations | 103 |
| Prior preserved checker checkpoint | 735 |
| Intermediate continuation checkpoint | 729 |
| Final checker `2026-11-02.2` findings | 723 |
| Error response declaration differences | 646 |
| Success response schema differences | 36 |
| Parameter differences | 23 |
| Request schema differences | 18 |

The current root classifications are 683 Python declaration/schema
deficiencies, 23 header/parameter declaration discrepancies, 11 stale Go
specification differences, and 6 nullable-fleet source/declaration questions.
No finding was suppressed. `STATIC_SCHEMA_PARITY = FAIL` because 723 static
differences remain.

### Current verification

- Focused checker/catalog/HTTP suite: 20 tests, `OK`.
- Full independent Python suite: 83 tests, `OK`.
- `manage.py check`: exit 0.
- `makemigrations --check --dry-run --noinput`: exit 0 and no model drift;
  the unreachable test database produced a history-check timeout warning, so
  this is not migration-replay evidence.
- Current checker: expected exit 1, fail closed, 723 findings retained.
- PostgreSQL, live Go differential behavior, SMTP, VPS, and deployment:
  **NOT RUN**. No production/VPS/database/service action occurred.

### Files changed in the continuation

| File | Reason |
| --- | --- |
| `cow/api.py` | Source-faithful omitted token and ignored logout body semantics. |
| `cow/admin_controls.py` | Import ordering after existing DTO/RBAC changes. |
| `cow/openapi.py` | Publish the actual response correlation header. |
| `cow/api_contracts.py` | Shared strict acknowledgement response DTO. |
| `config/middleware.py` | Go-path standard error envelope transformation. |
| `cow/services/auth.py` | Source-faithful opaque-token/password boundaries. |
| `scripts/check_go_wire_contract.py` | Header reference resolution and corrected historical/current ledger semantics. |
| `tests/test_go_wire_checker.py`, `tests/test_go_wire_http.py` | Controlled checker and isolated HTTP regressions. |
| `docs/verification/go-python-parity-20261102/*` | Checkpoints, complete ledgers, root cause mapping, and executed-test evidence. |

### Gate summary

```text
ORIGINAL_GO_OPERATIONS = 82
DJANGO_OPERATIONS_FOUND = 82/82

HISTORICAL_DISCREPANCIES = 121
HISTORICAL_DISCREPANCIES_STATICALLY_RECONCILED = 73/121
HISTORICAL_DISCREPANCIES_DYNAMICALLY_RESOLVED = 0/121
CURRENT_SUBSTANTIVE_SCHEMA_MISMATCHES = 723

STATIC_SCHEMA_PARITY = FAIL
HTTP_BEHAVIOR_PARITY = UNVERIFIED
AUTH_SECURITY_PARITY = UNVERIFIED
DISPOSABLE_POSTGRES_INTEGRATION = UNVERIFIED
PYTHON_ONLY_REGRESSIONS = PASS
FRONTEND_CONTRACT_COMPATIBILITY = UNVERIFIED
EXTERNAL_SMTP_ACCEPTANCE = UNVERIFIED
LIVE_DEPLOYMENT_ACCEPTANCE = NOT_PERFORMED
HAL_PHYSICAL_ACCEPTANCE = NOT_PERFORMED

CUTOVER_READY = NO
```

### Exact next safe action

Start with the 683 missing/different response declarations by grouping them by
shared Go error/status envelope and handler family, then verify each family
against a pinned Go process and Django with independently seeded disposable
PostgreSQL state. Before any migration or test mutation, obtain an
operator-authorized disposable target and verify its server, database, role,
marker, and migration history. Do not target the live `evcowbbe` database.

---

## Shared contract root-cause continuation

The 723 baseline was preserved before mutation in
`checkpoint-723-before-shared-error-20261102/` with hashes. The final unique
report is `PARITY_REPORT_SHARED_CONTRACTS_FINAL_20261102.json` (SHA-256
`95297B80D6AB6EA11A95D150E4BA49D4EA74940781D31B52C3287BAE764FA4A5`) and
has a report-specific ledger plus machine-readable root-cause map.

| Finding type | Start | Final |
| --- | ---: | ---: |
| Error response | 646 | 0 |
| Success response | 36 | 30 |
| Parameter | 23 | 1 |
| Request schema | 18 | 7 |
| Total | 723 | 38 |

Implemented shared corrections include a strict reusable error DTO, all
source-declared Go error status schemas, `Retry-After: 60` for Go rate limits,
actual `INVALID_CREDENTIALS` bearer failures, resolved reusable parameter
references, declared source-verified mutation headers, bounded list limits,
and exact diagnostic success/failure envelopes. Focused in-process HTTP tests
cover bearer failures, rate-limit headers, and readiness details; checker
self-tests cover genuine mismatch detection plus header/reference/query-null
equivalence.

The 38 remaining rows are not waived: 30 fleet/pricing success DTO gaps, six
fleet request nullable/range questions, one strict pricing-draft declaration,
and one pricing status-query enum declaration. `STATIC_SCHEMA_PARITY = FAIL`;
dynamic stateful parity and disposable PostgreSQL integration remain
unverified. Historical disposition remains 73 statically reconciled, 48
unchanged/reclassified, and zero dynamically proven resolved.
