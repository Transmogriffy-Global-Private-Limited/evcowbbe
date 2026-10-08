# EVCOWBBE deployment control-plane foundation

`ops/deploy` is Burner 1 source for a local-only deployment control-plane
state authority. It uses Python 3.12 standard library modules and a
caller-selected SQLite database. It has no network, subprocess, Git, build,
service, migration, release-link, application-environment, SMTP, or VPS
access capability.

Run the CLI from the repository root with an explicit state path:

```bash
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 status
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 submit \
  --event push --sha <40-lowercase-hex> --branch prod \
  --actor trusted-local-operator --source-id <unique-event-id>
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 pause \
  --operator trusted-local-operator --reason "maintenance window"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 sideline <sha> \
  --operator trusted-local-operator --reason "investigating failure"
python -m ops.deploy.cli --state-db /path/to/control-plane.sqlite3 resume \
  --operator trusted-local-operator --reason "maintenance complete"
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

`resume` releases pause and `release <sha>` releases an exact SHA sideline.
Neither command deploys anything. `rollback-hold <sha>` and
`rollback-release` are durable future rollback protections: while a rollback
hold is active, automatic reconciliation is blocked. Removing it resumes
selection from current durable state; it never replays an old request.

An admitted request is a local candidate, not proof that its SHA is current
remote `prod`. Normal `status` intentionally reports
`authoritative_prod_head_required` when candidates exist but no externally
verified SHA is supplied. `--trusted-prod-sha` is for controlled tests or a
future trusted executor only; this CLI cannot authenticate GitHub or inspect
the remote. Selection is exact: if the verified head is sidelined, deployment
is blocked and Burner 1 never falls back to an older accepted SHA.

SQLite permits exactly one active run globally. A planned run is fenced again
when it claims executable work, so a newly activated pause, sideline, rollback
hold, or changed verified head blocks/supersedes it safely. Burner 1 makes one
stable SHA-level attempt per selected request and does not retry it
automatically. Different ingress `source_id` values for the same SHA remain
audit history, not extra attempts. The first accepted event for a SHA is its
canonical request; later same-SHA events never relabel existing work. After a
failure, or cancellation after execution was claimed, `retry <sha> --operator
... --reason ...` creates one durable authorization that is consumed by a new
attempt for that same canonical request. A superseded or canceled-before-claim
run is safe pre-execution work and can be planned again if its SHA becomes the
verified head. A succeeded SHA requires future-executor runtime reconciliation
before another attempt because Burner 1 cannot infer actual release state. A
later verified fix-forward SHA can proceed once a failed run is terminal.
`cancel-retry <sha> --operator ... --reason ...` withdraws only an unused
authorization, preserves it as released audit history, and restores the retry
fence. Repeating cancellation is a no-op; a consumed authorization remains
historical and cannot be canceled retroactively.

## VPS2 ingestion stage

`ops/deploy/vps` is an installable-but-not-installed Ubuntu 24.04 package for
the next control-plane stage. It accepts only a successful Source CI candidate
for `prod` through a restricted `evcow-trigger` SSH key, writes it through the
existing SQLite control plane as `evcow-orchestrator`, then wakes a passive
systemd reconciliation inspection service. It is not an application deployer.

The SSH forced command permits only `evcowbbe-deploy-ingest-v1`; it has no
shell, PTY, forwarding, application configuration, release, migration, or
database-write authority. The fixed root bridge has only two effects: running
the root-owned admission entrypoint as `evcow-orchestrator`, then starting the
passive service. A persistent five-minute timer recovers a request committed
before a failed/missed wake. SQLite remains the only durable request store.

Install only through the separately authorized runbook in
`docs/DEPLOYMENT_CONTRACT.md`. The operator must provide an independently
verified VPS host key, SSH host/port, a protected GitHub private-key secret,
one public key, and a clean source checkout pinned to the control-plane SHA.
The package installs under `/opt/evcowbbe-deploy`, never under the application
release path `/srv/evcowbbe/current`.

The caller-provided operator identifier is audit attribution only. This local
source implementation assumes the invoking command boundary is trusted. A
future ingress/authentication layer must pass a verified operator identity.

The Python API offers migration-decision persistence but intentionally exposes
no migration execution. A future executor must derive an exact plan
fingerprint, obtain approval for that same SHA and fingerprint, recheck it
immediately before its irreversible migration commit boundary, and durably
claim that boundary before calling PostgreSQL. The fingerprint covers target
SHA, starting migration-ledger digest, and ordered migration identity/checksum
and compatibility data. Once claimed, the decision may be `executing` or
`uncertain`, and revocation is forbidden until the executor reconciles the
actual application ledger. Only an `approved` decision can be revoked;
executing, uncertain, no-commit, partial-commit, and committed decisions stay
immutable evidence. A revoked approval stays historical, while a later
permitted approval creates a fresh decision.

The executor must record a verified resulting application-ledger fingerprint
and the number of plan migrations that committed. Zero, partial, and complete
are distinct durable outcomes. A partial plan result fails the associated
deployment run but never pretends to undo already-applied migrations; that
exact plan cannot be reapproved, while a later corrective release can reconcile
forward from the real ledger.

Deployment-run failures are recorded as a bounded JSON object of safe,
operator-facing fields, such as `{"code":"BUILD_FAILED","message":"safe failure"}`.
Callers must not supply credentials, tokens, connection strings, raw command
lines, or other secrets in those details. Audit event details use the same
canonical JSON representation.

### VPS2 ingress installation status and wire format

This source includes a **passive-only** VPS ingestion package. It is not
installed by Source CI or any application release. See
`docs/DEPLOYMENT_CONTRACT.md` for the operator-authorized installation and
real-host acceptance runbook. Requests contain exactly one JSON object,
with no outside whitespace and optionally one LF terminator. Duplicate
JSON keys, extra objects, and arbitrary trailing data are forbidden.
The acknowledgement reports durable admission, **not deployment**.

The installer archives the approved clean Git SHA, stages immutable root-owned
code, verifies the existing orchestrator-owned SQLite state directory is
writable by the SQLite user, and installs restricted key-only SSH access
**last**. The public key is installed with an OpenSSH `restrict,command=...`
forced-command restriction and no Match/Include drop-in. The default
`authorized_keys` path and effective SSH configuration must be verified
against the actual VPS2; the installer refuses unsafe conflicts instead of
overwriting them. A manual check of SSH shell/PTY/forwarding denial and
forced-ingress behavior is mandatory before production use.

The passive reconciler reports historical `accepted_requests_total`,
`reconciliation_state: not_evaluated`, and `deployed: false`. It does not
pretend historical requests are pending deployments.

## Burner 3: verified authority / migration-plan preflight (source-only)

The `ops/deploy/executor` package contains the FIRST, strictly read-only
Burner 3 slice. It is **not** an executable application deployment pipeline and
is **not** installed or activated by Burner 2's passive service. There is no
new systemd unit, sudoers permission, VPS2 installer, secret, Git operation,
PostgreSQL mutation, or automatic application restart in this slice.

Contracts:

- `GitHubAuthority.verify()` fetches the authoritative `prod` HEAD from the
  authenticated private GitHub REST API, then requires completed, successful
  push-triggered **Source CI** from the exact repository for that exact SHA.
  HTTP/network failures, moved branch, missing CI, spoofed fork or different
  workflow all block. Re-verify close to every future irreversible boundary.
- `read_source_migrations` identifies every source SQL file by raw checksum,
  positive version and exact compatibility-floor declaration.
- `plan_migrations` compares these identities with the **actual durable**
  PostgreSQL migration ledger, including historical rows and future-compatible
  migrations. Missing/invalid ledger, checksums, compatibility, gaps, and
  unknown lower migrations fail closed. Each pending plan has a stable
  fingerprint binding exact SHA + starting ledger + pending identities.
- `require_exact_approval` consults existing Burner 1 decisions; a pending
  migration MUST be approved for that SHA, exact fingerprint, and plan count.
- `evaluate_preflight` inspects existing controls and produces a read-only
  `ready`/`blocked` assessment. It does not create runs, claim migration
  boundaries or execute anything. **Preflight ready is not deployment consent.**

The Go migrator now exposes `migrate status --json` as a machine-readable,
read-only observation of actual applied ledger rows. It preserves the existing
human-readable `status` command. For staging/production use the **canonical
paired release migrator with the matching `BUILD_REVISION`** and existing
runtime environment, not `go run`. Do not put `DATABASE_URL` into command
arguments or diagnostic output. Do not trust a hand-authored JSON file as an
independent DB observation for a real deployment.

Optional source-only CLI example (requires a safe operator-provided snapshot
and a separately provisioned read-only GitHub credential file):

```bash
python3 -m ops.deploy.executor.cli \
  --state-db /path/to/ORCHESTRATOR-COPY.sqlite3 \
  --migration-dir ./db/migrations \
  --ledger-json /path/to/trusted-migrator-ledger.json \
  --github-token-file /path/to/private-read-only-token
