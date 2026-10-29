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
| Initial migration determinism | PASS | generator reports `Verified unchanged` |
| All independently executable source/static tests | PASS | **15 tests passed**; output in `2026-10-29_static_tests.log` |
| Django import / `manage.py check` | BLOCKED | Django not installed in isolated build container; outbound pip DNS unavailable |
| Native `makemigrations --check` | BLOCKED | Django package unavailable |
| PostgreSQL 18 migrations and DB catalog checks | NOT RUN | no designated isolated PostgreSQL or driver in build container |
| Real auth and permission DB integration | NOT RUN | integration tests authored under `tests/integration`, not executed |
| Real outbox SMTP delivery | NOT RUN | no real SMTP configured; don't infer send acceptance |
| Go HAL network conformance | NOT RUN | Go HAL not implemented/connected for this project |
| Physical charger | NOT RUN | no hardware environment / approval |
| Payments, driver dispatch, invoices, compensation | NOT IMPLEMENTED | see implementation backlog |
| Existing Go FE behavior parity | NOT RUN | live FE remains on existing Go backend; published Go OpenAPI located on GitHub |
| VPS2 running process health | NOT CHECKED | no VPS2 SSH access exercised |
| Commit/push/deploy/Caddy/DB mutations on VPS2 | NOT PERFORMED | source package creation only |

**Confidence:** meaningful portable source-level proof, NOT runtime readiness or
full backend completeness. Keep old Go integration service unchanged. No
silent substitution of SQLite/mock for PostgreSQL constraint acceptance.
