# Burner 3 interrupted-build recovery contract (Patch 9, work in progress)

## Purpose and current incident

The first supervised positive deployment (run `601f52ab46ed40e4add026a6b50e7d61`, candidate `ddea3882d1b98d10506a959478e65a608136a01c`) stopped after `run_created`, `run_execution_claimed`, and `build/intent`. There is no durable `build/observed`, activation intent, privileged activation claim, or SQL migration. The last independently verified runtime is `2ac184c818db49dddf598984b68349d1d33e4c47`. An unchanged runtime alone does **not** prove no build side effect occurred.

Do not retry the executor, manually edit SQLite, delete candidate artifacts, promote another revision, clear the arm marker, or enable unattended scheduling merely because these facts were observed. Preserve the failed-run checkpoint and host logs.

## Component boundaries

* `ops/deploy/executor/build_recovery.py` is **pure assessment logic only**. It cannot inspect the host, connect to SQLite, authorize retry, or finalize a run. It returns `preconditions_met_not_authorized`, never `safe_to_retry` or a commit token.
* A future **privileged read-only evidence collector** must supply its required facts from actual host checks, never untrusted JSON, a human-supplied boolean, or the presence/absence of an individual path alone.
* A future **recovery coordinator** must own the same kernel flock as the executor for the entire observation/transition critical section and prove all subprocess/cgroup activity has finished. The ordinary execution coordinator must remain unable to replay outstanding intents.
* A future **SQLite recovery transition** must atomically compare the exact live run, events, request identity, active controls, migration decisions, and observed host-evidence fingerprint before it appends a recovery event and changes the run to a terminal state. Any mismatch must roll back the transaction. It must neither rewrite nor delete preexisting history.
* A terminal failed run does not automatically authorize a retry; current control-plane retry authorization remains a distinct explicit operator decision.

## Positive eligibility requires all evidence, simultaneously

1. Exact run ID, candidate Git SHA, different previous Git SHA, unique active run, attempt one, status `running`, and a real execution claim.
2. The complete run-specific durable history is exactly the three expected ordered events and the singular unmatched `build/intent` (including exact SHA and executor identity). There must be no activation, rollback, runtime-verification, or migration invocation record. Verify global cross-run and SQL histories separately.
3. No migration decisions or active operator controls; actual PostgreSQL ledger must match the independently verified pre-build baseline. A ledger count alone is insufficient when migrations exist.
4. Executor service inactive, timer disabled and inactive, no remaining child, transient, cgroup, or builder process relevant to this invocation, and exclusive executor lock continuously held by the authorized recovery coordinator.
5. No candidate prepared pair, immutable release, or temporary output/staging material, including symlinked or renamed collision paths. Inspect all relevant parent directories without following untrusted symlinks; check ownership and modes.
6. Previous release is the actual active binary with independently verified PID, revision, readiness, service user/group and configuration. No restart or activation is permitted in this recovery branch.
7. Trusted root-side evidence is captured **while fenced**, not accepted as externally submitted booleans. Recover only if every positive assertion succeeds. Keep the fence until a fresh recheck and atomic commit complete. Locking alone does not prevent unrelated root activity, so other writers and upgrade paths must also honor the recovery fence.

## Negative tests required before implementing any mutation

Reject missing/duplicated/reordered events; wrong run or SHA; concurrent or terminal runs; migration boundary or SQL decisions; any activation or rollback claim; any side-effect artifact; a live builder, stale cgroup, or occupied lock; changed production branch, runtime, ledger or control-state data; symlinks or unsafe ownership; truncated observations; unauthorized operator; races between observation and SQLite transition; concurrent recovery; DB write or fsync failures; and a crash between durable recovery intent and completion. A repeated recovery call must never reinterpret a terminal run as another candidate for action.

## Upgrade and activation constraints

The installed root-owned control-plane release and executor-arm marker are bound to an exact SHA. A Patch 9 code-only upgrade is not a direct application release deployment, and must preserve SQLite, PostgreSQL, ingress, sudoers, unit templates, and app runtime. A new executable is **not** authorized merely by switching the control-plane symlink. Reconcile the interrupted run before trying ordinary re-arming, or use a separately reviewed recovery-specific authority boundary; do not bypass an existing arm marker or broaden the fixed root bridge.

## Evidence and rollout status

This document and the accompanying pure assessor/tests are **Phase 1: source-only specification**. They do not perform recovery. No commit, production promotion, or VPS2 installation is authorized by this patch. The previously recorded backup remains protected at `/root/evcowbbe-deploy-backups/failed-first-deployment-QJERjpbP/orchestrator.sqlite3` with SHA-256 `977f481e969db4034d093ad00956e16e3d962ca17a1d887e421296e905e3ab5f`.

Phase 2 must implement the trusted host collector; Phase 3 implements the fenced atomic transition and complete negative/concurrency tests; Phase 4 verifies idempotent upgrade/re-arming sequencing; Phase 5 is isolated host acceptance and separate explicit human authorization for any live mutation.

## Phase 2: host evidence collector (Step 25-K2, source-only)

