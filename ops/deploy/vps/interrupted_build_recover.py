"""Explicit root-only, incident-pinned, one-shot interrupted-build reconciliation.

NOT a scheduler/HTTP bridge/retry mechanism. Requires exact operator CLI
acknowledgement and an executor kernel flock. The observer, backup verifier,
PostgreSQL SHARE-table writer fence, durable manifest, and SQLite compare-and-
transition all run inside the same critical section. The SQLite write executes
as evcow-orchestrator, not root, to preserve WAL file ownership.

PostgreSQL + SQLite are NOT a distributed transaction: unexpected loss of the
PG guard during/after SQLite commit yields an UNCERTAIN operational outcome.
Do not authorize retry following any uncertain acknowledgement until an
independent ledger/runtime/journal review. Ordinary retries always require a
separate operator-control authorization.
"""
from __future__ import annotations

import contextlib
import json
import os
import select
import sys
from pathlib import Path
from typing import Callable

if os.name == 'posix':
    import pwd
    import signal
else:
    pwd = None
    signal = None

from ops.deploy.control_plane import ControlPlane
from ops.deploy.executor.build_recovery_transition import finalize_attested_build_interruption
from ops.deploy.vps import interrupted_build_host as host
from ops.deploy.vps import interrupted_build_ledger as incident
from ops.deploy.vps import interrupted_build_coordinator as coordinator
from ops.deploy.vps import interrupted_build_attestation as attestation
from ops.deploy.vps import interrupted_build_pg_fence as postgres

OPERATOR = 'root-reviewed-recovery'


class RecoveryCommitUncertain(Exception):
    """The SQLite commit might have happened; no retry is permitted."""


class RecoveryExecutionBlocked(Exception):
    """A recovery precondition failed before or during the critical section."""


@contextlib.contextmanager
def _already_fenced_observer(observed: host.FencedHostObservation):
    # The host context (and lock FD) is owned by the caller and stays open.
    yield observed


def _commit_as_state_owner(evidence_sha256: str, sequences: tuple[int, int, int]) -> dict:
    """Drop privilege in a short-lived fork while parent retains all fences.

    The subprocess is not an executable API. Failure or lost child response
    leaves the status uncertain: inspect SQLite before any future action.
    """
    if os.geteuid() != 0:
        raise RecoveryExecutionBlocked('only root can launch the recovery commit')
    user = pwd.getpwnam('evcow-orchestrator')
    rfd, wfd = os.pipe2(os.O_CLOEXEC)
    try:
        pid = os.fork()
    except BaseException:
        os.close(rfd)
        os.close(wfd)
        raise
    if pid == 0:
        os.close(rfd)
        try:
            os.setgroups([])
            os.setgid(user.pw_gid)
            os.setuid(user.pw_uid)
            result = finalize_attested_build_interruption(
                ControlPlane(str(coordinator.DB)), run_id=incident.RUN_ID,
                candidate_sha=incident.CANDIDATE_SHA,
                previous_sha=incident.PREVIOUS_SHA,
                expected_event_sequences=sequences,
                evidence_sha256=evidence_sha256, operator=OPERATOR,
            )
            raw = json.dumps(result, sort_keys=True).encode('ascii')
            if len(raw) > 2048:
                raise ValueError('unexpected commit result length')
            os.write(wfd, raw)
            os._exit(0)
        except BaseException:
            os._exit(75)
    os.close(wfd)
    try:
        ready, _, _ = select.select([rfd], [], [], 30)
        if not ready:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise RecoveryCommitUncertain('SQLite child timed out; inspect durable state')
        data = os.read(rfd, 2049)
    finally:
        os.close(rfd)
    _, status = os.waitpid(pid, 0)
    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        raise RecoveryCommitUncertain('SQLite child failed; inspect persisted state')
    try:
        result = json.loads(data)
    except (ValueError, UnicodeError) as exc:
        raise RecoveryCommitUncertain('SQLite commit acknowledgement lost') from exc
    if (type(result) is not dict or result != {
            'created': True, 'run_id': incident.RUN_ID, 'status': 'failed'}):
        raise RecoveryCommitUncertain('unexpected SQLite commit acknowledgement')
    return result


