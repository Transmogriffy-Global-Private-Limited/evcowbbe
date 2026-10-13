# Burner 3 executor: installation, authority, operation, and safe recovery

**Status:** Patch 8 SOURCE PACKAGE ONLY. Committing this documentation or upgrading
control-plane code **does not** prepare, arm or authorize the executor. A separate
root operator reviews and executes both installation phases, after independent
host acceptance and provisioning a least-privileged GitHub read-only token. The
application and `prod` are not changed by this patch. This runbook is an
interim operational contract; a complete team-wide guidebook is a separate
mandatory pre-feature-development milestone.

## Invariants / authority

1. Only an accepted exact SHA currently at `prod` and with successful exact
   push-triggered Source CI may execute. Source CI and receipt of an SSH trigger
   are not themselves deployment authorization. The root-only GitHub token is
   never passed to the unprivileged `evcow-orchestrator` process: its
   `VPS2VerifiedAuthority` client receives only SHA, CI run ID/attempt, and
   branch through the one-operation fixed sudo bridge. It re-verifies the
   authoritative branch before every release boundary.
2. The root bridge always enforces durable SQLite intent and operator controls,
   plus exact GitHub authority and a fetched `prod` HEAD before mutating the
   filesystem or database. The orchestrator never obtains arbitrary root shell
   commands. The trigger key remains admission-only.
3. An exclusive `flock` on the root-owned, read-only file
   `/opt/evcowbbe-deploy/executor.lock` prevents overlapping executor starts,
   including manual invocation. **Never unlink or replace the lock file**:
   deleting it while an old process holds its inode defeats mutual exclusion.
   If the file is unsafe or absent, execution stops before creating a run.
4. The root-owned, world-readable (NON-secret) arming marker
   `/opt/evcowbbe-deploy/executor-arm` must contain exactly
   `evcowbbe-executor-v1 <installed-full-control-plane-SHA>` followed by LF,
   and mode 0444. A control-plane upgrade invalidates the marker. No implicit
   re-arming is permitted. This marker is **not** a substitute for GitHub
   authority, the durable SQLite fence, or per-stage checks.
5. Source/build/ledger/activation/migration/rollback occur through fixed
   operations, with explicit durable intent. A timeout, SIGKILL, or loss of
   network/SQLite/PID does not establish a negative result. An unfinished run
   remains manual-reconciliation-required; never re-send an operation simply
   because its process exited.

## Current live baseline before any executor authorization

As inspected on 13 October 2026, VPS2's accepted app release is
`2ac184c818db49dddf598984b68349d1d33e4c47`, the installed Patch 7 control
release is `0cf697f69348b0d2cefc98338e90a48c4d9a0b46`, schema-v4 SQLite
holds 2 admissions, 0 runs, 0 active controls, and the PostgreSQL ledger has 0
applied migrations. The server was healthy on `127.0.0.1:18280`. This is
historical evidence, **not** a permanent assumption: inspect again immediately
before installation and arming. A builder-owned prepared binary pair already
exists for the current app SHA. Do not try to publish over that active release.

## Preconditions and staged installation

- Review the exact committed Patch 8 SHA, successful Source CI, and source
  comparisons for the installed immutable control release. Check SSH trigger,
  application status and PID, `current` symlink, environment revision, SQLite
  integrity/backup, and PostgreSQL ledger. Check that no executor files, sudo
  rules, timers or arm marker have already been installed. Stop on conflicts.
- Pre-provision `/etc/evcowbbe-deploy/github-readonly.token` as a ROOT-owned,
  non-symlink regular file, mode 0600, holding an approved, fine-grained token
  with only the repository branch metadata and Actions **read** permissions
  required by `GitHubAuthority`. Never put the token in shell arguments,
  output, SQLite, ticket, chat, or the documentation. Provision and rotate it
  via a secure privileged credential-management workflow; DO NOT generate it
  automatically in an installer. Test the exact authenticated SHA + Source CI
  checks without logging the bearer token.
- First, after successful review and approval, as VPS2 root invoke the exact
  version-bound source script:

  ```bash
  bash /opt/evcowbbe-deploy/current/ops/deploy/vps/prepare-executor.sh <FULL_CONTROL_SHA>
  ```

  This **only** installs root-owned root-lock and disabled executor systemd
  service/timer files; it does not install sudoers, create arm marker, enable
  a timer, or touch the running app. Check `systemctl is-enabled` reports
  disabled, `systemctl is-active` reports inactive, `sudo -n -l` remains denied,
  and the marker is absent. `systemctl daemon-reload` is allowed here.
- Separately review exact sudoers syntax (`visudo -cf`), service sandbox,
  authenticated `prod` CI, backup, and fail-closed dry-run behavior using a
  disposable state snapshot. Confirm proper expiry/scopes of token.
- Only with a **separate positive operator authorization** invoke:

  ```bash
  bash /opt/evcowbbe-deploy/current/ops/deploy/vps/arm-executor.sh <FULL_CONTROL_SHA>
  ```

  This creates the exact no-arguments sudo rule and a release-bound,
  root-owned arming marker **but leaves the executor timer disabled**. The
  root bridge is now callable by `evcow-orchestrator`, so the privilege
  boundary is authorized; do not proceed without separate live host review.
  No deployment is initiated by this installer.
  Do NOT run this while another run is active, while a migration is uncertain,
  before token provisioning, or without approved rollback readiness.

