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
| `GET /health/ready` | `200 {"status":"ready"}` | PostgreSQL is reachable and the immutable migration set embedded in this binary matches an existing ledger with no pending migration. |
| `GET /version` | `200` JSON with `version`, `git_sha`, and `build_time` | Running binary identity. Response is `Cache-Control: no-store` and contains no secret configuration. |

Readiness returns `503 {"status":"not_ready"}` for database failure, a missing ledger, ledger/file checksum disagreement, an applied migration unknown to this binary, a pending migration, or a migration operation holding the PostgreSQL advisory lock. The HTTP response intentionally omits database errors, DSNs, SQL, and stack traces; the process logs safe diagnostic context. Readiness does not create the migration table or apply changes.

## Migration contract

Migration source is in `db/migrations/`. There are deliberately zero domain/application migrations today. Future migration files must be immutable, forward-only SQL files named `<positive-integer>_<lowercase_name>.sql`, for example `000001_create_example.sql`. The running binary embeds these files, sorts them by numeric version, and computes SHA-256 checksums from their exact bytes.

The `public.evcowbbe_schema_migrations` ledger is initialized only by:

```bash
go run ./cmd/migrate up
```

In a deployed release, run the release's migrator binary with the same runtime environment instead. `up` obtains a PostgreSQL session advisory lock, creates the ledger idempotently, verifies every already-applied migration against the embedded filename metadata and checksum, then applies each pending migration and ledger record in one transaction. A failed migration rolls back its SQL and does not gain a ledger entry. Concurrent `up` calls serialize on the same database-native lock.

The ledger records migration version, name, checksum, `applied_at`, and `source_git_sha`. A valid embedded SHA is stored when available. Honest local development builds with no real embedded SHA store `NULL`; they do not pretend that `dev` is a Git revision. Staging and production are rejected before migration work unless the release identity invariant holds.

Available commands are:

```bash
go run ./cmd/migrate up
go run ./cmd/migrate status
go run ./cmd/migrate verify
go run ./cmd/migrate verify --files-only
```

`status` reports only ledger presence and applied/pending counts/names. `verify` checks the existing durable ledger without applying or creating anything. `verify --files-only` validates embedded migration-file metadata without any database connection and is the CI-safe static migration check.

Deployment rollback must never blindly run database DOWN migrations. Binary rollback and database rollback are separate decisions. For an incompatible or destructive schema change, preserve database truth and use a deliberate forward fix or a separately authorized recovery procedure after impact analysis.

## Expected deployment sequence and verification failures

The future VPS framework should build a clean exact SHA, configure the process with the matching `BUILD_REVISION`, run `migrate up`, start/reload the selected binary, and then verify both HTTP readiness and `/version` identity. Deployment verification fails if any release-identity check, migration command, process startup, liveness/readiness response, or `/version` SHA comparison fails.

The server handles `SIGINT` and `SIGTERM` by stopping new HTTP work and calling graceful shutdown for the configured timeout. Database pools close when the process exits.

Source CI is defined in `.github/workflows/ci.yml`. It checks formatting without changing source, `go mod verify`, tests, vetting, both command builds, static migration validation, and whitespace errors. PostgreSQL integration tests remain gated solely by `TEST_DATABASE_URL`; they never fall back to `DATABASE_URL`. Source CI currently supplies no disposable PostgreSQL service, so database integration execution is an explicit separate verification boundary.
