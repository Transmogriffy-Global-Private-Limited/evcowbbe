# EVCOWBBE application deployment contract

## Purpose and ownership

This document is the contract that `github.com/Transmogriffy-Global-Private-Limited/evcowbbe` exposes to a future VPS-owned deployment framework. The Go module has the same identity. The application serves its HTTP process from `cmd/server` and manages PostgreSQL schema metadata and migrations from `cmd/migrate`.

GitHub Source CI verifies source only. It does not deploy, SSH, upload releases, change VPS infrastructure, configure Caddy, or restart a service. The VPS deployment framework remains the authority for fetching an exact SHA, building it with VPS-owned configuration, running migrations, activating an immutable release, restarting/reloading, and verifying the resulting process.

This repository intentionally does not provide Docker, a VPS release-directory layout, a systemd unit, Caddy configuration, a GitHub deployment trigger, or a generic deployment orchestrator.

## Canonical release build

Run this from a clean checkout with Bash and the Go version declared in `go.mod`:

```bash
bash scripts/build-release.sh --output /chosen/release/path/evcowbbe --version 0.0.0
```

`--output` is required so the VPS chooses the release artifact location. `--version` is optional and defaults to `0.0.0`; it must contain only ASCII letters, digits, `.`, `_`, `+`, or `-`.

The script refuses a repository without a committed `HEAD`, any staged, unstaged, or untracked worktree change, and an invalid SHA. It resolves the full `HEAD` SHA, requires exactly 40 lowercase hexadecimal characters, produces a UTC RFC3339 build time, and builds `./cmd/server` with `-trimpath`, `-buildvcs=false`, and linker values for:

- `internal/buildinfo.Version`
- `internal/buildinfo.GitSHA`
- `internal/buildinfo.BuildTime`

It writes via a temporary sibling artifact and only moves the completed binary to the selected output path. It does not accept or embed secrets.

The exact release invariant outside development is:

```text
configured BUILD_REVISION == embedded buildinfo GitSHA == GET /version git_sha
```

The server and migrator reject startup/use outside `development` if the embedded SHA is missing or invalid, if `BUILD_REVISION` is missing or invalid, or if the two values differ. A release build is therefore not identified merely by an environment variable.

## Runtime configuration

The application reads a local `.env` when present without overriding already-set process environment. The deployment framework should inject configuration by its own approved mechanism. These are key names only; do not place credentials in source control:

| Key | Required by | Meaning |
| --- | --- | --- |
| `APP_ENV` | server, migrator | `development`, `staging`, or `production` |
| `DATABASE_URL` | server, migrator | PostgreSQL connection URL |
| `BUILD_REVISION` | server, migrator | Development may use `dev`; staging/production must equal the embedded 40-character Git SHA |
| `HTTP_LISTEN_ADDR` | server | Loopback IPv4 or IPv6 `host:port`; defaults to `127.0.0.1:18080` |
| `LOG_LEVEL` | server | `DEBUG`, `INFO`, `WARN`, or `ERROR`; defaults to `INFO` |
| `PUBLIC_BASE_URL` | server | Required HTTPS base URL with host and no userinfo, query, or fragment |
| `DEFAULT_TIMEZONE` | server | IANA timezone; defaults to `Asia/Kolkata` |
| `SHUTDOWN_TIMEOUT_SECONDS` | server | Integer graceful-shutdown timeout, 1 through 300; defaults to 30 |

The server only accepts literal loopback IP listening addresses. Public exposure belongs to the VPS-owned reverse-proxy configuration, not this repository.

## HTTP contract

All three routes are unauthenticated loopback application endpoints intended for local proxy/deployment checks.

| Route | Success | Meaning |
| --- | --- | --- |
| `GET /health/live` | `200 {"status":"live"}` | Lightweight process liveness. It does not query PostgreSQL and remains live during a temporary database outage. |
| `GET /health/ready` | `200 {"status":"ready"}` | PostgreSQL is reachable and the ledger is valid and compatible with this binary's schema version, with no known pending migration. |
| `GET /version` | `200` JSON with `version`, `git_sha`, and `build_time` | Running binary identity. Response is `Cache-Control: no-store` and contains no secret configuration. |

Readiness returns `503 {"status":"not_ready"}` for database failure, a missing ledger, known migration identity/checksum/compatibility disagreement, history conflict, an incompatible future migration, a pending known migration, or a migration operation holding the PostgreSQL advisory lock. The HTTP response intentionally omits database errors, DSNs, SQL, and stack traces. Health probes do not log every readiness error, to avoid probe-driven log spam. Operators should use `migrate status` and `migrate verify` for migration diagnostics; readiness does not create the migration table or apply changes.

