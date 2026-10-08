# EVCOWBBE application deployment contract

## Purpose and ownership

This document is the contract that `github.com/Transmogriffy-Global-Private-Limited/evcowbbe` exposes to a future VPS-owned deployment framework. The Go module has the same identity. The application serves its HTTP process from `cmd/server` and manages PostgreSQL schema metadata and migrations from `cmd/migrate`.

GitHub Source CI verifies source only. It does not deploy, SSH, upload releases, change VPS infrastructure, configure Caddy, or restart a service. The VPS deployment framework remains the authority for fetching an exact SHA, building it with VPS-owned configuration, running migrations, activating an immutable release, restarting/reloading, and verifying the resulting process.

This repository intentionally does not provide Docker, an application-release directory layout, an application systemd unit, Caddy configuration, or an installed deployment executor. It does include a GitHub trigger and systemd templates for passive VPS ingress only; neither can deploy the application. It does contain the source-only durable control-plane foundation described below; that source is not an installed authority and normal application deployment must never silently update it.

## Deployment control-plane foundation (Burner 1)

`ops/deploy` is a Python 3.12-standard-library-only, local SQLite foundation for a future VPS deployment control plane. It has no network, subprocess, Git, GitHub, Go-build, migration, service, Caddy, release-link, application-environment, SMTP, secret, or VPS access capability. Its strongest permitted effect is creating or updating a caller-selected SQLite state file.

The future installed control plane will own desired state, observed state, and durable operator interventions. Burner 1 implements only the durable desired/control truth and request-admission foundations. The eventual installed paths, identities, and wake mechanism remain separately authorized operational work: source code must not assume they already exist or access `/srv/evcowbbe`, `/etc/evcowbbe-deploy`, `/var/lib/evcowbbe-deploy`, `/run/lock`, or VPS2 from local development.

### Durable state and recovery foundations

One SQLite database is authoritative for Burner 1's locally observed candidates, run ownership, operator controls, migration-ledger observations, and audit truth. Each connection enables WAL, foreign keys, `synchronous=FULL`, and a bounded busy timeout; schema initialization is transactional and repeatable without data loss. The database stores an explicit schema version, rejects a newer, older, incomplete, or integrity-invalid schema, and fails closed instead of recreating inconsistent state. Schema version 4 is a clean pre-release schema; version-1 through version-3 state databases are explicitly refused rather than reset or upgraded implicitly.

The normalized state includes durable deployment requests, deployment runs, append-only deployment events, operator controls, and migration decisions. Events cannot be updated or deleted by the SQLite schema. Current control/request/run/decision state is represented independently of event history. Timestamps are UTC. A future wake unit may signal that work exists, but it must never become a second queue or authoritative request store.

| State | Durable semantics |
| --- | --- |
| `control_plane_meta` | Singleton schema version and initialization timestamp. A newer, incomplete, or integrity-invalid schema is rejected without reset. |
| `deployment_requests` | Canonical accepted prod-push candidate, SHA-256 payload hash, external `source_id`, monotonic sequence, and acceptance time. `source_id` is unique. Acceptance proves only local receipt, never current remote branch authority. |
| `deployment_runs` | Stable run ID, exact canonical ingress-request reference, SHA-level work identity, monotonic SHA attempt number, actor, explicit state, execution-claimed timestamp, migration-decision reference, outcome, bounded structured error details, and lifecycle timestamps. SQLite allows only one active run globally. |
| `deployment_events` | Immutable UTC audit sequence with canonical JSON details. SQLite triggers reject updates and deletes. |
| `operator_controls` | Active/released pause, exact-SHA sideline, exact-SHA rollback hold, or single-use SHA retry authorization, with creator/releaser/consumer identity, reason, and timestamps. |
| `migration_decisions` | Exact SHA plus exact migration-plan fingerprint and count, historical approval/revocation, `executing`/`uncertain` irreversible-boundary states, observed resulting-ledger fingerprint/count, and reconciled no/partial/full commit state. |

