"""K3B-4 isolated test suite: no production host or database mutations."""
from __future__ import annotations

import contextlib
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.deploy.control_plane import ControlPlane
from ops.deploy.vps import interrupted_build_coordinator as co
from ops.deploy.vps import interrupted_build_host as host
from ops.deploy.vps import interrupted_build_ledger as ledger


class CoordinatorReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'recovery.sqlite3'
        self.cp = ControlPlane(self.db)
        self.sha = ledger.CANDIDATE_SHA
        # 3 ingress events precede the three pinned run events at 4/5/6.
        request = None
        for index in range(3):
            request = self.cp.submit_request({
                'event': 'push', 'branch': 'prod',
                'sha': (str(index + 1)*40 if index < 2 else self.sha),
                'actor': 'source-ci', 'source_id': f'fixture-source-{index}',
            })
        with patch('ops.deploy.control_plane._new_id', return_value=ledger.RUN_ID):
            run = self.cp.create_run(request['request']['id'], self.sha, 'evcow-orchestrator')
        self.run_id = run['id']
        self.cp.claim_run_execution(self.run_id, self.sha, 'evcow-orchestrator')
        self.cp._transaction(lambda conn: self.cp._append_event(
            conn, 'executor_checkpoint', 'deployment_run', self.run_id,
            {'stage':'build','phase':'intent','sha':self.sha,'executor_identity':'evcow-orchestrator'}))
        self.assertEqual(tuple(x['sequence'] for x in reversed(self.cp.history(20)) if
                               x['entity_type'] == 'deployment_run'), (4, 5, 6))
        self.calls = []

    @contextlib.contextmanager
    def observed(self, candidate, previous):
        self.calls.append('lock_enter')
        self.assertEqual(candidate, self.sha)
        self.assertEqual(previous, ledger.PREVIOUS_SHA)
        try:
            yield host.FencedHostObservation(candidate, previous)
        finally:
            self.calls.append('lock_exit')

    def ledger(self):
        self.calls.append('postgresql')
        return ledger.PinnedLedgerEvidence(
            ledger.RUN_ID, ledger.CANDIDATE_SHA, ledger.PREVIOUS_SHA,
            ledger.BACKUP_SHA256, ledger.EMPTY_LEDGER_SHA256, 0,
        )

    def recheck(self):
        self.calls.append('host_recheck')

    def invoke(self, **kwargs):
        opts = {'database': self.db, 'observer_context': self.observed,
                'ledger_prover': self.ledger, 'host_recheck': self.recheck}
        opts.update(kwargs)
        with patch.object(co.os, 'geteuid', return_value=0, create=True), \
             patch.object(co.os, 'name', 'posix'):
            return co._prepare(**opts)

    def test_success_is_read_only_rechecked_and_digest_reproducible(self):
        original = self.cp.history(50)
        outcome = self.invoke()
        self.assertEqual(outcome.result, 'read_only_preflight_not_authorized_to_transition')
        self.assertEqual(outcome.event_sequences, (4, 5, 6))
        self.assertEqual(self.calls, ['lock_enter', 'postgresql', 'host_recheck',
                                      'postgresql', 'lock_exit'])
        self.assertEqual(original, self.cp.history(50))
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')
        self.assertEqual(outcome.evidence_sha256, self.invoke().evidence_sha256)

    def test_wrong_host_observation_refuses(self):
        @contextlib.contextmanager
        def bad(a, b):
            yield host.FencedHostObservation(b, a)
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke(observer_context=bad)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')

    def test_mismatched_ledger_refuses(self):
        def bad_ledger():
            return ledger.PinnedLedgerEvidence(
                ledger.RUN_ID, ledger.CANDIDATE_SHA, ledger.PREVIOUS_SHA,
                'f'*64, ledger.EMPTY_LEDGER_SHA256, 0,
            )
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke(ledger_prover=bad_ledger)

    def test_changed_ledger_between_reads_refuses(self):
        calls = 0
        def changed():
            nonlocal calls
            calls += 1
            record = self.ledger()
            if calls == 2:
                return ledger.PinnedLedgerEvidence(
                    record.run_id, record.candidate_sha, record.previous_sha,
                    record.archive_sha256, record.ledger_fingerprint, 1,
                )
            return record
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke(ledger_prover=changed)
        self.assertEqual(self.calls[-1], 'lock_exit')

    def test_unexpected_extra_run_event_refuses(self):
        self.cp._transaction(lambda c: self.cp._append_event(
            c, 'host_invocation_claimed', 'deployment_run', self.run_id,
            {'stage': 'migration'}))
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke()

    def test_active_pause_refuses(self):
        self.cp.pause('operator', 'maint')
        with self.assertRaises(ValueError):
            self.invoke()

    def test_migration_decision_refuses(self):
        def insert(conn):
            conn.execute(
                "INSERT INTO migration_decisions (id,sha,plan_fingerprint,required_migration_count,"
                "state,approved_by,approval_reason,approved_at) VALUES (?,?,?,?,?,?,?,?)",
                ('decision', self.sha, 'f'*64, 1, 'approved', 'operator', 'review', '2026-10-15T00:00:00Z'),
            )
        self.cp._transaction(insert)
        with self.assertRaises(ValueError):
            self.invoke()

    def test_extra_source_request_refuses(self):
        self.cp.submit_request({'event':'push','branch':'prod','sha':self.sha,
            'actor':'source-ci','source_id':'extra-source'})
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke()

    def test_missing_sqlite_file_refuses_without_creation(self):
        missing = self.db.with_name('missing.sqlite3')
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke(database=missing)
        self.assertFalse(missing.exists())

    def test_recheck_failure_keeps_sqlite_running(self):
        def fails():
            raise co.CoordinatorBlocked('simulated process appeared')
        with self.assertRaises(co.CoordinatorBlocked):
            self.invoke(host_recheck=fails)
        self.assertEqual(self.calls[-1], 'lock_exit')
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')

    def test_requires_linux_root_even_with_mocks(self):
        with patch.object(co.os, 'geteuid', return_value=1000, create=True), \
             patch.object(co.os, 'name', 'posix'):
            with self.assertRaises(co.CoordinatorBlocked):
                co._prepare(database=self.db, observer_context=self.observed,
                            ledger_prover=self.ledger, host_recheck=self.recheck)


if __name__ == '__main__':
    unittest.main()