```

This CLI never deploys; do **not** install it as an automatic reconciler or
configure a GitHub token on VPS2 just for this source-only slice. Actual VPS2
integration still needs a reviewed privilege-separated executor service,
upgrade path for the already-installed `/opt/evcowbbe-deploy` release,
independent live ledger retrieval, durable run checkpoints and crash recovery,
source/build/activation and rollback implementation, and real host acceptance.

Run from the project root:

```bash
python3 -m unittest discover -s ops/deploy/tests -v
go test ./...
go vet ./...
go run ./cmd/migrate verify --files-only
```

## Burner 3 Patch 2: lifecycle core (NOT an installed executor)

`ops/deploy/executor/lifecycle.py` is an **inert, dependency-injected**
execution coordinator. It has no concrete `ExecutionPort`, no privileged
command dispatcher, no cron/systemd activation, no application secret reader,
and no ability to build, migrate, or restart anything on its own. It is
intentionally **not wired into** the installed passive reconciler.

It consumes the exact GitHub authority and PostgreSQL migration plan from
Patch 1, uses schema-v4 SQLite run states (no schema change or reset), and
journals before/after build, activation, runtime verification, and rollback in
append-only deployment events. A journal *intent* proves permission and
ordering, not that a side effect occurred. An *observed* checkpoint requires
the future trusted host adapter to positively validate the actual result.
Unfinished intent/active run after a crash blocks replay until explicit,
independent reconciliation; repeated invocations never repeat the same
external effect just because a service restarts.

The database migration boundary uses the existing durable
`claim_migration_boundary` / `reconcile_migration_ledger` sequence and an
actual observed **prefix** of the exact approved migration plan, including
pre-existing ledger identities. A failed migrator exit does not prove that no
SQL committed; a successful exit alone does not prove all SQL committed.
Unobservable or non-prefix metadata stays uncertain. Earlier durable SQL
changes are never rolled down; later SHA B can fix-forward from observed
ledger state after failed SHA A is terminally reconciled. Migration approvals
are SHA, count, and exact starting-ledger-plan fingerprint scoped.

On readiness/identity failure after observed activation, the coordinator first
persists a rollback hold, then requests rollback and independently verifies
that the previous binary is ready. A failed or incompatible rollback remains
held for human reconciliation. Other ambiguous failures likewise retain their
active run and must not be auto-replayed. The operator control plane remains
sole authoritative state, including pause, sideline, retry and rollback hold.

**Before enabling production execution**, implement and independently test a
privilege-separated, fixed-operation `ExecutionPort` that enforces exact SHA
and Source CI at the host boundary; clean Git/source provenance; correct
builder/deployer roles; paired binary identity; immutable releases; literal
configured service and database identities; PostgreSQL snapshot provenance;
secure migration credentials; atomic release-link activation; systemd restart;
bounded readiness/version checks; and guarded rollback. Add durable host-level
fencing against simultaneous executor processes and a verified manual
recovery/upgrade procedure. Then install via an atomic, state-preserving
control-plane upgrade with separate VPS2 authorization. Without those steps,
Patch 2 is **not** a ready-to-enable deployment executor.

### Burner 3, Patch 3: VPS2 host operation boundary (source-only)

`ops/deploy/executor/host/` defines a fixed, VPS2-specific execution port and a
root-only JSON operation bridge. **It is not enabled by merely committing or
upgrading source.** The accepted passive timer remains passive. No root sudoers
rule, systemd executor unit, root GitHub token, privileged bridge, build job or
automatic deployment is installed by the source patch or the code-only upgrade
script. Local tests use injected runners and do not claim VPS2 acceptance.

The bridge accepts exactly one bounded JSON object with `op` and `sha`, no
arbitrary path/command/unit/environment/shell strings. Its operations are
`source`, `ledger`, `build`, `present`, `active`, `activate`, `migrate`, `verify`
and `rollback`; `active` and `ledger` take `sha:null`, other operations require
a complete 40-character lowercase SHA. Production bridge execution requires
root and is intended to be invoked only through a future separately reviewed
**no-arguments** sudoers rule for `evcow-orchestrator`, never for `evcow-trigger`
or the app identity. The `evcow-builder` process has no PostgreSQL or app
credential access. `evcow-trigger` retains admission-only access.

Before any new privileged *forward* effect, the root bridge requires an active
durable SQLite execution intent, freshly verifies the exact upstream `prod`
HEAD and successful Source CI using a separate **read-only** GitHub credential,
and checks the builder checkout's fetched `prod` HEAD. This credential does not
exist yet at `/etc/evcowbbe-deploy/github-readonly.token`; absence must remain
a block, not permission to skip verification. Set it up only during a later
separately authorized deployment-executor installation.

**Burner 3 integration correction:** an operator pause, target sideline or
rollback hold introduced *after* a previously recorded build/activation intent
must block the next forward privilege-boundary operation. The root bridge
reads current SQLite controls again after external GitHub/checkout work and
immediately before the first switch mutation. A separately held rollback is
allowed to complete while paused, but requires its own exact target hold and
matching durable rollback intent. A pause cannot undo an OS operation that
already began; ambiguous cross-system races always require reconciliation.

**Version-independent ledger bootstrap:** the accepted live release
`2ac184c` predates `migrate status --json`. Never call its old migrator for a
machine ledger snapshot. The root-owned control-plane release instead bundles
a dedicated `evcowbbe-ledger-observer`, built offline from reviewed Git objects
by the code-only upgrader and run as `evcowbbe` through a fixed transient
systemd unit using the preexisting application EnvironmentFile. This observer
opens PostgreSQL read-only, coordinates with the application migration
advisory lock, and emits only ledger presence and exact applied identities.
Missing/obsolete compatibility metadata fails closed. It does not apply SQL,
change the application revision or require a new server release to exist.
The installed code-only upgrader must be tested on Ubuntu with the exact local
Go toolchain and already-cached dependencies before use; an offline build
failure stops without changing the current release pointer.

Builders generate the canonical paired binaries as `evcow-builder` under
`/srv/evcowbbe/build-work/prepared/<SHA>` from a clean detached checkout. The
privileged bridge stages them under `/srv/evcowbbe/releases`, verifies file
ownership and type, and publishes a root-owned immutable release with an exact
SHA and SHA-256 binary-pair manifest. Legacy root-owned releases can serve as
prior/rollback targets without pretending they have this new manifest.

The migrator runs under `evcowbbe` through a bounded transient systemd unit,
using a root-private temporary `EnvironmentFile` derived byte-for-byte from
`/etc/evcowbbe/dev.env` except for the exact `BUILD_REVISION`. It neither
sources the EnvironmentFile as shell code nor prints its secrets. Go itself
owns transactional migration execution and durable ledger checks. The
coordinator always independently reconciles actual ledger changes and must
refuse a missing ledger, a non-prefix partial result or incompatible rollback.

Activation and rollback require exact durable intents before touching system
state. A switch updates `BUILD_REVISION` by atomic file replacement, atomically
replaces the release symlink, then restarts `evcowbbe-dev.service`. These are
**not one atomic transaction**. Interruption after intent but before a verified
runtime result must remain a manual-reconciliation state, not automatic
success/replay. Rollback must first pass the previous binary's actual
`migrate verify` against PostgreSQL and never runs DOWN migrations. The
runtime check requires the running PID's executable path, service identities,
EnvironmentFile revision, `/health/ready`, and `/version` to match the exact
requested release.

`ops/deploy/vps/upgrade-control-plane.sh` is a separate *code-only*,
state-preserving, immutable release updater. It verifies a clean approved
source checkout, stages only committed source and compiles the dedicated
read-only ledger observer with `GOPROXY=off` and `GOTOOLCHAIN=local`, switches
`/opt/evcowbbe-deploy/current` atomically and runs the passive reconcile
smoke check. It must preserve existing SQLite state, the existing SSH key,
existing sudoers rules, systemd units, application release, PostgreSQL, and
all accepted ingestion history. Do not run it yet: verify the code on the
actual VPS2 host and review the privilege policy first. It does not install
any executor privileges, enable automated deployments or change `prod`.

**Remaining before enabling execution:** test the exact Ubuntu `systemd-run`
behavior and `EnvironmentFile` semantics against a safe rehearsal, verify
builder Git credentials under `evcow-builder`, inspect `systemctl show -p
ExecStart`, check the `current` symlink's literal target and the exact
`BUILD_REVISION` syntax without printing other secrets, test target/baseline
release compatibility, add and review a narrowly scoped root authorization
policy, validate upgrade/rollback of control-plane code preserving SQLite,
and run host-level failure, SIGKILL and fix-forward drills. All these are
explicit acceptance gates, not implied by green source tests.

### One-shot privileged execution claims

After a verified lifecycle intent, the trusted root host bridge must atomically
record `host_invocation_claimed` in the existing schema-v4 append-only SQLite
audit trail **before** invoking the external migration, activation, or rollback
operation. A previously claimed operation must not be dispatched again merely
because the acknowledgement, or the later observed checkpoint, is absent.
This is a conservative crash boundary: uncertain outcomes require real OS and
PostgreSQL observation and a separately approved recovery, not an automatic
replay. The bridge rechecks operator controls transactionally at claim time.
No schema reset, SQL DOWN migration, or application change is involved.

### VPS2 Go compiler visibility (first host acceptance)

The exact Ubuntu host installs Go at `/usr/local/go/bin/go`. The root bridge
runs subprocesses with an explicitly restricted environment, so the Go
compiler directory **must** be in that environment's fixed `PATH`; an
interactive root login's Go version proves nothing about the unprivileged
builder's inherited `PATH`. This was confirmed by a fail-closed
`evcow-builder` acceptance preflight on VPS2. The fixed path includes
`/usr/local/go/bin` and remains independent of the request payload. Confirm
that this compiler is owned by root, executable by `evcow-builder`, and that
the builder's module/cache locations are usable before a permitted build.
No application binaries, environments, GitHub branches, SQLite records, or
executor privileges change as part of this source-only correction.