Incoming prod requests have exactly five fields: `event`, `sha`, `branch`, `actor`, and `source_id`. Burner 1 accepts only `push` to `prod` with a 40-character lowercase SHA and bounded safe identifiers. It canonicalizes the validated payload, persists its SHA-256 hash, and makes `source_id` the database-enforced external idempotency key. One new source ID creates one accepted candidate and one audit event in one transaction. An identical repeat returns the original request; conflicting reuse fails without mutating it. SQLite serialization and uniqueness protect concurrent independent local processes. Candidate acceptance is immutable ingress history, so it is not misleadingly marked superseded merely because another event arrives.

Requests and runs are distinct. A request is a locally accepted candidate; a run is a stable future deployment attempt with explicit transition rules. Failure details and audit details are canonical structured JSON, not opaque unbounded logs; callers must not include credentials, tokens, connection strings, or raw commands. A future trusted executor must independently obtain the exact current remote `prod` SHA and pass it as a trusted input to selection, run planning, and the just-before-execution claim. Burner 1 never derives remote authority from request order and never simulates GitHub verification. Without that input, desired state is blocked as `authoritative_prod_head_required`.

Selection is exact-SHA only: the verified head must have an admitted candidate and must not be sidelined. If verified B is sidelined while older A is accepted, desired state is blocked as `sidelined`; Burner 1 never falls back to A. Releasing B permits a later fresh remote-head verification to select B, not an automatic replay. For multiple ingress events with one SHA, the first accepted request is the immutable canonical ingress association; later `source_id` values remain truthful audit history and never invalidate or relabel planned/running work. `create_run` and `claim_run_execution` both require that exact canonical request ID.

Ingress identity and deployment-work identity are separate. Different `source_id` values may record truthful same-SHA delivery history, but they do not create automatic retry eligibility. A SHA has monotonic run attempts and only one active run globally. After a failed attempt, or a cancellation after execution was claimed, a trusted local operator must create a single-use retry authorization for that exact SHA; its consumption is audited atomically with a new run for the same canonical request. `cancel-retry` atomically withdraws only an unused retry authorization, preserves it as released history, restores the retry fence, and is idempotent when no active authorization exists. A consumed authorization cannot be retroactively canceled. This prevents unbounded automatic retry while a newer verified B remains independently eligible after failed A. A planned run that is superseded or canceled before execution was claimed is safe pre-execution disposition, not a failed deployment, and may be planned again if its SHA later becomes the verified head. A previously succeeded SHA instead requires future-executor runtime reconciliation before another deployment attempt; Burner 1 cannot infer whether the runtime already matches it.

SQLite serializes all active work with one partial unique index spanning `planned`, `running`, `migration_boundary_claimed`, `migration_uncertain`, and `migration_committed`. A planned run is rechecked against current remote-head input and every pause, sideline, and rollback-hold fence when it claims executable work. A changed head supersedes only a still-planned run; a running or post-boundary run is never falsely rewritten as harmless. Recovery resumes or reconciles the same durable run ID.

### Local operator controls

The local CLI requires an explicit state path and writes stable JSON:

```bash
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 submit \
  --event push --sha <40-lowercase-hex> --branch prod \
  --actor trusted-local-operator --source-id <unique-event-id>
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 pause \
  --operator trusted-local-operator --reason "maintenance window"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 resume \
  --operator trusted-local-operator --reason "maintenance complete"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 sideline <sha> \
  --operator trusted-local-operator --reason "investigating failure"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 release <sha> \
  --operator trusted-local-operator --reason "review complete"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 retry <sha> \
  --operator trusted-local-operator --reason "reviewed retry after failure"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 cancel-retry <sha> \
  --operator trusted-local-operator --reason "retry no longer authorized"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 requests
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 history --limit 100
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status --verbose
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status \
  --trusted-prod-sha <externally-verified-40-lowercase-hex-sha>
```

