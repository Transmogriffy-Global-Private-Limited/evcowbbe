"""Disposable SQLite tests: no real host, PostgreSQL, restart or deployment."""
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from ops.deploy.control_plane import ControlPlane, ExecutionBlocked
from ops.deploy.executor.build_recovery_transition import (
    RECOVERY_KIND, RECOVERY_OUTCOME, finalize_attested_build_interruption,
)

A = 'a'*40
B = 'b'*40
EVIDENCE = 'e'*64


class RecoveryTransitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cp = ControlPlane(str(Path(self.tmp.name)/'state.sqlite3'))
        req = self.cp.submit_request({
            'event': 'push', 'branch': 'prod', 'sha': A, 'actor': 'source-ci',
            'source_id': 'build-recovery-test-source',
        })
        r = self.cp.create_run(req['request']['id'] if 'request' in req else req['id'], A, 'evcow-orchestrator')
        self.run_id = r['id']

        def seed(conn):
            conn.execute("UPDATE deployment_runs SET status='running', execution_claimed_at='2026-10-15T00:00:00Z' WHERE id=?", (self.run_id,))
            self.cp._append_event(conn, 'run_execution_claimed', 'deployment_run', self.run_id,
                                  {'executor_identity': 'evcow-orchestrator', 'trusted_prod_sha': A})
            self.cp._append_event(conn, 'executor_checkpoint', 'deployment_run', self.run_id,
                                  {'stage':'build', 'phase':'intent', 'sha':A, 'executor_identity':'evcow-orchestrator'})
        self.cp._transaction(seed)
        self.pre_events = [x for x in reversed(self.cp.history(50)) if x['entity_type'] == 'deployment_run']
        self.seq = tuple(x['sequence'] for x in self.pre_events)
        self.assertEqual(len(self.seq),3)

    def call(self, **changes):
        opts = {'control':self.cp,'run_id':self.run_id,'candidate_sha':A,
                'previous_sha':B,'expected_event_sequences':self.seq,
                'evidence_sha256':EVIDENCE,'operator':'root-recovery'}
        opts.update(changes)
        return finalize_attested_build_interruption(**opts)

    def test_success_is_atomic_audited_and_explicit_retry_still_required(self):
        before = self.cp.history(100)
        result = self.call()
        self.assertEqual(result, {'created':True,'run_id':self.run_id,'status':'failed'})
        run = self.cp.status(verbose=True)['runs'][0]
        self.assertEqual((run['status'], run['outcome']),('failed',RECOVERY_OUTCOME))
        after = self.cp.history(100)
        self.assertEqual(len(after),len(before)+2)
        self.assertEqual(after[0]['kind'],'run_transitioned')
        self.assertEqual(after[1]['kind'],RECOVERY_KIND)
        self.assertEqual(after[1]['details']['evidence_sha256'], EVIDENCE)
        self.assertEqual(after[2:],before)
        self.assertNotIn('retry', [x['type'] for x in self.cp.status()['active_controls']])
        desired = self.cp.desired_state(A)
        self.assertEqual((desired['state'], desired['reason']),
                         ('blocked', 'retry_authorization_required'))

    def test_identical_repeat_is_read_only(self):
        self.call()
        before = self.cp.history(100)
        self.assertEqual(self.call()['created'], False)
        self.assertEqual(self.cp.history(100),before)
        with self.assertRaises(ExecutionBlocked):
            self.call(evidence_sha256='f'*64)

    def test_changed_event_sequence_rejected_without_mutation(self):
        before = self.cp.history(100)
        with self.assertRaises(ExecutionBlocked):
            self.call(expected_event_sequences=(1,2,3))
        self.assertEqual(self.cp.history(100),before)

    def test_extra_checkpoint_or_host_invocation_blocks(self):
        self.cp._transaction(lambda conn: self.cp._append_event(
            conn, 'host_invocation_claimed', 'deployment_run', self.run_id, {'stage':'activation'}))
        before = self.cp.history(100)
        with self.assertRaises(ExecutionBlocked):
            self.call()
        self.assertEqual(before,self.cp.history(100))

    def test_active_operator_pause_blocks(self):
        self.cp.pause('operator','maintenance')
        with self.assertRaises(ExecutionBlocked):
            self.call()
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'], 'running')

    def test_wrong_previous_candidate_or_fingerprint_rejected(self):
        for kw in ({'previous_sha':A}, {'candidate_sha':B}, {'evidence_sha256':'q'*64},
                   {'run_id':'wrong'}, {'expected_event_sequences':(1,1,2)}):
            with self.subTest(kw=kw), self.assertRaises(Exception):
                self.call(**kw)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'],'running')

    def test_transaction_error_rolls_back_entire_operation(self):
        def fail(*args, **kwargs):
            raise ExecutionBlocked('forced write failure after append')
        before = self.cp.history(100)
        with patch.object(self.cp,'_finish_run',side_effect=fail), self.assertRaises(ExecutionBlocked):
            self.call()
        self.assertEqual(self.cp.history(100), before)
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'],'running')

    def test_sql_migration_decision_blocks(self):
        def add(conn):
            conn.execute(
                "INSERT INTO migration_decisions "
                "(id,sha,plan_fingerprint,required_migration_count,state,approved_by,approval_reason,approved_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                ('approval',A,'f'*64,1,'approved','operator','reviewed','2026-10-15T00:00:00Z'),
            )
        self.cp._transaction(add)
        with self.assertRaises(ExecutionBlocked):
            self.call()
        self.assertEqual(self.cp.status(verbose=True)['runs'][0]['status'],'running')

    def test_terminal_without_matching_attestation_blocks(self):
        self.cp.transition_run(self.run_id, 'failed', 'some_other_failure', {'code':'REJECTED'})
        with self.assertRaises(ExecutionBlocked):
            self.call()

    def test_conflicting_build_checkpoint_blocks(self):
        def change(conn):
            row = conn.execute(
                "SELECT sequence FROM deployment_events WHERE entity_id=? "
                "AND kind='executor_checkpoint'", (self.run_id,)
            ).fetchone()
            # Append-only history cannot be altered. An additional contradictory
            # event should be rejected without rewriting existing evidence.
            self.cp._append_event(conn, 'executor_checkpoint', 'deployment_run', self.run_id,
                                  {'stage':'activation','phase':'intent','sha':A})
        self.cp._transaction(change)
        with self.assertRaises(ExecutionBlocked):
            self.call()

    def test_simultaneous_closers_only_one_append(self):
        barrier = threading.Barrier(2)
        results = []
        def job():
            try:
                barrier.wait(timeout=5)
                results.append(self.call())
            except Exception as exc:
                results.append(type(exc).__name__)
        threads = [threading.Thread(target=job) for _ in range(2)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(x['created'] for x in results if isinstance(x,dict)),[False,True])
        self.assertEqual(sum(x['kind']==RECOVERY_KIND for x in self.cp.history(100)),1)


if __name__ == '__main__':
    unittest.main()
