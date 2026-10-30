# October 30, 2026: actual executed verification vs required acceptance

| Check | Result | Evidence / limitation |
| --- | --- | --- |
| Python syntax across project | PASS | `python -m compileall -q cow config scripts tests manage.py` |
| Pure HAL command/fact semantics and replay | PASS | unittest logs; no external Go HAL exercised |
| HMAC tampering and freshness | PASS | pure contract tests |
| HAL JSON Schema example fixtures | PASS | jsonschema fixture test |
| Money/Wh rounding | PASS | pure exact-money test |
| OpenAPI static comparison detector | PASS | unittest synthetic old/new diff tests |
| Static Django-model to 0001 migration field coverage | PASS | AST comparison test |
| 0002 SQL table reference consistency | PASS | static inspection, NOT PostgreSQL execution |
| Initial migration/model state consistency | PASS | Django offline migration autodetector reports no drift; obsolete one-time generator retired. |
| PostgreSQL harness URL/identity/role/retry rejection | PASS | pure `tests/test_pg_test_database.py`; no database contacted |
| WP3.1A diagnostics, generated OpenAPI registration and migration guards | PASS | **35 tests and 70 subtests passed** with isolated Python dependencies; confirms all 12 scoped operations are registered, the public YAML document serializes, and response safety is source-tested |
| WP3.1B pairing correction source acceptance | PASS | **38 tests and 77 subtests passed** locally with PostgreSQL integration excluded. New guards verify that 0004 maps non-NULL historical `effective_to` to ENDED before lifecycle checks and keeps unavailable legacy provenance NULL. |
| All independently executable source/static tests | PASS | **38 tests and 77 subtests passed** with isolated Python dependencies; PostgreSQL integration tests intentionally excluded. |
| Django import / `manage.py check` | PASS | executed with process-local test-only configuration; no issues. |
| Offline migration-state consistency | PASS | static migration-parity test passed and `makemigrations --check --dry-run` found no model drift; no database contacted. |
| Native `makemigrations --check --dry-run` | PASS WITH WARNING | Django reported `No changes detected`; its non-mutating history probe to the process-local dummy loopback URL was rejected before authentication, so this is not PostgreSQL acceptance evidence |
| WP3.1A scoped OpenAPI operation comparison | PASS WITH REPRESENTATION GAPS | All 12 scoped methods/paths are present; effective bearer authentication normalizes to the same HTTP bearer mechanism. Generated Ninja declarations do not yet fully express legacy response/error/header components, which remains visible in the field-level report and is not asserted as contract parity. |
| WP3.1A PostgreSQL migration and integration acceptance | PASS | VPS2, 2026-10-30: **15/15 PostgreSQL integration tests passed** against fresh isolated `cow_wp31a_20261030_test`; migrations 0001, 0002 and 0003 applied successfully and replay reported no changes. One Django Ninja tuple-response deprecation warning was observed. Existing Go production deployment and live `evcowbbe` DB were not targets. |
| PostgreSQL catalog, triggers, constraints, indexes and sequences through 0003 | PASS | The WP3.1A marker-authorized fresh database acceptance above exercised the reviewed 0001-0003 ledger. This is not evidence for a future 0004 migration. |
| Real auth and permission DB integration for WP3.1A | PASS | Included in the 15/15 VPS2 PostgreSQL integration acceptance above; no production credentials or target were used. |
| WP3.1B PostgreSQL 0003-to-0004 upgrade rehearsal | NOT RUN | The reusable marker-authorized `cow_wp31a_20261030_test` workflow now seeds representative pre-0004 ACTIVE and closed pairings, then upgrades without flush. It checks ID, dates, vehicle/charger ownership, expected flag, lifecycle mapping, and absence of fabricated legacy provenance. Operator execution is still required. |
| WP3.1B fresh PostgreSQL migration and integration acceptance through 0004 | NOT RUN | After separately authorized operator-only reset of the disposable `public` schema (marker preserved), `scripts/run_pg_tests.py --fresh` applies and replays 0001-0004, validates catalog shape/dependencies and runs integration tests. No VPS2 or database was contacted for this correction. |
| Reusable PostgreSQL target/reset safety guards | PASS (source-only); NOT RUN (runtime) | Local harness regression tests passed. The harness accepts only `cow_wp31a_20261030_test`, validates actual identity, role, ownership, grants, schemas, dependencies and ledger before mutation, and rejects Django `flush`/`--reset`. The operator SQL reset is separately authorized and preserves `cow_test_harness`. |
| Real outbox SMTP delivery | NOT RUN | no real SMTP configured; don't infer send acceptance |
| Go HAL network conformance | NOT RUN | Go HAL not implemented/connected for this project |
| Physical charger | NOT RUN | no hardware environment / approval |
| Payments, driver dispatch, invoices, compensation | NOT IMPLEMENTED | see implementation backlog |
| Existing Go FE behavior parity | NOT RUN | Published-contract representation gaps and broader FE behavior parity remain open; live FE remains on the existing Go backend. |
| VPS2 running process health | NOT CHECKED | no VPS2 SSH access exercised |
| VPS2 deployment, Caddy and protected application DB | UNCHANGED | Only isolated acceptance DB provisioned and migrated. Existing Go service and live evcowbbe DB not targeted. |

**Confidence:** WP3.1A source tests plus isolated PostgreSQL acceptance through
0003, NOT runtime readiness or full backend completeness. Keep old Go
integration service unchanged. No silent substitution of SQLite/mock for
PostgreSQL constraint acceptance.