`status` is concise by default and has `--verbose` for requests, runs, and migration-decision state. `--trusted-prod-sha` is inspection-only input: it is valid for controlled tests or a future trusted executor, but this CLI cannot authenticate or verify it. `requests` and `history` expose the durable candidate list and append-only events. `pause`/`resume`, exact-SHA `sideline`/`release`, and `rollback-hold`/`rollback-release` are idempotent where no state changes. A rollback hold records the protected rollback target and blocks automatic selection; a conflicting target returns a domain conflict without replacing the existing hold. Removing it resumes reconciliation from real current state instead of replaying that rollback target. A failed SHA and a sidelined SHA remain different facts.

Caller-supplied operator names are only trusted-local audit attribution in Burner 1. They are not authentication. A future installed ingress must authenticate the command source and pass a verified operator identity. Local CLI validation, idempotency conflicts, invalid transitions, and state/integrity failures have distinct exit codes and JSON errors.

### Migration-decision boundary

Migration decisions are deliberately separate from deployment runs. Burner 1 stores approval only for one exact admitted SHA, one exact 64-character plan fingerprint, and the exact positive number of migrations in that plan. The fingerprint is SHA-256 of canonical JSON containing the target release SHA, the complete starting application migration-ledger digest, and the ordered proposed migrations including version, filename, SQL checksum, and compatibility floor. A changed starting ledger or materially changed plan must produce a different fingerprint.

Revocation is allowed only while a decision is `approved`. A revoked or verified `not_committed` approval remains immutable history, and a later approval for that same exact SHA and plan creates a new decision ID. `executing`, `uncertain`, `not_committed`, `partially_committed`, and `committed` decisions cannot be rewritten as revoked. `committed` or `partially_committed` permanently blocks reapproval of the same exact target/start-ledger-bound plan. The partial rule prevents retrying migrations that real ledger evidence says already applied.

Before a future executor can call PostgreSQL, it must atomically claim the decision/run migration boundary after rechecking exact SHA, fingerprint, trusted remote head, and all operator fences. The control-plane state becomes `executing`; revocation is then rejected because PostgreSQL may commit independently. On ambiguous result or crash it becomes `uncertain`. Reconciliation stores the independently observed resulting application-ledger fingerprint and how many of the approved plan migrations committed: zero becomes `not_committed`, an in-range nonzero subset becomes `partially_committed`, and the full count becomes `committed`. A partial result marks A's run failed without claiming to undo the durable migrations, releases serialized run ownership, and permits a newer corrective B to reconcile forward from the actual ledger. This is deliberately not a cross-database transaction and never claims that a migration succeeded without ledger evidence. Burner 1 executes no application migration and assumes no database DOWN migration is safe.

### VPS2 ingestion integration package

The source-controlled `ops/deploy/vps` package is generated and locally tested, but is not installed by source CI, an application release, or this repository checkout. A separately authorized root operator installs an immutable control-plane release at `/opt/evcowbbe-deploy/releases/<40-character-control-plane-source-sha>` and points the root-owned `/opt/evcowbbe-deploy/current` link at that release. This is entirely separate from `/srv/evcowbbe/current`; no application release silently upgrades the control plane.

`Prod VPS Ingestion` is a `workflow_run` workflow that runs from the default branch only after the named `Source CI` workflow has succeeded for a `push` to `prod` in this exact repository. It uses `github.event.workflow_run.head_sha`, not the downstream workflow SHA, and rejects a non-lowercase 40-character SHA before constructing JSON. The deterministic `source_id` is `source-ci:<upstream-run-id>:<upstream-run-attempt>`. It has no checkout, artifact, pull-request-target, deployment, or broad token permission. It writes an operator-configured dedicated private key and an operator-configured complete `known_hosts` entry into runner-temporary `0600` files, uses `StrictHostKeyChecking=yes`, and never runs `ssh-keyscan` or disables host checking. Host, port, private key, and host key are GitHub secret configuration, not repository values.

