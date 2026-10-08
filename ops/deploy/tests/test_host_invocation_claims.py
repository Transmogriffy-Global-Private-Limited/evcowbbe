"""Regression: a previously claimed privileged operation is never replayable."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch, Mock

from ops.deploy.control_plane import ControlPlane, ExecutionBlocked
from ops.deploy.executor.host.operations import VPS2Operations
from ops.deploy.executor.host import contract as c

A, OLD, FINGERPRINT = 'a' * 40, 'c' * 40, '1' * 64


class HostInvocationClaimTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.db = str(Path(temporary.name) / 'state.sqlite3')
        self.cp = ControlPlane(self.db)
        self.request = self.cp.submit_request({
            'event': 'push', 'branch': 'prod', 'sha': A,
            'actor': 'source-ci', 'source_id': 'host-invocation:1',
        })['request']
        self.run = self.cp.create_run(self.request['id'], A, 'trusted-executor')
        self.id = self.run['id']
        self.cp.claim_run_execution(self.id, A, 'trusted-executor')
        self.cp.checkpoint_run(self.id, 'build', 'intent', 'trusted-executor', sha=A)
        self.cp.checkpoint_run(self.id, 'build', 'observed', 'trusted-executor', sha=A)

    def claim(self, stage, sha=A):
        return self.cp.claim_host_invocation(self.id, stage, sha, 'privileged-host-bridge')

    def test_migration_is_single_use_after_durable_boundary(self):
        decision = self.cp.approve_migration(A, FINGERPRINT, 'operator', 'approved', 1)['decision']
        self.cp.claim_migration_boundary(self.id, decision['id'], FINGERPRINT, A, 'trusted-executor')
        self.assertTrue(self.claim('migration')['claimed'])
        with self.assertRaises(ExecutionBlocked):
            self.claim('migration')
        self.assertEqual(self.count('migration'), 1)
        self.assertEqual(self.cp.migration_decisions()[0]['state'], 'executing')

    def test_pause_after_boundary_blocks_invocation_transaction(self):
        decision = self.cp.approve_migration(A, FINGERPRINT, 'operator', 'approved', 1)['decision']
        self.cp.claim_migration_boundary(self.id, decision['id'], FINGERPRINT, A, 'trusted-executor')
        self.cp.pause('operator', 'stop before sql')
        with self.assertRaises(ExecutionBlocked):
            self.claim('migration')
        self.assertEqual(self.count('migration'), 0)

    def test_activation_requires_intent_and_is_single_use(self):
        with self.assertRaises(ExecutionBlocked):
            self.claim('activation')
        self.cp.checkpoint_run(self.id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.assertTrue(self.claim('activation')['claimed'])
        with self.assertRaises(ExecutionBlocked):
            self.claim('activation')
        self.assertEqual(self.count('activation'), 1)

    def test_rollbacks_are_not_replayable_even_after_observed(self):
        self.cp.checkpoint_run(self.id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'activation', 'observed', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'runtime_verification', 'intent', 'trusted-executor', sha=A)
        self.cp.set_rollback_hold(OLD, 'operator', 'runtime unhealthy')
        self.cp.checkpoint_run(self.id, 'rollback', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.assertTrue(self.claim('rollback', OLD)['claimed'])
        with self.assertRaises(ExecutionBlocked):
            self.claim('rollback', OLD)
        self.cp.checkpoint_run(self.id, 'rollback', 'observed', 'trusted-executor', sha=A, previous_sha=OLD)
        with self.assertRaises(ExecutionBlocked):
            self.claim('rollback', OLD)
        self.assertEqual(self.count('rollback'), 1)

    def test_rollback_not_blocked_by_pause_but_requires_hold(self):
        self.cp.checkpoint_run(self.id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'activation', 'observed', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'runtime_verification', 'intent', 'trusted-executor', sha=A)
        self.cp.set_rollback_hold(OLD, 'operator', 'runtime unhealthy')
        self.cp.checkpoint_run(self.id, 'rollback', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.pause('operator', 'keep forward work paused')
        self.assertTrue(self.claim('rollback', OLD)['claimed'])
        self.assertEqual(self.count('rollback'), 1)

    def test_concurrent_migration_claim_has_exactly_one_winner(self):
        decision = self.cp.approve_migration(A, FINGERPRINT, 'operator', 'approved', 1)['decision']
        self.cp.claim_migration_boundary(self.id, decision['id'], FINGERPRINT, A, 'trusted-executor')
        def compete(_):
            try:
                ControlPlane(self.db).claim_host_invocation(
                    self.id, 'migration', A, 'privileged-host-bridge')
                return True
            except ExecutionBlocked:
                return False
        with ThreadPoolExecutor(max_workers=6) as executor:
            winners = list(executor.map(compete, range(6)))
        self.assertEqual(winners.count(True), 1)
        self.assertEqual(self.count('migration'), 1)

    def test_root_migration_dispatch_cannot_reexecute_after_claim(self):
        decision = self.cp.approve_migration(A, FINGERPRINT, 'operator', 'approved', 1)['decision']
        self.cp.claim_migration_boundary(self.id, decision['id'], FINGERPRINT, A, 'trusted-executor')
        ops = VPS2Operations()
        migrator = Mock(return_value=b'')
        with (patch('ops.deploy.executor.host.operations.ControlPlane', return_value=self.cp),
              patch.object(ops, '_github_authorize'), patch.object(ops, '_builder_head'),
              patch.object(ops, '_migrator', migrator), patch.object(c, 'verify_release', return_value=True)):
            self.assertTrue(ops.migrate(A))
            with self.assertRaises(ExecutionBlocked):
                ops.migrate(A)
        self.assertEqual(migrator.call_count, 1)
        self.assertEqual(self.count('migration'), 1)

    def test_root_activation_dispatch_cannot_repeat_restart(self):
        self.cp.checkpoint_run(self.id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        ops = VPS2Operations()
        switch = Mock(return_value=None)
        with (patch('ops.deploy.executor.host.operations.ControlPlane', return_value=self.cp),
              patch.object(ops, '_github_authorize'), patch.object(ops, '_builder_head'),
              patch.object(ops, '_switch', switch), patch.object(c, 'verify_release', return_value=True),
              patch.object(c, 'active_sha', return_value=OLD)):
            self.assertTrue(ops.activate(A))
            with self.assertRaises(ExecutionBlocked):
                ops.activate(A)
        self.assertEqual(switch.call_count, 1)
        self.assertEqual(self.count('activation'), 1)

    def test_root_rollback_dispatch_cannot_repeat_restart(self):
        self.cp.checkpoint_run(self.id, 'activation', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'activation', 'observed', 'trusted-executor', sha=A, previous_sha=OLD)
        self.cp.checkpoint_run(self.id, 'runtime_verification', 'intent', 'trusted-executor', sha=A)
        self.cp.set_rollback_hold(OLD, 'operator', 'runtime unhealthy')
        self.cp.checkpoint_run(self.id, 'rollback', 'intent', 'trusted-executor', sha=A, previous_sha=OLD)
        ops = VPS2Operations()
        switch = Mock(return_value=None)
        with (patch('ops.deploy.executor.host.operations.ControlPlane', return_value=self.cp),
              patch.object(ops, '_migrator', return_value=b''),
              patch.object(ops, '_switch', switch), patch.object(c, 'verify_release', return_value=True),
              patch.object(c, 'active_sha', return_value=A)):
            self.assertTrue(ops.rollback(OLD))
            with self.assertRaises(ExecutionBlocked):
                ops.rollback(OLD)
        self.assertEqual(switch.call_count, 1)
        self.assertEqual(self.count('rollback'), 1)

    def count(self, stage):
        return sum(event['kind'] == 'host_invocation_claimed' and event['details']['stage'] == stage
                   for event in self.cp.history(limit=200))


if __name__ == '__main__':
    unittest.main()