`ops/deploy/vps/interrupted_build_host.py` supplies a root-only context manager (`fenced_build_observation`) with the **same exact immutable kernel-lock path** as the executor. It refuses a second lock holder, a live/ambiguous executor service, an enabled/active timer, detected builder/executor processes, candidate-named published or temporary artifacts (including dangling links), unsafe build/release directory ownership or group/world writability, and nonmatching or unready old runtime. It takes two independent process/artifact/systemd snapshots around runtime identity verification. It does not alter state.

Its returned `FencedHostObservation` contains only candidate/prior SHA and the literal status `host_quiescence_only_not_authorized_to_reconcile`. It does **not** return the K1 assessor's required host boolean map, compare the PostgreSQL ledger to a trustworthy pre-build identity snapshot, authorize retry, perform a SQLite write, or finalize any run. It is authoritative only inside the live `with` block while the file descriptor remains open. No executable root CLI, sudoers or external request boundary is added by this phase. The injected test helpers must not become user-facing arguments in later integrations.

Important limitations: kernel flock fences cooperators using this exact lock inode, not arbitrary privileged file edits. A `/proc` scan and systemd state inspection cannot by themselves prove that an unrelated root actor is not racing the deployment. The future coordinator must additionally guard upgrades/writers, verify the full PostgreSQL ledger identities against immutable pre-build evidence, atomically inspect and transition the exact SQLite run within the fenced critical section, and fail closed on cross-system races. The active release also has to remain verified until the commit. Never treat this collector alone as success or permission to retry.


## Phase 3A: pinned SQL-ledger evidence (Step 25-K3B-3, source-only)

The pre-build PostgreSQL custom archive was verified independently (SHA-256 `ba94bf6b97e6187790a587dc68ce4f03e58a4715f7b0ca370bf5c46ff7bdc5e1`) and its migration table found **present with zero applied migrations**. A separate read-only live observer independently found the table present and an identical canonical ledger fingerprint (`4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945`). These are **observations**, not permanent guarantees.

`ops/deploy/vps/interrupted_build_ledger.py` pins the precise original run, candidate and previous SHA, root-only backup archive path, archive SHA-256 and pre-build empty-ledger fingerprint. It rejects unexpected file types/permissions, broken or changed backup bytes, unreadable/partial observer output, missing ledger, and any applied migration. The existing reviewed ledger observer obtains a read-only repeatable-read PostgreSQL snapshot coordinated with the migration advisory lock. Do not substitute an HTTP health check, SQL row count from a different table, or caller-provided boolean for these facts.

This component does not contain a root entrypoint, executor invocation, SQLite mutation, or authorization to retry. It is intentionally **incident-scoped**; a different deployment or nonempty pre-build ledger requires a separately verified, immutable baseline manifest and a more general, reviewed comparator. It must be invoked by the future coordinator under the continuously held executor kernel lock and should be accompanied by a fresh host recheck. Neither the kernel lock nor a read-only ledger snapshot guarantees exclusion of unrelated privileged PostgreSQL writers; the coordinator's operational preconditions and failure model must explicitly address this cross-system limitation before any live transition.

## Phase 3B-4: read-only integrated preflight, **not** a recovery capability

`ops/deploy/vps/interrupted_build_coordinator.py` connects the existing K2
kernel-flock observer, K3B-3 archive-and-live-ledger verifier and K1 pure
assessor. It performs two root-authoritative live ledger checks and an
additional host recheck **while the executor lock is held**, checks the exact
observed three ingress requests and run event sequence `(4,5,6)`, and returns
canonical nonsecret evidence JSON with an SHA-256 reference. It opens SQLite
in read-only mode and cannot call the K3A terminal transition, persist an
attestation, add a retry, or produce a root executable. There is intentionally
no live invocation step at this phase.

**Important cross-system atomicity gap:** the existing Go PostgreSQL observer
releases its advisory lock and read-only snapshot before returning. Consequently,
passing this preflight does NOT prove that another privileged DB writer could
not change the migration ledger before a later SQLite commit. We will not
connect this result to the K3A mutation until a reviewed continuous writer-fence
and a durable root-owned evidence manifest exist, with failure injection for
crashes between manifest fsync, SQL observation and SQLite transition. No
unattended recovery or general-purpose migration skip is authorized.

## Phase 3B-5A: durable immutable evidence artifact (source only)

`ops/deploy/vps/interrupted_build_attestation.py` implements an incident-pinned
root-owned evidence writer, **not** a privileged recovery entrypoint. The future
coordinator must obtain a fresh preflight while holding the executor lock **and**
verify continuous PostgreSQL writer exclusion before it invokes this writer.
The writer accepts only exact canonical K3B-4A incident metadata, validates
its SHA-256 and bounded format, and publishes bytes under the root-owned
`/opt/evcowbbe-deploy/recovery-attestations/` directory. That directory must be
separately provisioned as root:root mode `0700`, under an existing safely owned
root directory. Never place this artifact in the orchestrator-writable SQLite
state directory or in the source/build workspace.