The dedicated `evcow-trigger` key is restricted by a root-owned `authorized_keys` entry with OpenSSH `restrict,command=...`. It accepts only the literal SSH original command `evcowbbe-deploy-ingest-v1`, disables PTY, user rc, X11, agent forwarding, TCP forwarding, and `PermitOpen`, and receives exactly one JSON object on stdin. The root-owned ingress code bounds input at 4096 bytes, requires strict UTF-8 and at most one trailing LF (no other framing or duplicate keys), then reuses Burner 1's exact request validation. It has no database write permission. Its only fixed privileged handoff is the no-argument sudo command `/opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-admit-and-wake`.

That fixed root bridge runs the SQLite admission command as `evcow-orchestrator`, the only database writer. Admission uses the existing transactional `submit_request` idempotency model, so SQLite remains the sole durable queue/truth. The acknowledgement is emitted only after that transaction returns. On successful admission the bridge starts `evcowbbe-deploy-reconcile.service`; a wake failure returns nonzero even though the durable request is retained. The five-minute persistent timer is the recovery path for a crash after commit but before wake. Wakes may coalesce because they are hints only.

The systemd service is passive. It reports an exact historical `accepted_requests_total` and `reconciliation_state: not_evaluated`, always with `deployed: false`. Historical admission is not itself pending work. It does not fetch Git, build, migrate, alter releases/configuration, restart the application, roll back, or notify. The unit runs as `evcow-orchestrator` with `NoNewPrivileges`, private temporary/devices, `ProtectSystem=strict`, protected home/kernel/control groups, no capabilities, native syscall architecture, and write access only to `/var/lib/evcowbbe-deploy`.

Ingress acceptance remains only an audited candidate. A delayed successful Source CI event can admit an obsolete SHA safely. The later executor must independently verify the current authoritative `prod` SHA, the relevant Source CI success, actual release identity, PostgreSQL ledger/checksums/compatibility, and all operator controls before any irreversible work. It must not treat an admission record, a wake, or a GitHub acknowledgement as deployment success.

### Ubuntu 24.04 installation and recovery runbook

This is a separately authorized VPS2 procedure. Substitute the values in angle brackets; do not invent a host, port, key fingerprint, or source SHA. It does not access the application `.env`, PostgreSQL credentials, or `evcowbbe-dev.service`.

1. On an administrative workstation, generate the dedicated key without printing its private material:

   ```bash
   umask 077
   ssh-keygen -t ed25519 -a 64 -f ./evcow-trigger-vps2 -C evcowbbe-prod-ingest
   ssh-keygen -lf ./evcow-trigger-vps2.pub
   ```

   Record the public-key fingerprint. Store the private key only in the GitHub secret `EVCOWBBE_VPS2_INGEST_PRIVATE_KEY`; store the exact trusted VPS host-key line from an independently verified console/provider record in `EVCOWBBE_VPS2_INGEST_HOST_KEY`. Configure `EVCOWBBE_VPS2_INGEST_HOST` and `EVCOWBBE_VPS2_INGEST_PORT` as GitHub secrets. Never derive the host key with an unauthenticated `ssh-keyscan` during CI.

2. On VPS2 as root, inspect rather than alter existing account and SSH state:

   ```bash
   for account in evcow-trigger evcow-orchestrator; do id "$account"; getent passwd "$account"; passwd -S "$account"; done
   sudo -l -U evcow-trigger
   test -d /srv/evcowbbe/source
   test -f /etc/evcowbbe-deploy/deploy.conf
   test -d /var/lib/evcowbbe-deploy
   ```

   A password-locked account is not evidence that public-key forced-command authentication works. Do not change its shell or lock state merely to satisfy this check.