def _reconcile(*, observer: Callable = host.fenced_build_observation,
               ledger_verifier: Callable = incident.verify_pinned_first_build_ledger,
               guard: Callable = postgres.continuous_postgres_writer_fence,
               host_recheck: Callable = coordinator._verify_live_host_again,
               guarded_host_recheck: Callable = coordinator._verify_live_host_while_pg_fenced,
               persist: Callable = attestation.persist_first_build_evidence,
               commit: Callable = _commit_as_state_owner) -> dict:
    """Internal dependency-injection seam for disposable integration tests only."""
    if os.name != 'posix' or os.geteuid() != 0:
        raise RecoveryExecutionBlocked('Linux root authority required')
    with observer(incident.CANDIDATE_SHA, incident.PREVIOUS_SHA) as observed:
        if type(observed) is not host.FencedHostObservation:
            raise RecoveryExecutionBlocked('invalid host observation contract')
        preflight = coordinator._prepare(
            database=coordinator.DB,
            observer_context=lambda candidate, previous: _already_fenced_observer(observed),
            ledger_prover=ledger_verifier, host_recheck=host_recheck,
        )
        # Obtain continuous PostgreSQL writer exclusion *before* persisting
        # the manifest or committing the SQLite terminal transition.
        commit_attempted = False
        try:
            with guard() as pg:
                pg.ping()
                guarded_host_recheck()
                manifest = persist(preflight)
                if (type(manifest) is not attestation.DurableEvidenceManifest
                        or manifest.run_id != incident.RUN_ID
                        or manifest.evidence_sha256 != preflight.evidence_sha256
                        or manifest.result != 'durable_evidence_only_not_authorized_to_reconcile'):
                    raise RecoveryExecutionBlocked('manifest identity does not match preflight')
                # A second persistence call revalidates existing root-owned
                # manifest bytes and inode without replacing or modifying them.
                checked = persist(preflight)
                if checked.evidence_sha256 != manifest.evidence_sha256 or checked.created:
                    raise RecoveryExecutionBlocked('manifest changed before commit')
                pg.ping()
                guarded_host_recheck()
                # Crossing this boundary may commit. Every failure afterward is
                # operationally uncertain until independent SQLite examination.
                commit_attempted = True
                outcome = commit(preflight.evidence_sha256, preflight.event_sequences)
                try:
                    pg.ping()
                    guarded_host_recheck()
                    checked = persist(preflight)
                    if checked.evidence_sha256 != manifest.evidence_sha256 or checked.created:
                        raise RecoveryCommitUncertain('evidence changed after commit')
                except BaseException as exc:
                    raise RecoveryCommitUncertain('post-commit recheck failed') from exc
                result = {
                    'run_id': incident.RUN_ID, 'status': outcome['status'],
                    'evidence_sha256': manifest.evidence_sha256,
                    'result': 'interrupted_build_reconciled_no_retry_authorized',
                }
            # PostgreSQL advisory lock is released here. Only now is the
            # application's normal migration-aware /health/ready meaningful.
            # The root-owned executor flock remains continuously held.
            host_recheck()
            return result
        except BaseException as exc:
            if commit_attempted:
                raise RecoveryCommitUncertain(
                    'failure after commit boundary; inspect durable SQLite and ledger'
                ) from exc
            raise


def recover_first_interrupted_build() -> dict:
    """Only production API, no caller-selected paths, facts, ledger or SHA."""
    return _reconcile()


def main() -> int:
    # Explicit review gate. Never add this tool to a timer or executor bridge.
    if sys.argv[1:] != [
        '--finalize', incident.RUN_ID, incident.CANDIDATE_SHA,
        '--acknowledge-recovery-does-not-retry',
    ]:
        print('{"error":"explicit_incident_acknowledgement_required"}', file=sys.stderr)
        return 2
    try:
        result = recover_first_interrupted_build()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Exception:
        # This intentionally includes potential post-commit uncertainty.
        print('{"error":"recovery_blocked_or_uncertain_inspect_journal_before_retry"}', file=sys.stderr)
        return 7


if __name__ == '__main__':
    raise SystemExit(main())