Publication uses private exclusive temporary files, file `fsync`, no-overwrite
hard linking to an incident-and-digest-named final file, directory `fsync`, and
full readback with owner/mode/inode/link-count checks. Existing identical
immutable evidence is idempotent; conflicts, links, a missing directory, or
write/durability failures block. Crash-after-link can leave a valid manifest
without a SQLite transition; an orphan manifest **never** proves the run was
closed and **never** authorizes a retry. The K3A transition records only its
SHA-256 audit reference, so the final coordinator must require the matching
manifest throughout its SQLite critical section and retain the exact bytes
for forensic verification. The writer itself does not touch SQLite or PostgreSQL.

**Outstanding safety gate:** the existing read-only PG observer does not hold
a writer fence across the SQLite transaction. Do not invoke the K3A transition
or add a root executable until a reviewed continuous writer-exclusion protocol,
including table writes by actors outside the application migrator, is tested
and connected. Even a cross-system guard requires explicit failure semantics
for abrupt PostgreSQL session loss; it is not a distributed transaction.

## Phase 3B consolidated: Explicit incident recovery coordinator (Patch 9)

The integrated implementation is **root-only and incident-pinned**. It has no
scheduler or HTTP route. The explicitly acknowledged CLI wrapper is shipped by
the exact source-SHA-bound immutable control-plane upgrade and is not installed
or executed by a local source patch. There is no user-selectable SQL query,
PostgreSQL credential, filesystem path, run ID, or candidate SHA.

**Protocol:** acquire the executor's root-owned non-replaceable flock;
require executor service inactive and timer disabled; verify no builder process,
symlink, prepared, release or candidate staging entry; verify original running
binary/revision/readiness; twice compare the original root-owned predeployment
PostgreSQL backup and live empty ledger; recheck SQLite run/request/audit and
migration decisions; acquire a separate PostgreSQL session-level migration
advisory lock **and** transaction-level `SHARE` lock on the exact migration ledger
(`NOWAIT`), confirming it remains empty; persist and read back an immutable,
canonical evidence manifest while those locks remain held; recheck host and
PostgreSQL guard liveness; fork a short-lived child and drop to the real
`evcow-orchestrator` UID/GID for a single `BEGIN IMMEDIATE` guarded audit-and-
terminal transition; then recheck guard, host and immutable evidence before
releasing the locks. Neither ingress nor a timer invokes this path.

**PostgreSQL exclusion guarantees and limits:** the matching advisory lock
excludes all migrations that honor the application's existing lock ID, and the
`SHARE` table lock conflicts with ordinary `INSERT`, `UPDATE`, `DELETE` and
`TRUNCATE`/DDL on the specific ledger table. This protects the migration ledger,
not every possible application table or a malicious superuser. The long-lived
hold is provided by a no-argument Go sidecar launched under the existing
`evcowbbe` identity by systemd with its established EnvironmentFile. It replies
only with an exact pinned ledger identity and bounded `ping` acknowledgements.
EOF or abnormal process/session loss fails closed. **PostgreSQL and SQLite are
not a distributed transaction:** a guard loss in the tiny interval surrounding
a committed SQLite write can yield an *uncertain result*. In that case, do not
retry or infer rollback; inspect the actual durable SQLite audit, manifest,
PostgreSQL ledger and old application state before any separately authorized
retry. Explicit retry remains gated by the existing operator-control state.

**Source-only status:** the implementation and tests do not authorize a live
reconciliation. Before any installation or execution, review and separately
approve the immutable source commit, independent Ubuntu 24.04 test deployment
of the streamed systemd/Go handshake and table-lock exclusion, Go build and
static analysis, root/UID/WAL permissions, backup integrity, exact `systemctl`
state, trust boundary, verification of production unit templates, and a
rollback plan for an interrupted code-only upgrade. The previous application
must stay running; the root-only evidence directory at
`/opt/evcowbbe-deploy/recovery-attestations` must be independently provisioned
(root:root 0700) by the reviewed source script **only when explicitly approved**.
The upgrade must take the immutable executor lock throughout its symlink
switch, never enable a timer, and leave the old SHA arm invalid for new code.

**Source verification minimum:** Python suite in Windows and Linux (review all
platform skips); Go `mod verify`, tests and vet; shell syntax; source diff and
expected file scope; isolated Postgres test that (1) table writes block while
the guard holds SHARE, (2) second migrator cannot obtain the advisory lock,
(3) unexpected loss of guard refuses recovery, and (4) normal app queries stay
available. Then separately review the host installation and one-time recovery
invocation. `provision-recovery-evidence.sh` is a separately authorized root
operation; it never runs as part of an ordinary app deployment.

**Post-reconciliation is not re-arm authorization.** The prior `executor-arm`
and sudoers may still exist and are bound to the old control code; the existing
`arm-executor.sh` refuses those artifacts. Re-arming requires an independent,
fully reviewed replacement/rotation protocol, explicit retry approval for the
candidate SHA, and no automatic execution. Preserve old backups and evidence.