3. Obtain a clean checkout of the control-plane source at the separately approved `<control-plane-source-sha>` outside `/srv/evcowbbe/current`, verify it, and run the installer once:

   ```bash
   control_source=/root/evcowbbe-deploy-source
   control_sha=<40-lowercase-hex-sha>
   git -C "$control_source" rev-parse HEAD
   git -C "$control_source" status --short
   sudo bash "$control_source/ops/deploy/vps/install-control-plane.sh" \
     --source-dir "$control_source" \
     --source-sha "$control_sha" \
     --trigger-public-key-file /root/evcow-trigger-vps2.pub
   ```

   The installer refuses a source SHA mismatch or dirty checkout (including untracked files), pre-existing conflicting release/current/key/sudoers/systemd paths, missing accounts, malformed Ed25519 public key, incompatible existing SQLite directory ownership, or unsupported effective sshd key configuration. It archives only committed Git objects at the approved exact SHA, stages and publishes root-owned immutable code, initializes `/var/lib/evcowbbe-deploy/orchestrator.sqlite3` as `evcow-orchestrator`, enables the passive timer, and creates a **root-owned, forced-command, `restrict` authorized key last**. No `Match` drop-in is installed: Ubuntu's sshd `Include` ordering can cause `Match` blocks in drop-ins to affect unrelated settings. No sshd reload is needed for `authorized_keys` updates. The installer removes only newly created artifacts on ordinary failure, preserving the SQLite database, but a power loss/SIGKILL can leave a partially staged installation; follow the recovery procedure below. Retain the current administrative console until real SSH denial and ingress acceptance checks pass. The application service is never touched.

4. Verify the installation and constrained identity:

   ```bash
   systemctl status evcowbbe-deploy-reconcile.timer --no-pager
   systemctl start evcowbbe-deploy-reconcile.service
   journalctl -u evcowbbe-deploy-reconcile.service -n 50 --no-pager
   sudo -u evcow-orchestrator /opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-reconcile
   sudo -u evcow-trigger -n /usr/bin/sudo -n /opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-admit-and-wake </dev/null || true
   systemctl status evcowbbe-dev.service --no-pager
   readlink -f /srv/evcowbbe/current
   ```

   From the workstation, test only the forced operation using the dedicated key and pinned known-hosts file. Use a real successful Source CI SHA, never a fabricated production-looking SHA:

   ```bash
   vps_host=<operator-configured-host>
   vps_port=<operator-configured-port>
   candidate_sha=<successful-source-ci-40-character-sha>
   printf '%s\n' '<independently-verified-known-hosts-line>' > ./vps2-known_hosts
   chmod 0600 ./vps2-known_hosts
   CANDIDATE_SHA="$candidate_sha" python3 - <<'PY' > ./admission-request.json
   import json
   import os
   import re
   sha = os.environ["CANDIDATE_SHA"]
   if not re.fullmatch(r"[0-9a-f]{40}", sha):
       raise SystemExit("candidate SHA is invalid")
   print(json.dumps({"event":"push","sha":sha,"branch":"prod","actor":"manual-verifier","source_id":"manual-ingress-test:1"}, separators=(",", ":")))
   PY
   ssh -i ./evcow-trigger-vps2 -p "$vps_port" -o BatchMode=yes -o IdentitiesOnly=yes \
     -o StrictHostKeyChecking=yes -o UserKnownHostsFile=./vps2-known_hosts \
     "evcow-trigger@$vps_host" evcowbbe-deploy-ingest-v1 < ./admission-request.json
   ssh -i ./evcow-trigger-vps2 -p "$vps_port" -o BatchMode=yes -o StrictHostKeyChecking=yes \
     -o UserKnownHostsFile=./vps2-known_hosts "evcow-trigger@$vps_host" true && exit 1 || true
   ```

   An arbitrary command, PTY request, shell, port forwarding attempt, or a malformed request must fail. Also test an SSH request with no remote command; the wrapper must reject it. Never consider a merely successful `sshd -t` check evidence that the forced-key restrictions have been exercised. A valid request returns a bounded JSON acknowledgement; repeating it returns the same request with `created:false`. Inspect `orchestrator.sqlite3` only through the orchestrator CLI/API and verify that the application service and application release link are unchanged.