### Controlled first execution and eventual automation

With the timer still disabled, perform supervised single-shot host acceptance
and verify the real `prod` authority, no overlapping process, no migration,
original app runtime, and any durable state changes. An actual positive-path
execution can deploy and restart an application. Start it only with explicit
operator consent using `systemctl start evcowbbe-deploy-executor.service`.
Inspect the unit journal, control-plane events, application PID, ledger and
release identity. For recovery tests, work with a disposable fixture; do not
manufacture fake approval records in the live SQLite DB.

**Only after real single-shot acceptance, separately authorize recurring
execution** and invoke:

```bash
bash /opt/evcowbbe-deploy/current/ops/deploy/vps/enable-executor-timer.sh <FULL_CONTROL_SHA>
```

This checks arm/sudoers/credential and activates the timer. Once enabled,
new prod admissions may cause real deployments without another manual start.
Never enable the timer during a pending uncertain state or before failure and
rollback acceptance has been completed.

## Systemd, privileges, and process fencing

The separate `evcowbbe-deploy-executor.service` is `Type=oneshot`, runs as
`evcow-orchestrator`, accepts no arguments and uses a root-owned executable.
Its unit needs `NoNewPrivileges=no` because `sudo` must legitimately execute
only the fixed root bridge. Never grant the trigger or builder this privilege.
The systemd sandbox must be tested on the actual VPS2: all bridge subprocesses
inherit relevant unit restrictions, so verify Git HTTPS, builder source fetch,
PostgreSQL transient observer, root-owned release staging, root-only temporary
migration environment, and systemd restart work under the real unit. A passing
manual root bridge test is **not** proof that the sandboxed executor works.
The exclusive lock is released by the kernel when the process terminates;
never implement a stale-lock cleanup that unlinks its inode.

The passive SSH-ingress reconcile timer remains separate and passive; it must
never be silently repurposed as an active deployment executor. The executor
has its own separately authorized timer.

## Expected outcomes and recovery

`DeploymentLifecycle.execute_once()` can report `blocked`, `already_current`,
`succeeded`, `failed`, `rolled_back_hold`, or
`manual_reconciliation_required`. A nonzero command exit or service failure
**does not** mean its requested OS/SQL effect did not occur.

When any operation is interrupted, fails after a durable intent, or a rollback
hold is active:

1. **Contain:** disable the executor *timer* to prevent new scheduling. Do
   not stop a currently executing unit or delete the lock without examining
   its effects. Preserve the operator-control state; use a durable pause or
   sideline if reviewed and safe. Do not delete SQLite WAL/SHM files.
2. **Back up:** take a consistent SQLite `sqlite3.Connection.backup()` copy,
   verify integrity, and preserve copies of the release manifest, relevant
   systemd journal, current symlink and non-secret environment revision. Do
   not print credentials or environment-file contents.
3. **Observe, never replay:** as root invoke the fixed read-only command

   ```bash
   /opt/evcowbbe-deploy/current/bin/evcowbbe-deploy-recovery-inspect
   ```

   It checks actual root release, service identity and health, and independent
   PostgreSQL ledger. Compare with SQLite run checkpoints,
   `host_invocation_claimed` records and operator controls. A pending
   activation `intent` does not prove activation occurred; a successful
   systemctl exit does not prove runtime health. A claimed migration requires
   exact SQL ledger reconciliation, including any committed prefix.
4. **Decide explicitly:** if output or metadata is inconsistent, retain the
   hold and escalate for reviewed manual reconciliation. Do not edit SQLite
   directly, delete a host-invocation claim, force an `observed` checkpoint,
   re-run a claimed SQL/activation/rollback command, apply down migrations, or
   overwrite an existing immutable release. A higher-SHA fix-forward or a
   compatible rollback must pass the exact remaining contract and receive
   explicit new intent; a rollback never assumes all SQL was undone.
5. **Restore service safely:** independently verify executable inode/path,
   environment revision, `/version` Git SHA, readiness, PostgreSQL ledger,
   active release link, and current operator controls before authorizing more
   automated work. Where no supported recovery API exists, stop and develop a
   specifically reviewed, test-backed reconciliation procedure; do not
   improvise state transitions in the live database.

Disarming for maintenance: before touching executor privilege, inspect for
an in-flight process and hold. Remove the root-owned arm *only with explicit
operator approval*, then disable the executor timer. Do not assume removing
that marker cancels an operation already in flight. Removing sudo authorization
requires a separate coordinated change and `visudo` validation.

## Acceptance gates still required

The Patch 8 source suite and inert template review are **not** proof of live
executor correctness. Host acceptance must separately demonstrate lock
contention, missing-token refusal, missing-arm refusal, remote SHA movement,
CI failure, current-state preservation, positive release under sandboxed
systemd, interrupted/malformed SQL ledger, exact rollback and recovery, and
finally an authorized end-to-end push-to-healthy deployment. Leave the executor
unarmed until those tests and explicit deployment consent exist. The later
full developer/ops documentation suite must incorporate verified results,
real SOPs and troubleshooting/recovery examples before backend feature work.
