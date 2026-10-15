# Patch 9 — isolated Linux recovery-integration acceptance

## Purpose and boundary

This source-only package addresses a PostgreSQL writer-fence subprocess
protocol issue: buffered `.readline()` can block after `select()` when a child
writes a partial frame without a newline. The implementation uses bounded,
nonblocking reads with a monotonic deadline; it refuses partial, oversized,
malformed, extra-byte, EOF and exited-child responses. A positive response
must match the incident-pinned migration ledger identity.

**It does not deploy, install, finalize a run, arm the executor or authorize a
retry.** VPS2 state remains frozen. PostgreSQL and SQLite do not form a
single distributed transaction.

## Environment and isolation

Source CI gains one independent job, `recovery-linux-integration`, running on
a GitHub-hosted Ubuntu 24.04 runner with an ephemeral Docker `postgres:16`
service. The fixture is **only** database `evcowbbe_recovery_ci`, at
`127.0.0.1:5432`, user `recovery_ci` and password `recovery_ci`. These are
nonproduction credentials. The tagged Go test refuses to run unless the
explicit isolated-test flag, URL host, database and credential all match.
The CI job has only `contents: read`, no production SSH keys, and no VPS
actions. Existing Source CI keeps its push/pull_request triggers; the
separate ingestion workflow acts only on successful Source CI pushes to
`prod`. Test only on the feature branch.

## Acceptance cases

* Linux pipe: partial messages time out, split messages succeed only before
  the deadline; extra bytes, oversized messages and EOF are rejected.
* Real PostgreSQL: exact migration advisory lock, table SHARE lock, concurrent
  INSERT/ALTER exclusion, lock release, refusal of competing locks, refusal
  of a nonempty ledger, controller EOF and database connection termination.
* Real systemd-run: transient actual Go recovery guard verifies `--quiet --pipe --wait
  --collect` bidirectional stdin/stdout streaming. This never launches the
  recovery coordinator or connects to the production database.
* Static workflow: the isolated job must not contain production secrets or
  deployment calls; all fixture targets must remain explicit.

## Procedure and recovery

1. Apply the reviewed patch on the existing Patch 9 feature branch after
   verifying unchanged baseline and patch hash; review all changed files.
2. Run Python/Go regressions, Go module verification/vet/formatting and Bash
   syntax locally. Platform-specific tests may skip on Windows.
3. Commit only after inspecting expected file scope, then push **only**
   `fix/patch9-builder-recovery`, subject to explicit approval. Do not merge
   or push to `prod` for these tests.
4. Require both Source CI jobs, `verify` and `recovery-linux-integration`, to
   pass on the exact feature-branch SHA. Retain the run URL/logs and evidence.
5. Any failing, timed-out or unexpectedly skipped PostgreSQL/systemd job
   blocks acceptance. Investigate in the disposable runner and rerun; never
   substitute the production database or VPS2 as a test environment.

These tests do not replace later Ubuntu VPS permissions, upgrade/rearm and
recovery procedure reviews. Any live reconciliation is a distinct explicitly
authorized operation.