5. To recover after a wake failure, do not resubmit with a new source ID. Inspect the durable request/history, then start the passive service or wait for the persistent timer:

   ```bash
   systemctl start evcowbbe-deploy-reconcile.service
   journalctl -u evcowbbe-deploy-reconcile.service -n 100 --no-pager
   ```

   For interrupted initial installation, inspect exact ownership, content, and any partial paths before retrying. If and ONLY IF they are confirmed to have been created by this COW installer and are not live/useful, disable its passive timer and remove its specific sudoers file, systemd units, root-owned trigger authorized_keys and `.ssh` (only if empty after removal), its root-owned `/opt/evcowbbe-deploy/current` symlink and matching release directory; never delete the application releases or SQLite state. Run `systemctl daemon-reload`, `sshd -t`, and `visudo -c` and repeat preflight. Do not clean unknown or pre-existing paths automatically.

   To roll back a control-plane installation, stop/disable the timer, preserve the SQLite state, and repoint `/opt/evcowbbe-deploy/current` only through a separately reviewed root change to a prior immutable release. Revalidate `sshd -t`, `visudo -cf /etc/sudoers.d/evcowbbe-trigger-ingest`, and `systemctl daemon-reload` before re-enabling the timer. Never roll back application code, run database DOWN migrations, or delete SQLite history as part of this procedure.

The future executor is a reconciler: it must independently verify the current remote `prod` SHA, then compare that exact SHA with admitted candidates, real runtime/release/database observation, and durable controls after every wake or crash recovery. It must permit a newer fix-forward SHA after a failed earlier run and must not manufacture successful outcomes. It must reconcile every `executing` or `uncertain` migration decision against the application ledger before allowing revocation, reapproval, or a conflicting deployment. The eventual notification subsystem must add a separate durable outbox with retry policy, deduplication keys, delivery state, and SMTP-failure isolation. Burner 1 does not send email or persist notification delivery claims.

Run the control-plane source tests locally with:

```bash
python -m compileall -q ops/deploy
python -m unittest discover -s ops/deploy/tests -v
```

## Canonical release build

Run this from a clean checkout with Bash and the Go version declared in `go.mod`:

```bash
bash scripts/build-release.sh \
  --server-output /chosen/release/path/evcowbbe \
  --migrator-output /chosen/release/path/evcowbbe-migrate \
  --version 0.0.0
```

`--server-output` and `--migrator-output` are required, distinct paths chosen by the VPS release framework. A release consists of at least those two executables. `--version` is optional and defaults to `0.0.0`; it must contain only ASCII letters, digits, `.`, `_`, `+`, or `-`.

The script refuses a repository without a committed `HEAD`, any staged, unstaged, or untracked worktree change, and an invalid SHA. It resolves the full `HEAD` SHA and one UTC RFC3339 build time once, requires exactly 40 lowercase hexadecimal characters, and builds both `./cmd/server` and `./cmd/migrate` with the same `-trimpath`, `-buildvcs=false`, and linker values for:

- `internal/buildinfo.Version`
- `internal/buildinfo.GitSHA`
- `internal/buildinfo.BuildTime`

It builds both binaries into temporary sibling paths before changing either selected output. It then moves existing outputs to sibling backups, publishes each completed binary by same-directory rename, and restores the prior pair if later finalization fails. Two independent filesystem paths cannot be atomically replaced as one operation, so an abrupt process or filesystem failure during finalization may still require operator inspection; ordinary build failure cannot publish a new server with an old migrator. The script reports `release_server_output`, `release_migrator_output`, `release_git_sha`, `release_build_time`, and `release_version`. It does not accept or embed secrets.

The exact release invariant outside development is:

