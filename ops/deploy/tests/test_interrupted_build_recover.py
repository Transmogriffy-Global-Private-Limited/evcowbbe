"""End-to-end incident recovery logic against disposable SQLite and filesystem.

All host/PG effects are instrumented fakes: never access VPS2 or live DB.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from io import StringIO

from ops.deploy.control_plane import ControlPlane
from ops.deploy.executor.build_recovery_transition import finalize_attested_build_interruption
from ops.deploy.vps import interrupted_build_attestation as attest
from ops.deploy.vps import interrupted_build_host as host
from ops.deploy.vps import interrupted_build_ledger as incident
from ops.deploy.vps import interrupted_build_pg_fence as fence
from ops.deploy.vps import interrupted_build_coordinator as coordinator
from ops.deploy.vps import interrupted_build_recover as recover


@unittest.skipUnless(os.name == 'posix' and os.geteuid() == 0, 'POSIX root fixture required')
class RootRecoveryIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'journal.sqlite3'
        self.directory = Path(self.tmp.name) / 'evidence'
        self.directory.mkdir(mode=0o700)
        os.chmod(self.tmp.name, 0o700)
        self.cp = ControlPlane(self.db)
        request = None
        for i in range(3):
            sha = str(i + 1) * 40 if i < 2 else incident.CANDIDATE_SHA
            request = self.cp.submit_request({
                'event': 'push', 'branch': 'prod', 'sha': sha,
                'actor': 'source-ci', 'source_id': f'fixture-source-{i}',
            })
        with patch('ops.deploy.control_plane._new_id', return_value=incident.RUN_ID):
            run = self.cp.create_run(request['request']['id'], incident.CANDIDATE_SHA, 'evcow-orchestrator')
        self.cp.claim_run_execution(run['id'], incident.CANDIDATE_SHA, 'evcow-orchestrator')
        self.cp._transaction(lambda conn: self.cp._append_event(
            conn, 'executor_checkpoint', 'deployment_run', run['id'],
            {'stage': 'build', 'phase': 'intent', 'sha': incident.CANDIDATE_SHA,
             'executor_identity': 'evcow-orchestrator'}))
        self.events = []
        self.pings = 0

    @contextlib.contextmanager
    def observer(self, candidate, previous):
        self.events.append('executor-lock-acquired')
        try:
            yield host.FencedHostObservation(candidate, previous)
        finally:
            self.events.append('executor-lock-released')

    def ledger(self):
        self.events.append('postgres-ledger-observed')
        return incident.PinnedLedgerEvidence(
            incident.RUN_ID, incident.CANDIDATE_SHA, incident.PREVIOUS_SHA,
            incident.BACKUP_SHA256, incident.EMPTY_LEDGER_SHA256, 0,
        )

    def recheck(self):
        self.events.append('host-checked')

    def guarded_recheck(self):
        self.events.append('guarded-host-checked')

    @contextlib.contextmanager
    def guard(self):
        self.events.append('postgres-writer-fence-acquired')
        class FakeGuard:
            def ping(s):
                self.pings += 1
                self.events.append('postgres-writer-fence-ping')
                if getattr(self, 'fail_ping', 0) == self.pings:
                    raise fence.PgFenceBlocked('simulated lost PG session')
        try:
            yield FakeGuard()
        finally:
            self.events.append('postgres-writer-fence-released')

    def persist(self, preflight):
        self.events.append('evidence-persisted')
        return attest._persist(preflight, self.directory)

    def commit(self, digest, sequences):
        self.events.append('sqlite-commit-begin')
        return finalize_attested_build_interruption(
            self.cp, run_id=incident.RUN_ID,
            candidate_sha=incident.CANDIDATE_SHA,
            previous_sha=incident.PREVIOUS_SHA,
            expected_event_sequences=sequences,
            evidence_sha256=digest, operator=recover.OPERATOR,
        )

    def run_recovery(self):
        with patch.object(coordinator, 'DB', self.db):
            return recover._reconcile(
                observer=self.observer, ledger_verifier=self.ledger,
                guard=self.guard, host_recheck=self.recheck,
                guarded_host_recheck=self.guarded_recheck,
                persist=self.persist, commit=self.commit,
            )

    def test_success_records_audited_terminal_and_never_retries(self):
        result = self.run_recovery()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['result'], 'interrupted_build_reconciled_no_retry_authorized')
        self.assertLess(self.events.index('postgres-writer-fence-acquired'),
                        self.events.index('sqlite-commit-begin'))
        self.assertLess(self.events.index('evidence-persisted'),
                        self.events.index('sqlite-commit-begin'))
        self.assertLess(self.events.index('sqlite-commit-begin'),
                        self.events.index('postgres-writer-fence-released'))
        self.assertLess(self.events.index('postgres-writer-fence-released'),
                        self.events.index('executor-lock-released'))
        self.assertEqual(self.pings, 3)
        self.assertEqual(self.events.count('guarded-host-checked'), 3)
        self.assertLess(self.events.index('postgres-writer-fence-released'),
                        self.events.index('host-checked', self.events.index('postgres-writer-fence-released')))
        run = self.cp.status(verbose=True)['runs'][0]
        self.assertEqual(run['status'], 'failed')
        self.assertEqual(self.cp.desired_state(incident.CANDIDATE_SHA)['reason'],
                         'retry_authorization_required')
        self.assertEqual(len(list(self.directory.glob('*.json'))), 1)

    def test_pg_fence_fails_before_manifest_and_commit(self):
        @contextlib.contextmanager
        def unavailable():
            raise fence.PgFenceBlocked('advisory lock busy')
            yield
        with patch.object(coordinator, 'DB', self.db), self.assertRaises(fence.PgFenceBlocked):
            recover._reconcile(observer=self.observer, ledger_verifier=self.ledger,
                guard=unavailable, host_recheck=self.recheck,
                guarded_host_recheck=self.guarded_recheck,
                persist=self.persist, commit=self.commit)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_precommit_guard_loss_stops_before_sqlite(self):
        self.fail_ping = 2
        with self.assertRaises(fence.PgFenceBlocked):
            self.run_recovery()
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertNotIn('sqlite-commit-begin', self.events)
        self.assertEqual(len(list(self.directory.glob('*.json'))), 1)

    def test_post_release_full_readiness_failure_is_uncertain_not_success(self):
        def full_check():
            self.recheck()
            if 'postgres-writer-fence-released' in self.events:
                raise RuntimeError('normal readiness did not recover')
        with patch.object(coordinator, 'DB', self.db), self.assertRaises(recover.RecoveryCommitUncertain):
            recover._reconcile(observer=self.observer, ledger_verifier=self.ledger,
                guard=self.guard, host_recheck=full_check,
                guarded_host_recheck=self.guarded_recheck,
                persist=self.persist, commit=self.commit)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'failed')

    def test_guarded_host_recheck_failure_prevents_manifest_and_commit(self):
        def blocked():
            raise RuntimeError('runtime changed while fenced')
        with patch.object(coordinator, 'DB', self.db), self.assertRaises(RuntimeError):
            recover._reconcile(observer=self.observer, ledger_verifier=self.ledger,
                guard=self.guard, host_recheck=self.recheck,
                guarded_host_recheck=blocked,
                persist=self.persist, commit=self.commit)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_postcommit_guard_loss_is_reported_uncertain(self):
        self.fail_ping = 3
        with self.assertRaises(recover.RecoveryCommitUncertain):
            self.run_recovery()
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'failed')
        self.assertEqual(self.cp.desired_state(incident.CANDIDATE_SHA)['reason'],
                         'retry_authorization_required')

    def test_missing_or_untrusted_manifest_never_commits(self):
        def wrong(preflight):
            manifest = self.persist(preflight)
            from dataclasses import replace
            return replace(manifest, evidence_sha256='f'*64)
        with patch.object(coordinator, 'DB', self.db), self.assertRaises(recover.RecoveryExecutionBlocked):
            recover._reconcile(observer=self.observer, ledger_verifier=self.ledger,
                guard=self.guard, host_recheck=self.recheck,
                guarded_host_recheck=self.guarded_recheck,
                persist=wrong, commit=self.commit)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertNotIn('sqlite-commit-begin', self.events)

    def test_repeat_cannot_create_another_recovery_event(self):
        self.run_recovery()
        before = self.cp.history(100)
        with self.assertRaises(Exception):
            self.run_recovery()
        self.assertEqual(before, self.cp.history(100))

    def test_extra_sqlite_event_rejected_at_preflight(self):
        self.cp._transaction(lambda conn: self.cp._append_event(
            conn, 'host_invocation_claimed', 'deployment_run', incident.RUN_ID,
            {'stage': 'migration'}))
        with self.assertRaises(Exception):
            self.run_recovery()
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')

    def test_failure_after_persist_before_commit_leaves_orphan_attestation(self):
        def deny_commit(*args):
            raise RuntimeError('simulated SQLite inaccessible')
        with patch.object(coordinator, 'DB', self.db), self.assertRaises(recover.RecoveryCommitUncertain):
            recover._reconcile(observer=self.observer, ledger_verifier=self.ledger,
                guard=self.guard, host_recheck=self.recheck,
                guarded_host_recheck=self.guarded_recheck,
                persist=self.persist, commit=deny_commit)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertEqual(len(list(self.directory.glob('*.json'))), 1)
        self.assertIn('executor-lock-released', self.events)


class GuardProtocolContractTests(unittest.TestCase):
    def test_root_entrypoint_is_explicit_and_requires_acknowledgement(self):
        with patch.object(recover.sys, 'argv', ['recovery']), \
             patch.object(recover.sys, 'stderr', StringIO()), \
             patch.object(recover, 'recover_first_interrupted_build', side_effect=AssertionError('called')):
            self.assertEqual(recover.main(), 2)

    def test_guard_rejects_unbound_ledger_identity(self):
        class Output:
            def fileno(self):
                return 987
        class Process:
            stdout = Output()
            def poll(self):
                return None
        reply = b'{"state":"held","ledger_present":true,"applied":1,"fingerprint":"x"}\n'
        with patch.object(fence.os, 'set_blocking', create=True), \
             patch.object(fence.select, 'select', return_value=([987], [], [])), \
             patch.object(fence.os, 'read', return_value=reply):
            with self.assertRaises(fence.PgFenceBlocked):
                fence.Guard(Process())._read(1)

if __name__ == '__main__':
    unittest.main()
