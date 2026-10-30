# October 29, 2026: actual executed verification vs required acceptance

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
| WP3.1A diagnostics, generated OpenAPI registration and migration guards | PASS | **35 tests and 68 subtests passed** with isolated Python dependencies; confirms all 12 scoped operations are registered, the public YAML document serializes, and response safety is source-tested |
| All independently executable source/static tests | PASS | **35 tests and 68 subtests passed** with isolated Python dependencies; PostgreSQL integration tests intentionally excluded |
| Django import / `manage.py check` | PASS | executed with process-local test-only configuration; no issues |
| Offline migration-state consistency | PASS | static migration-parity test passed; no database contacted |
| Native `makemigrations --check --dry-run` | PASS WITH WARNING | Django reported `No changes detected`; its non-mutating history probe to the process-local dummy loopback URL was rejected before authentication, so this is not PostgreSQL acceptance evidence |
| WP3.1A scoped OpenAPI operation comparison | PASS WITH REPRESENTATION GAPS | All 12 scoped methods/paths are present; effective bearer authentication normalizes to the same HTTP bearer mechanism. Generated Ninja declarations do not yet fully express legacy response/error/header components, which remains visible in the field-level report and is not asserted as contract parity. |
| PostgreSQL migrations, replay, catalog, triggers, constraints, indexes and sequences | NOT RUN for 0003 | 2026-10-29 evidence covers only immutable 0001/0002. The new vehicle migration requires a new marker-verified disposable acceptance run. |
| Real auth and permission DB integration | NOT RUN for WP3.1A | Earlier marker-verified evidence predates vehicle parity and is not evidence for this new slice. |
| Real outbox SMTP delivery | NOT RUN | no real SMTP configured; don't infer send acceptance |
| Go HAL network conformance | NOT RUN | Go HAL not implemented/connected for this project |
| Physical charger | NOT RUN | no hardware environment / approval |
| Payments, driver dispatch, invoices, compensation | NOT IMPLEMENTED | see implementation backlog |
| Existing Go FE behavior parity | NOT RUN | live FE remains on existing Go backend; published Go OpenAPI located on GitHub |
| VPS2 running process health | NOT CHECKED | no VPS2 SSH access exercised |
| VPS2 deployment, Caddy and protected application DB | UNCHANGED | Only isolated acceptance DB provisioned and migrated. Existing Go service and live evcowbbe DB not targeted. |

**Confidence:** portable tests plus isolated PostgreSQL acceptance, NOT runtime readiness or
full backend completeness. Keep old Go integration service unchanged. No
silent substitution of SQLite/mock for PostgreSQL constraint acceptance.