```text
configured BUILD_REVISION == embedded buildinfo GitSHA == GET /version git_sha
configured BUILD_REVISION == embedded migrator buildinfo GitSHA
```

The server and migrator reject startup/use outside `development` if the embedded SHA is missing or invalid, if `BUILD_REVISION` is missing or invalid, or if the two values differ. The VPS deployment flow must run `migrate up` with the migrator produced by this same canonical release build, never with an ordinary `go build ./cmd/migrate` binary. A release build is therefore not identified merely by an environment variable.

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

The value means the oldest binary schema version allowed to operate after that migration. It must be an integer satisfying `0 <= floor <= migration version`; a comment line using the reserved `evcowbbe:min-compatible-binary-version` prefix is always an attempted declaration, so there must be exactly one and it must use the exact syntax above with no trailing text. Missing, duplicate, malformed, negative, or out-of-range directives reject the migration file. The directive is part of the exact SQL bytes and therefore part of the SHA-256 checksum.

The `public.evcowbbe_schema_migrations` ledger is initialized only by:

```bash
go run ./cmd/migrate up
```

In a deployed release, run the release's migrator binary with the same runtime environment instead. `up` obtains a PostgreSQL session advisory lock, creates the ledger idempotently, verifies every already-applied migration against the embedded filename metadata and checksum, then applies each pending migration and ledger record in one transaction. A failed migration rolls back its SQL and does not gain a ledger entry. Concurrent `up` calls serialize on the same database-native lock.

The ledger records migration version, name, checksum, `applied_at`, `source_git_sha`, and `min_compatible_binary_version`. `migrate up` evolves the foundation ledger by adding the compatibility column if absent; it never deletes or recreates ledger history. Existing historical rows without this metadata fail verification rather than receiving an invented compatibility classification. A valid embedded SHA is stored when available. Honest local development builds with no real embedded SHA store `NULL`; they do not pretend that `dev` is a Git revision. Staging and production are rejected before migration work unless the release identity invariant holds.

For a migration the current binary knows, the ledger name, checksum, and compatibility floor must all exactly match the embedded migration. A known but unapplied migration is pending and blocks readiness. Migration history must remain ordered: if any applied version is higher than a migration known to the current binary that is missing from the ledger, readiness, verification, and `up` reject the ledger as a history conflict instead of applying the missing lower migration. For an applied migration newer than the current binary's schema version, an older binary cannot verify that future migration's SQL or checksum because it does not embed that source. It can only trust the durable, range-validated compatibility floor: it remains compatible if the floor is less than or equal to its own schema version, and becomes unready if the floor requires a newer binary. An unknown applied migration at or below the binary's schema version is always a history conflict and fails.

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

The future VPS framework should build a clean exact SHA into the paired server and migrator outputs, configure both processes with the matching `BUILD_REVISION`, run `migrate up` using that release migrator, start/reload the paired release server, and then verify both HTTP readiness and `/version` identity. Deployment verification fails if any release-identity check, migration command, process startup, liveness/readiness response, or `/version` SHA comparison fails.

The server handles `SIGINT` and `SIGTERM` by stopping new HTTP work and calling graceful shutdown for the configured timeout. Database pools close when the process exits.

Source CI is defined in `.github/workflows/ci.yml`. It checks formatting without changing source, `go mod verify`, tests, vetting, both command builds into runner-temporary paths, static migration validation, the canonical paired-release interface, both release-binary existence/executability, and that the release builder reports the workflow's exact `GITHUB_SHA`. It runs the release migrator against a bounded intentionally unreachable loopback PostgreSQL URL: matching `BUILD_REVISION` must reach the normal `database unavailable` result, while a syntactically valid mismatched revision must fail specifically at release-identity validation before database access. It does not deploy or publish an artifact. PostgreSQL integration tests remain gated solely by `TEST_DATABASE_URL`; they never fall back to `DATABASE_URL`. Source CI currently supplies no disposable PostgreSQL service, so database integration execution is an explicit separate verification boundary.