## Migration contract

Migration source is in `db/migrations/`. There are deliberately zero domain/application migrations today. A binary's schema version is `0` when it embeds no application migrations, otherwise it is the highest embedded migration version. Future migration files must be immutable, forward-only SQL files named `<positive-integer>_<lowercase_name>.sql`, for example `000001_create_example.sql`.

Every future migration must declare exactly one compatibility floor on its own line:

```sql
-- evcowbbe:min-compatible-binary-version=0
```

The value means the oldest binary schema version allowed to operate after that migration. It must be an integer satisfying `0 <= floor <= migration version`; missing, duplicate, malformed, negative, or out-of-range directives reject the migration file. The directive is part of the exact SQL bytes and therefore part of the SHA-256 checksum.

The `public.evcowbbe_schema_migrations` ledger is initialized only by:

```bash
go run ./cmd/migrate up
```

In a deployed release, run the release's migrator binary with the same runtime environment instead. `up` obtains a PostgreSQL session advisory lock, creates the ledger idempotently, verifies every already-applied migration against the embedded filename metadata and checksum, then applies each pending migration and ledger record in one transaction. A failed migration rolls back its SQL and does not gain a ledger entry. Concurrent `up` calls serialize on the same database-native lock.

The ledger records migration version, name, checksum, `applied_at`, `source_git_sha`, and `min_compatible_binary_version`. `migrate up` evolves the foundation ledger by adding the compatibility column if absent; it never deletes or recreates ledger history. Existing historical rows without this metadata fail verification rather than receiving an invented compatibility classification. A valid embedded SHA is stored when available. Honest local development builds with no real embedded SHA store `NULL`; they do not pretend that `dev` is a Git revision. Staging and production are rejected before migration work unless the release identity invariant holds.

For a migration the current binary knows, the ledger name, checksum, and compatibility floor must all exactly match the embedded migration. A known but unapplied migration is pending and blocks readiness. For an applied migration newer than the current binary's schema version, an older binary cannot verify that future migration's SQL or checksum because it does not embed that source. It can only trust the durable, range-validated compatibility floor: it remains compatible if the floor is less than or equal to its own schema version, and becomes unready if the floor requires a newer binary. An unknown applied migration at or below the binary's schema version is always a history conflict and fails.

This permits safe binary rollback after an explicitly backward-compatible forward migration without automatic database DOWN migration. A breaking migration records a floor above an older binary's schema version and deliberately blocks that rollback candidate from becoming ready. Database rollback remains a separate, deliberate recovery decision.

Available commands are:

```bash
go run ./cmd/migrate up
go run ./cmd/migrate status
go run ./cmd/migrate verify
go run ./cmd/migrate verify --files-only
```

`status` reports ledger presence plus known applied, known pending, future compatible, and future incompatible migrations without SQL or DSN output. `verify` checks the existing durable ledger without applying or creating anything and succeeds only when the current binary has no known pending migration and every applied migration is valid and compatible under the rules above. `verify --files-only` validates embedded migration-file names, checksums, and required compatibility metadata without any database connection and is the CI-safe static migration check.

Deployment rollback must never blindly run database DOWN migrations. Binary rollback and database rollback are separate decisions. For an incompatible or destructive schema change, preserve database truth and use a deliberate forward fix or a separately authorized recovery procedure after impact analysis.

## Expected deployment sequence and verification failures

The future VPS framework should build a clean exact SHA, configure the process with the matching `BUILD_REVISION`, run `migrate up`, start/reload the selected binary, and then verify both HTTP readiness and `/version` identity. Deployment verification fails if any release-identity check, migration command, process startup, liveness/readiness response, or `/version` SHA comparison fails.

The server handles `SIGINT` and `SIGTERM` by stopping new HTTP work and calling graceful shutdown for the configured timeout. Database pools close when the process exits.

Source CI is defined in `.github/workflows/ci.yml`. It checks formatting without changing source, `go mod verify`, tests, vetting, both command builds into runner-temporary paths, static migration validation, the canonical `scripts/build-release.sh` interface, release-binary existence/executability, and that the release builder reports the workflow's exact `GITHUB_SHA`. It does not deploy or publish an artifact. PostgreSQL integration tests remain gated solely by `TEST_DATABASE_URL`; they never fall back to `DATABASE_URL`. Source CI currently supplies no disposable PostgreSQL service, so database integration execution is an explicit separate verification boundary.
